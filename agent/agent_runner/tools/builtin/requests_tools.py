# agents/tools/builtin/requests_tools.py
"""HTTP requests tools for agent to fetch information from the internet."""

import json
import asyncio
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientTimeout, ClientError

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class HTTPGetTool(BaseTool):
    """HTTP GET request tool"""
    
    def __init__(self, context: ToolContext):
        super().__init__(context)
        self.timeout = ClientTimeout(total=30)
        self.max_response_size = 1024 * 1024  # 1MB max response size
        self.allowed_domains = []  # Empty means no restriction
        self.blocked_domains = []  # Can be configured
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="http_get",
            description="Make an HTTP GET request to fetch information from a URL",
            parameters={
                "url": {
                    "type": "string",
                    "description": "URL to fetch (must start with http:// or https://)",
                    "required": True
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers to send",
                    "required": False
                },
                "params": {
                    "type": "object",
                    "description": "Optional query parameters",
                    "required": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                    "required": False,
                    "default": 30
                },
                "max_size": {
                    "type": "integer",
                    "description": "Maximum response size in bytes (default: 1MB)",
                    "required": False,
                    "default": 1048576
                }
            },
            category="web",
            tags=["http", "web", "request", "fetch"],
            version="1.0.0"
        )
    
    def _validate_url(self, url: str) -> bool:
        """Validate URL for security"""
        try:
            parsed = urlparse(url)
            
            # Must be http or https
            if parsed.scheme not in ['http', 'https']:
                return False
            
            # Check for localhost/internal IPs (security)
            hostname = parsed.hostname or ""
            if hostname in ['localhost', '127.0.0.1', '::1']:
                return False
            
            # Check for internal IP ranges
            if hostname.startswith('192.168.') or hostname.startswith('10.'):
                return False
            
            # Domain restrictions if configured
            if self.allowed_domains:
                if not any(hostname.endswith(domain) for domain in self.allowed_domains):
                    return False
            
            if self.blocked_domains:
                if any(hostname.endswith(domain) for domain in self.blocked_domains):
                    return False
            
            return True
            
        except Exception:
            return False
    
    async def execute(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        max_size: int = 1048576
    ) -> ToolResult:
        """Execute HTTP GET request"""
        
        # Validate URL
        if not self._validate_url(url):
            return ToolResult.fail(f"Invalid or blocked URL: {url}")
        
        # Set timeout
        request_timeout = ClientTimeout(total=timeout)
        max_response_size = min(max_size, self.max_response_size)
        
        # Default headers
        default_headers = {
            'User-Agent': 'NanoClaw-Agent/1.0',
            'Accept': 'text/html,application/json,application/xml,*/*'
        }
        
        if headers:
            default_headers.update(headers)
        
        try:
            async with aiohttp.ClientSession(timeout=request_timeout) as session:
                async with session.get(url, headers=default_headers, params=params) as response:
                    
                    # Check status
                    if response.status >= 400:
                        error_text = await response.text()
                        return ToolResult.fail(
                            f"HTTP {response.status}: {response.reason}\n{error_text[:200]}"
                        )
                    
                    # Check content type
                    content_type = response.headers.get('Content-Type', '')
                    
                    # Read response with size limit
                    content = await response.read()
                    
                    if len(content) > max_response_size:
                        return ToolResult.fail(
                            f"Response too large: {len(content)} bytes (max: {max_response_size})"
                        )
                    
                    # Parse based on content type
                    result = {
                        "url": str(response.url),
                        "status": response.status,
                        "headers": dict(response.headers),
                        "content_type": content_type,
                        "size": len(content)
                    }
                    
                    # Try to decode based on content type
                    if 'application/json' in content_type:
                        try:
                            result["data"] = json.loads(content)
                            result["format"] = "json"
                        except:
                            result["text"] = content.decode('utf-8', errors='replace')
                            result["format"] = "text"
                    
                    elif 'text/html' in content_type:
                        result["html"] = content.decode('utf-8', errors='replace')[:5000]  # First 5000 chars
                        result["format"] = "html"
                        result["size"] = len(content)
                    
                    elif 'text/plain' in content_type:
                        result["text"] = content.decode('utf-8', errors='replace')
                        result["format"] = "text"
                    
                    else:
                        # Binary or unknown, return as text
                        result["text"] = content.decode('utf-8', errors='replace')[:5000]
                        result["format"] = "binary"
                    
                    return ToolResult.ok(result)
                    
        except asyncio.TimeoutError:
            return ToolResult.fail(f"Request timeout after {timeout} seconds")
        except ClientError as e:
            return ToolResult.fail(f"HTTP client error: {e}")
        except Exception as e:
            return ToolResult.fail(f"Request failed: {e}")


class HTTPPostTool(BaseTool):
    """HTTP POST request tool"""
    
    def __init__(self, context: ToolContext):
        super().__init__(context)
        self.timeout = ClientTimeout(total=30)
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="http_post",
            description="Make an HTTP POST request to send data to a URL",
            parameters={
                "url": {
                    "type": "string",
                    "description": "URL to send request to",
                    "required": True
                },
                "data": {
                    "type": "object",
                    "description": "Data to send (will be JSON encoded)",
                    "required": True
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers",
                    "required": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30
                }
            },
            category="web",
            tags=["http", "web", "post", "api"],
            version="1.0.0"
        )
    
    async def execute(
        self,
        url: str,
        data: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30
    ) -> ToolResult:
        """Execute HTTP POST request"""
        
        # Default headers
        default_headers = {
            'User-Agent': 'NanoClaw-Agent/1.0',
            'Content-Type': 'application/json'
        }
        
        if headers:
            default_headers.update(headers)
        
        try:
            async with aiohttp.ClientSession(timeout=ClientTimeout(total=timeout)) as session:
                async with session.post(url, json=data, headers=default_headers) as response:
                    
                    # Read response
                    content = await response.text()
                    
                    result = {
                        "url": str(response.url),
                        "status": response.status,
                        "headers": dict(response.headers),
                        "content": content[:5000],  # First 5000 chars
                        "size": len(content)
                    }
                    
                    # Try to parse as JSON
                    if 'application/json' in response.headers.get('Content-Type', ''):
                        try:
                            result["json"] = json.loads(content)
                        except:
                            pass
                    
                    if response.status >= 400:
                        return ToolResult.fail(
                            f"HTTP {response.status}: {response.reason}\n{content[:200]}",
                            data=result
                        )
                    
                    return ToolResult.ok(result)
                    
        except asyncio.TimeoutError:
            return ToolResult.fail(f"Request timeout after {timeout} seconds")
        except Exception as e:
            return ToolResult.fail(f"Request failed: {e}")


class DownloadFileTool(BaseTool):
    """Tool to download files from URLs"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="download_file",
            description="Download a file from a URL and save to workspace",
            parameters={
                "url": {
                    "type": "string",
                    "description": "URL of the file to download",
                    "required": True
                },
                "filename": {
                    "type": "string",
                    "description": "Name to save the file as",
                    "required": True
                },
                "max_size": {
                    "type": "integer",
                    "description": "Maximum file size in bytes",
                    "default": 10485760  # 10MB
                }
            },
            category="web",
            tags=["download", "file", "http"],
            version="1.0.0"
        )
    
    async def execute(
        self,
        url: str,
        filename: str,
        max_size: int = 10485760
    ) -> ToolResult:
        """Execute file download"""
        
        # Security: prevent path traversal
        if '..' in filename or filename.startswith('/'):
            return ToolResult.fail("Invalid filename")
        
        save_path = self.context.workspace_dir / 'downloads' / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    
                    if response.status >= 400:
                        return ToolResult.fail(f"HTTP {response.status}: {response.reason}")
                    
                    # Check content length
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > max_size:
                        return ToolResult.fail(
                            f"File too large: {content_length} bytes (max: {max_size})"
                        )
                    
                    # Download with size limit
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        content.extend(chunk)
                        if len(content) > max_size:
                            return ToolResult.fail(f"Download exceeded size limit of {max_size} bytes")
                    
                    # Save file
                    save_path.write_bytes(content)
                    
                    return ToolResult.ok({
                        "url": url,
                        "filename": filename,
                        "size": len(content),
                        "path": str(save_path),
                        "content_type": response.headers.get('Content-Type')
                    })
                    
        except Exception as e:
            return ToolResult.fail(f"Download failed: {e}")


class WebSearchTool(BaseTool):
    """Simple web search using external API (can be configured)"""
    
    def __init__(self, context: ToolContext, search_api_url: Optional[str] = None):
        super().__init__(context)
        self.search_api_url = search_api_url or "https://api.duckduckgo.com/"
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web_search",
            description="Search the web for information",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Search query",
                    "required": True
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5
                }
            },
            category="web",
            tags=["search", "web", "google"],
            version="1.0.0"
        )
    
    async def execute(self, query: str, num_results: int = 5) -> ToolResult:
        """Execute web search"""
        
        # This is a simple example using DuckDuckGo API
        # In production, you might want to use Google Custom Search, Bing API, etc.
        
        params = {
            'q': query,
            'format': 'json',
            'no_html': 1,
            'skip_disambig': 1
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.search_api_url, params=params) as response:
                    
                    if response.status != 200:
                        return ToolResult.fail(f"Search API error: {response.status}")
                    
                    data = await response.json()
                    
                    # Parse DuckDuckGo response
                    results = []
                    
                    # Abstract
                    if data.get('Abstract'):
                        results.append({
                            'title': 'Summary',
                            'snippet': data['Abstract'],
                            'url': data.get('AbstractURL', '')
                        })
                    
                    # Related topics
                    for topic in data.get('RelatedTopics', [])[:num_results]:
                        if isinstance(topic, dict):
                            results.append({
                                'title': topic.get('Text', '')[:100],
                                'snippet': topic.get('Text', ''),
                                'url': topic.get('FirstURL', '')
                            })
                    
                    return ToolResult.ok({
                        'query': query,
                        'results': results,
                        'count': len(results)
                    })
                    
        except Exception as e:
            return ToolResult.fail(f"Web search failed: {e}")


class RequestsToolsPlugin(ToolPlugin):
    """Plugin providing HTTP request tools"""
    
    def __init__(self):
        super().__init__()
     
    @property
    def name(self):
        return "requests"

    @property
    def version(self):
        return "1.0.0"
    

    async def initialize(self, context: ToolContext) -> None:
        """Initialize requests tools plugin"""
        self.register_tool(HTTPGetTool(context))
        self.register_tool(HTTPPostTool(context))
        self.register_tool(DownloadFileTool(context))
        
        # Optional: Add web search if configured
        # self.register_tool(WebSearchTool(context))
        
        logger.info(f"Requests tools plugin initialized with {len(self._tools)} tools")
