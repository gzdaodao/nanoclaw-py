# agents/tools/builtin/mcp_server_tools.py
"""McpServer management tools plugin."""

from pathlib import Path
from typing import Optional, List, Dict, Any
import uuid as uuid_lib

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger
from agent_runner.mcp_servers import get_mcp_server_loader, McpServer, delete_mcp_server, create_mcp_server


class GetMcpServerDetailTool(BaseTool):
    """Tool to get mcp_server details"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_mcp_server_detail",
            description="Get detailed information about a mcp_server including its README content",
            parameters={
                "identifier": {
                    "type": "string",
                    "description": "McpServer identifier (UUID or name)",
                    "required": True
                }
            },
            category="mcp_servers",
            required_permissions=["mcp_servers:read"],
            version="1.0.0",
            tags=["mcp_server", "read", "query"]
        )
    
    async def execute(self, identifier: str) -> ToolResult:
        """Execute get mcp_server detail"""
        try:
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            mcp_server_detail = loader.get_mcp_server_detail(identifier)
            
            if not mcp_server_detail:
                return ToolResult.fail(f"McpServer not found: {identifier}")
            
            return ToolResult.ok(mcp_server_detail)
            
        except Exception as e:
            logger.error(f"Failed to get mcp_server detail: {e}")
            return ToolResult.fail(f"Failed to get mcp_server detail: {e}")


class SearchMcpServersTool(BaseTool):
    """Tool to search mcp_servers"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="search_mcp_servers",
            description="Search for mcp_servers by name or description",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Search query string",
                    "required": True
                }
            },
            category="mcp_servers",
            required_permissions=["mcp_servers:read"],
            version="1.0.0",
            tags=["mcp_server", "search", "query"]
        )
    
    async def execute(self, query: str) -> ToolResult:
        """Execute search mcp_servers"""
        try:
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            results = loader.search_mcp_servers(query)
            
            return ToolResult.ok({
                "results": results,
                "count": len(results),
                "query": query
            })
            
        except Exception as e:
            logger.error(f"Failed to search mcp_servers: {e}")
            return ToolResult.fail(f"Failed to search mcp_servers: {e}")


class ListMcpServersTool(BaseTool):
    """Tool to list all mcp_servers"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_mcp_servers",
            description="List all available mcp_servers with their names and descriptions",
            parameters={
                "include_summary": {
                    "type": "boolean",
                    "description": "Include formatted summary text",
                    "default": False
                }
            },
            category="mcp_servers",
            required_permissions=["mcp_servers:read"],
            version="1.0.0",
            tags=["mcp_server", "list", "query"]
        )
    
    async def execute(self, include_summary: bool = False) -> ToolResult:
        """Execute list mcp_servers"""
        try:
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            mcp_servers = loader.get_all_mcp_servers()
            
            result = {
                "mcp_servers": mcp_servers,
                "count": len(mcp_servers)
            }
            
            if include_summary:
                result["summary"] = loader.get_mcp_servers_summary()
            
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to list mcp_servers: {e}")
            return ToolResult.fail(f"Failed to list mcp_servers: {e}")


class CreateMcpServerTool(BaseTool):
    """Tool to create a new mcp_server"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="create_mcp_server",
            description="Create a new mcp_server with the given name, description, and content",
            parameters={
                "name": {
                    "type": "string",
                    "description": "McpServer name (must be unique)",
                    "required": True
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the mcp_server",
                    "required": True
                },
                "url": {
                    "type": "string",
                    "description": "McpServer Url",
                    "required": True
                },
                "api_key": {
                    "type": "string",
                    "description": "API KEY for mcp_server",
                    "required": True
                },
                "api_key_header": {
                    "type": "string",
                    "description": "API KEY header to auth",
                    "required": False
                },

                "api_key_prefix": {
                    "type": "string",
                    "description": "API KEY prefix to auth",
                    "required": False
                },

                "timeout ": {
                    "type": "integer",
                    "description": "Connection timeout to MCP server",
                    "required": False
                },

                "max_retries": {
                    "type": "integer",
                    "description": "Max retries times when connect fail",
                    "required": False
                },

                "auto_load": {
                    "type": "Boolean",
                    "description": "Auto load the MCP server for every conversation",
                    "required": False
                },


            },
            category="mcp_servers",
            required_permissions=["mcp_servers:write"],
            version="1.0.0",
            tags=["mcp_server", "create", "write"]
        )
    
    async def execute(self, name, url,
            description,
            api_key,
            api_key_header="Authorization",
            api_key_prefix="Bearer",
            timeout=60,
            max_retries=3,
            auto_load=False) -> ToolResult:
        """Execute create mcp_server"""
        try:
            # 验证参数
            if not name or not name.strip():
                return ToolResult.fail("McpServer name cannot be empty")
            
            if not description or not description.strip():
                return ToolResult.fail("McpServer description cannot be empty")
 
            if not url or not url.strip():
                return ToolResult.fail("McpServer url cannot be empty")
            
            
            if not api_key or not api_key.strip():
                return ToolResult.fail("McpServer api_key cannot be empty")
            
            
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            # 检查名称是否已存在
            existing = loader.get_mcp_server(name)
            if existing:
                return ToolResult.fail(f"McpServer with name '{name}' already exists")
            
            # 创建MCP服务
            result = create_mcp_server(name.strip(), description.strip(), url.strip(),
                api_key=api_key.strip(),
                api_key_header=api_key_header.strip(),
                api_key_prefix=api_key_prefix.strip(),
                timeout=timeout,
                max_retries=max_retries,
                auto_load=auto_load)
            
            if not result:
                return ToolResult.fail(f"Failed to create mcp_server: {name}")
            
            logger.info(f"McpServer created successfully: {name} ({result['uuid']})")
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to create mcp_server: {e}")
            return ToolResult.fail(f"Failed to create mcp_server: {e}")


class DeleteMcpServerTool(BaseTool):
    """Tool to delete a mcp_server"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="delete_mcp_server",
            description="Delete a mcp_server by UUID or name",
            parameters={
                "identifier": {
                    "type": "string",
                    "description": "McpServer identifier (UUID or name)",
                    "required": True
                }

            },
            category="mcp_servers",
            required_permissions=["mcp_servers:write"],
            version="1.0.0",
            tags=["mcp_server", "delete", "remove"]
        )
    
    async def execute(self, identifier: str) -> ToolResult:
        """Execute delete mcp_server"""
        try:
            if not identifier or not identifier.strip():
                return ToolResult.fail("McpServer identifier cannot be empty")
            
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            # 调用封装好的删除方法
            result = delete_mcp_server(identifier.strip())
            
            if not result:
                return ToolResult.fail(f"McpServer not found: {identifier}")
            
            # 如果需要确认
            if result.get("requires_confirmation"):
                return ToolResult.fail(result.get("message", "Confirmation required"))
            
            # 删除成功
            logger.info(f"McpServer deleted: {result['name']} ({result['uuid']})")
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to delete mcp_server: {e}")
            return ToolResult.fail(f"Failed to delete mcp_server: {e}")


class LoadMcpServersTool(BaseTool):
    """Tool to load all mcp_server tools to tool list"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="load_mcp_tools",
            description="Load the McpServer tools to tool list",
            parameters={
                "identifier": {
                    "type": "string",
                    "description": "McpServer identifier (UUID or name)",
                    "required": True
                }
            },
            category="mcp_servers",
            required_permissions=["mcp_servers:read"],
            version="1.0.0",
            tags=["mcp_server", "list", "query"]
        )
    
    async def execute(self, identifier: str) -> ToolResult:
        """Execute get mcp_server detail"""
        try:
            loader = get_mcp_server_loader()
            
            # 确保MCP服务已加载
            if not loader._loaded:
                loader.load_all_mcp_servers()
            
            mcp_server = loader.get_mcp_server(identifier)
            agent = self.content.agent
            if not agent:
                return ToolResult.fail(f"Failed to load mcp_server tools with agent in tool context")
           
            #更新agent工具
            agent.tool_handlers.update(mcp_server.tool_handlers)
            
            if not mcp_server:
                return ToolResult.fail(f"McpServer not found: {identifier}")
            
            return ToolResult.ok(f"Loaded McpServer:{identifier} Tools!")
            
        except Exception as e:
            logger.error(f"Failed to load mcp_server tools: {e}")
            return ToolResult.fail(f"Failed to load mcp_server tools: {e}")




class McpServersPlugin(ToolPlugin):
    """Plugin providing mcp_server management tools"""
    
    def __init__(self):
        super().__init__()
        self.mcp_servers_root: Optional[Path] = None
    
    @property
    def name(self) -> str:
        return "mcp_servers"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    async def initialize(self, context: ToolContext) -> None:
        """Initialize mcp_servers plugin"""
        # 设置MCP服务根目录
        if context.workspace_dir:
            self.mcp_servers_root = context.workspace_dir / "mcp_servers"
        else:
            self.mcp_servers_root = Path("/workspace/group/mcp_servers")
        
        # 更新MCP服务加载器的根目录
        from agent_runner.mcp_servers import _mcp_server_loader
        if _mcp_server_loader:
            _mcp_server_loader.mcp_servers_root = self.mcp_servers_root
        
        # 注册所有MCP服务管理工具
        self.register_tool(GetMcpServerDetailTool(context))
        self.register_tool(SearchMcpServersTool(context))
        self.register_tool(ListMcpServersTool(context))
        self.register_tool(CreateMcpServerTool(context))
        self.register_tool(DeleteMcpServerTool(context))
        self.register_tool(LoadMcpServersTool(context))
        
        # 预加载MCP服务
        try:
            from agent_runner.mcp_servers import load_all_mcp_servers
            count = load_all_mcp_servers()
            logger.info(f"McpServers plugin initialized with {count} mcp_servers loaded")
        except Exception as e:
            logger.warning(f"Failed to pre-load mcp_servers: {e}")
        
        logger.info(f"McpServers plugin initialized with {len(self._tools)} tools")
    
    async def cleanup(self) -> None:
        """Cleanup plugin resources"""
        logger.info("McpServers plugin cleaned up")


