# agents/mcp_client.py
"""MCP Client with API Key support for external MCP services."""

import json
import asyncio
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
import aiohttp
from aiohttp import ClientTimeout, ClientError

from .logger import logger


class MCPClient:
    """MCP Client with API Key authentication"""
    
    def __init__(
        self,
        server_url: str,
        api_key: Optional[str] = None,
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer",
        timeout: int = 60,
        max_retries: int = 3
    ):
        """
        Initialize MCP Client
        
        Args:
            server_url: MCP server URL (e.g., https://mcp.example.com)
            api_key: API key for authentication (optional)
            api_key_header: Header name for API key (default: Authorization)
            api_key_prefix: Prefix for API key (default: Bearer)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
        """
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.api_key_header = api_key_header
        self.api_key_prefix = api_key_prefix
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_id = 0
        self._stats = {
            "requests": 0,
            "errors": 0,
            "tools_called": 0
        }
        
        logger.info(f"MCP Client initialized for {server_url}")
        if api_key:
            logger.info(f"API Key authentication enabled (header: {api_key_header})")
    
    async def __aenter__(self):
        timeout = ClientTimeout(total=self.timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session exists"""
        if self._session is None or self._session.closed:
            timeout = ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with API key if provided"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "NanoClaw-MCP-Client/1.0"
        }
        
        if self.api_key:
            if self.api_key_prefix:
                headers[self.api_key_header] = f"{self.api_key_prefix} {self.api_key}"
            else:
                headers[self.api_key_header] = self.api_key
        
        return headers
    
    async def _request(
        self, 
        method: str, 
        params: Optional[Dict] = None,
        endpoint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send JSON-RPC request to MCP server
        
        Args:
            method: JSON-RPC method name (e.g., 'tools/list')
            params: Method parameters
            endpoint: Specific endpoint (default: /mcp)
        """
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._request_id
        }
        
        # Determine endpoint
        endpoint_path = endpoint or "/mcp"
        url = f"{self.server_url}{endpoint_path}"
        
        headers = self._get_headers()
        session = await self._ensure_session()
        
        self._stats["requests"] += 1
        
        # Retry logic
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Request: {method} (attempt {attempt + 1}/{self.max_retries})")
                
                async with session.post(url, headers=headers, json=payload) as response:
                    # Handle authentication errors
                    if response.status == 401:
                        error_msg = "Authentication failed. Please check your API key."
                        logger.error(error_msg)
                        raise Exception(error_msg)
                    
                    if response.status == 403:
                        error_msg = "Access forbidden. Your API key may not have sufficient permissions."
                        logger.error(error_msg)
                        raise Exception(error_msg)
                    
                    # Handle rate limiting
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "5")
                        logger.warning(f"Rate limited. Retry after {retry_after}s")
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(int(retry_after))
                            continue
                        else:
                            raise Exception("Rate limit exceeded")
                    
                    # Try to parse response
                    try:
                        result = await response.json()
                    except json.JSONDecodeError:
                        text = await response.text()
                        logger.error(f"Invalid JSON response: {text[:200]}")
                        raise Exception(f"Invalid JSON response: {text[:200]}")
                    
                    # Check for JSON-RPC error
                    if "error" in result:
                        error_info = result["error"]
                        error_msg = error_info.get("message", "Unknown error")
                        error_code = error_info.get("code", 0)
                        logger.error(f"JSON-RPC error {error_code}: {error_msg}")
                        raise Exception(f"JSON-RPC error {error_code}: {error_msg}")
                    
                    # Check HTTP status
                    if response.status >= 400:
                        error_msg = result.get("error", {}).get("message", f"HTTP {response.status}")
                        logger.error(f"Request failed: {error_msg}")
                        raise Exception(error_msg)
                    
                    return result
                    
            except aiohttp.ClientResponseError as e:
                logger.error(f"HTTP error: {e.status} - {e.message}")
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise
                
            except (ClientError, aiohttp.ClientError) as e:
                logger.error(f"Connection error: {e}")
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise
                
            except asyncio.TimeoutError:
                logger.error(f"Request timeout after {self.timeout}s")
                last_exception = TimeoutError("Request timeout")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        
        raise last_exception or Exception("Request failed after retries")
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        Get list of available tools from MCP server
        
        Returns:
            List of tool definitions with name, description, and input schema
        """
        try:
            # Try standard MCP endpoint
            result = await self._request("tools/list")
            
            # Extract tools from response
            if result.get("result") and result["result"].get("tools"):
                tools = result["result"]["tools"]
                logger.info(f"Found {len(tools)} tools from MCP server")
                return tools
            
            # Alternative response format
            if result.get("tools"):
                tools = result["tools"]
                logger.info(f"Found {len(tools)} tools from MCP server")
                return tools
            
            # Try alternative method name
            result2 = await self._request("listTools")
            if result2.get("result") and result2["result"].get("tools"):
                tools = result2["result"]["tools"]
                logger.info(f"Found {len(tools)} tools from MCP server")
                return tools
            
            logger.warning(f"No tools found in response: {result}")
            return []
            
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            # Try to get tools via /tools endpoint as fallback
            try:
                session = await self._ensure_session()
                headers = self._get_headers()
                async with session.get(f"{self.server_url}/tools", headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        tools = data.get("tools", [])
                        logger.info(f"Tools loaded via /tools endpoint: {len(tools)}")
                        return tools
            except Exception as e2:
                logger.error(f"Failed to load tools via /tools: {e2}")
            
            return []
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call a tool on the MCP server
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as dict
            
        Returns:
            Tool execution result
        """
        try:
            self._stats["tools_called"] += 1
            logger.info(f"Calling tool: {tool_name} with args: {arguments}")
            
            result = await self._request("tools/call", {
                "name": tool_name,
                "arguments": arguments
            })
            
            # Extract result
            if result.get("result"):
                return result["result"]
            
            # Some servers return directly
            if "content" in result:
                return result
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to call tool {tool_name}: {e}")
            raise
    
    async def list_resources(self) -> List[Dict[str, Any]]:
        """Get list of available resources from MCP server"""
        try:
            result = await self._request("resources/list")
            if result.get("result") and result["result"].get("resources"):
                resources = result["result"]["resources"]
                logger.info(f"Found {len(resources)} resources from MCP server")
                return resources
            return []
        except Exception as e:
            logger.error(f"Failed to list resources: {e}")
            return []
    
    async def read_resource(self, uri: str) -> Any:
        """Read a resource from MCP server"""
        try:
            result = await self._request("resources/read", {
                "uri": uri
            })
            if result.get("result"):
                return result["result"]
            return result
        except Exception as e:
            logger.error(f"Failed to read resource {uri}: {e}")
            raise
    
    async def ping(self) -> bool:
        """Ping the MCP server to check connectivity"""
        try:
            result = await self._request("ping")
            return result.get("result") == "pong" or result.get("status") == "ok"
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False
    
    async def initialize(self) -> Dict[str, Any]:
        """Initialize connection with MCP server"""
        try:
            result = await self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "clientInfo": {
                    "name": "NanoClaw-Agent",
                    "version": "1.0.0"
                }
            })
            logger.info("MCP client initialized successfully")
            return result
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            raise
    
    async def close(self):
        """Close the client and cleanup resources"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("MCP client closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics"""
        return {
            **self._stats,
            "authenticated": bool(self.api_key),
            "server_url": self.server_url
        }


class MCPClientFactory:
    """Factory for creating and managing MCP clients"""
    
    _clients: Dict[str, MCPClient] = {}
    
    @classmethod
    async def get_or_create_client(
        cls,
        server_url: str,
        api_key: Optional[str] = None,
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer",
        force_new: bool = False
    ) -> MCPClient:
        """Get or create an MCP client instance"""
        key = f"{server_url}:{api_key_header}:{api_key_prefix}"
        
        if not force_new and key in cls._clients:
            client = cls._clients[key]
            if not client._session or client._session.closed:
                # Recreate if session is closed
                del cls._clients[key]
            else:
                return client
        
        client = MCPClient(
            server_url=server_url,
            api_key=api_key,
            api_key_header=api_key_header,
            api_key_prefix=api_key_prefix
        )
        await client.__aenter__()
        
        # Try to initialize connection
        try:
            await client.initialize()
        except Exception as e:
            logger.warning(f"Initialization failed (will retry later): {e}")
        
        cls._clients[key] = client
        return client
    
    @classmethod
    async def close_all(cls):
        """Close all clients"""
        for client in cls._clients.values():
            await client.close()
        cls._clients.clear()