# agents/mcp_servers/loader.py
"""极简分布式MCP服务加载器 - 每个MCP服务独立索引文件"""

import json
import shutil
import uuid as uuid_lib
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Union, Awaitable
from dataclasses import dataclass
from datetime import datetime
from .mcp_client import MCPClient, MCPClientFactory
from .tools.base import BaseTool, ToolPlugin, ToolContext, ToolResult, ToolMetadata

from .logger import logger


class MCPTool(BaseTool):
    def __init__(self, server, tool_spec, context: Optional[ToolContext] = None):
        super(MCPTool, self).__init__(context=context)
        self.server = server
        self._tool_spec = tool_spec
        self.name = 'MCP_{}_{}'.format(server.name, tool_spec.get('name'))
        self.description = tool_spec.get('description', f'MCP tool: {self.name}')
    
        # 获取 inputSchema 并转换为 OpenAI 格式
        input_schema = tool_spec.get('inputSchema', {})
        self.parameters = self._convert_schema_to_openai(input_schema)


    def _convert_schema_to_openai(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 MCP 的 JSON Schema 转换为 OpenAI function 参数格式
        
        MCP 格式:
        {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
                "param2": {"type": "number", "description": "..."}
            },
            "required": ["param1"]
        }
        
        OpenAI 格式 (相同结构):
        {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
                "param2": {"type": "number", "description": "..."}
            },
            "required": ["param1"]
        }
        """
        if not schema:
            return {"type": "object", "properties": {}, "required": []}
        
        # 直接使用 schema，确保必要字段存在
        return {
            "type": schema.get("type", "object"),
            "properties": schema.get("properties", {}),
            "required": schema.get("required", [])
        }


    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            category="mcp_servers",
            required_permissions=["mcp:call"],
            version="1.0.0",
            tags=["mcp",]
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        """Execute tools"""
        try:
            client = await self._connect()
            
            # 调用 MCP 工具
            logger.info(f"Calling MCP tool: {self.name} with args: {kwargs}")
            
            result = await client.call_tool(self.name, kwargs)
            
            # 返回标准 ToolResult
            if isinstance(result, dict):
                if result.get('success') is False:
                    return ToolResult.fail(result.get('error', 'MCP tool execution failed'))
                return ToolResult.ok(result.get('data', result))
            elif result is not None:
                return ToolResult.ok(result)
            else:
                return ToolResult.ok({"status": "success"})
                
        except Exception as e:
            logger.error(f"MCP tool {self.name} execution error: {e}")
            return ToolResult.fail(str(e))
 

   
       
class McpServer:
    def __init__(self, uuid, name, url, path,
            description,
            api_key=None,
            api_key_header="Authorization",
            api_key_prefix="Bearer",
            timeout=60,
            max_retries=3,
            auto_load=False):
        
        self.uuid = uuid
        self.name = name
        self.url = url
        self.path = path
        self.description = description
        self.api_key = api_key
        self.api_key_header = api_key_header
        self.api_key_prefix = api_key_prefix
        self.timeout = timeout
        self.max_retries = max_retries
        self.auto_load = auto_load
        self.tool_handlers = {}
        # 不在这里调用 _load_tools
        
    async def initialize(self):
        """异步初始化方法"""
        await self._load_tools()
        return self
    
    async def _connect(self):
        client = await MCPClientFactory.get_or_create_client(
            server_url=self.url,
            api_key=self.api_key,
            api_key_header=self.api_key_header,
            api_key_prefix=self.api_key_prefix
        )
        return client  # 注意：原代码缺少 return

    async def _load_tools(self):
        client = await self._connect()
        self.tool_handlers = {}
               
        tools = await client.list_tools()
        logger.info(f"Found {len(tools)} MCP tools from {self.name}")
  
        for tool_spec in tools:
            tool_name = tool_spec.get('name')
            if not tool_name:
                continue
                       
            self.tool_handlers[tool_name] = MCPTool(self, tool_spec)
            logger.info(f"Registered MCP tool: {tool_name} from {self.name}")

    @classmethod
    async def create(cls, **kwargs):
        """异步工厂方法"""
        instance = cls(**kwargs)
        await instance.initialize()
        return instance

class McpServerLoader:
    """极简MCP服务加载器 - 每个MCP服务独立索引"""
    
    def __init__(self, mcp_servers_root: Path = Path("/workspace/group/mcp_servers")):
        self.mcp_servers_root = mcp_servers_root
        self.mcp_servers: Dict[str, McpServer] = {}  # uuid -> McpServer
        self.mcp_servers_by_name: Dict[str, McpServer] = {}  # name -> McpServer
        self._loaded = False
        self._load_time: Optional[datetime] = None
    
    async def load_all_mcp_servers(self) -> int:
        """加载所有MCP服务 - 扫描每个子目录的 config.json"""
        if not self.mcp_servers_root.exists():
            logger.warning(f"McpServers directory not found: {self.mcp_servers_root}")
            return 0
        
        self.mcp_servers.clear()
        self.mcp_servers_by_name.clear()
        
        count = 0
        for mcp_server_dir in self.mcp_servers_root.iterdir():
            if not mcp_server_dir.is_dir():
                continue
            
            # 读取 config.json
            config_file = mcp_server_dir / "config.json"
            if not config_file.exists():
                logger.debug(f"No config.json in {mcp_server_dir}, skipping")
                continue
            
            try:
                data = json.loads(config_file.read_text(encoding='utf-8'))
                data.update({
                    'path': mcp_server_dir,
                    })
                
                # 验证必要字段
                if not all(k in data for k in ["uuid", "name", "description"]):
                    logger.warning(f"Invalid config.json in {mcp_server_dir}: missing required fields")
                    continue
                
                mcp_server = await McpServer.create(**data)
                
                self.mcp_servers[mcp_server.uuid] = mcp_server
                self.mcp_servers_by_name[mcp_server.name] = mcp_server
                count += 1
                
                logger.debug(f"Loaded mcp_server: {mcp_server.name} ({mcp_server.uuid})")
                
            except Exception as e:
                logger.error(f"Failed to load mcp_server from {mcp_server_dir}: {e}")
        
        self._loaded = True
        self._load_time = datetime.now()
        logger.info(f"Loaded {count} mcp_servers from {self.mcp_servers_root}")
        return count
    
    def create_mcp_server(self, **kwargs) -> Optional[Dict[str, Any]]:
        """
        创建新MCP服务
        
        Args:
            name: MCP服务名称
            description: MCP服务简短描述
            content: MCP服务详细内容 (mcp_server.md)
            
        Returns:
            创建成功的MCP服务信息字典，失败返回 None
        """
        # 检查MCP服务名称是否已存在
        name = kwargs['name']
        if name in self.mcp_servers_by_name:
            logger.warning(f"McpServer with name '{name}' already exists")
            return None
        
        # 生成 UUID
        mcp_server_uuid = str(uuid_lib.uuid4())
        
        # 创建MCP服务目录（使用 UUID 作为目录名）
        mcp_server_dir = self.mcp_servers_root / mcp_server_uuid
        try:
            mcp_server_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            logger.error(f"McpServer directory already exists: {mcp_server_dir}")
            return None
        except Exception as e:
            logger.error(f"Failed to create mcp_server directory: {e}")
            return None
        
        # 写入 config.json
        config_data = {
            "uuid": mcp_server_uuid,
            "name": name,
            "created_at": datetime.now().isoformat()
        }
        config_data.update(kwargs)
        
        config_file = mcp_server_dir / "config.json"
        try:
            config_file.write_text(
                json.dumps(config_data, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error(f"Failed to write config.json: {e}")
            # 清理已创建的目录
            try:
                mcp_server_dir.rmdir()
            except Exception:
                pass
            return None
               
        # 创建 McpServer 对象并加入缓存
        config_data.update({
            "path": mcp_server_dir,
            })
        mcp_server = McpServer(
            **config_data
        )
        
        self.mcp_servers[mcp_server_uuid] = mcp_server
        self.mcp_servers_by_name[name] = mcp_server
        
        logger.info(f"Created mcp_server: {name} ({mcp_server_uuid})")
        
        return config_data 
    
    def delete_mcp_server(self, identifier: str) -> Optional[Dict[str, Any]]:
        """
        删除MCP服务
        
        Args:
            identifier: MCP服务标识符（UUID 或名称）
            
        Returns:
            删除成功的MCP服务信息字典，失败返回 None
        """
        # 获取MCP服务
        mcp_server = self.get_mcp_server(identifier)
        if not mcp_server:
            logger.warning(f"McpServer not found: {identifier}")
            return None
               
        try:
            # 保存MCP服务信息以便返回
            mcp_server_info = {
                "uuid": mcp_server.uuid,
                "name": mcp_server.name,
                "description": mcp_server.description,
                "path": str(mcp_server.path),
                "deleted": False
            }
            
            # 删除MCP服务目录及其内容
            if mcp_server.path.exists():
                shutil.rmtree(mcp_server.path)
                logger.info(f"Deleted mcp_server directory: {mcp_server.path}")
                mcp_server_info["deleted"] = True
            else:
                logger.warning(f"McpServer directory does not exist: {mcp_server.path}")
                mcp_server_info["deleted"] = False
                mcp_server_info["warning"] = "Directory did not exist"
            
            # 从缓存中移除
            self.mcp_servers.pop(mcp_server.uuid, None)
            self.mcp_servers_by_name.pop(mcp_server.name, None)
            
            logger.info(f"McpServer deleted successfully: {mcp_server.name} ({mcp_server.uuid})")
            return mcp_server_info
            
        except Exception as e:
            logger.error(f"Failed to delete mcp_server '{mcp_server.name}': {e}")
            return None
    
    def get_mcp_server(self, identifier: str) -> Optional[McpServer]:
        """通过 UUID 或名称获取MCP服务"""
        # 先按 UUID 查找
        if identifier in self.mcp_servers:
            return self.mcp_servers[identifier]
        
        # 再按名称查找
        if identifier in self.mcp_servers_by_name:
            return self.mcp_servers_by_name[identifier]
        
        return None
   
    
    def search_mcp_servers(self, query: str) -> List[Dict[str, Any]]:
        """搜索MCP服务 - 简单文本匹配"""
        query = query.lower()
        results = []
        
        for mcp_server in self.mcp_servers.values():
            score = 0
            
            # 名称匹配
            if query in mcp_server.name.lower():
                score += 10
            
            # 描述匹配
            if query in mcp_server.description.lower():
                score += 5
            
            if score > 0:
                results.append({
                    "uuid": mcp_server.uuid,
                    "name": mcp_server.name,
                    "description": mcp_server.description,
                    "relevance": score
                })
        
        # 按相关度排序
        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results
    
    def get_all_mcp_servers(self) -> List[Dict[str, str]]:
        """获取所有MCP服务列表"""
        return [
            {
                "uuid": s.uuid,
                "name": s.name,
                "description": s.description
            }
            for s in self.mcp_servers.values()
        ]
    
    def get_mcp_servers_summary(self) -> str:
        """生成MCP服务摘要文本（用于系统提示词）"""
        if not self.mcp_servers:
            return "No mcp_servers available."
        
        lines = ["## Available McpServers:"]
        for mcp_server in self.mcp_servers.values():
            lines.append(f"- **{mcp_server.name}**: {mcp_server.description}")
        
        return "\n".join(lines)
    
    def get_mcp_server_detail(self, identifier: str) -> Optional[Dict[str, Any]]:
        """获取MCP服务详细信息（包含 README）"""
        mcp_server = self.get_mcp_server(identifier)
        if not mcp_server:
            return None
        
        return {
            "uuid": mcp_server.uuid,
            "name": mcp_server.name,
            "description": mcp_server.description,
            "path": str(mcp_server.path),
            "url": str(mcp_server.url),
        }


# 全局单例
_mcp_server_loader: Optional[McpServerLoader] = None


def get_mcp_server_loader() -> McpServerLoader:
    """获取全局MCP服务加载器单例"""
    global _mcp_server_loader
    if _mcp_server_loader is None:
        _mcp_server_loader = McpServerLoader()
    return _mcp_server_loader


async def load_all_mcp_servers() -> int:
    """加载所有MCP服务"""
    return await get_mcp_server_loader().load_all_mcp_servers()


def get_mcp_server(identifier: str) -> Optional[McpServer]:
    """获取MCP服务"""
    return get_mcp_server_loader().get_mcp_server(identifier)


def get_mcp_server_detail(identifier: str) -> Optional[Dict]:
    """获取MCP服务详细信息"""
    return get_mcp_server_loader().get_mcp_server_detail(identifier)


def search_mcp_servers(query: str) -> List[Dict]:
    """搜索MCP服务"""
    return get_mcp_server_loader().search_mcp_servers(query)


def get_mcp_servers_summary() -> str:
    """获取MCP服务摘要"""
    return get_mcp_server_loader().get_mcp_servers_summary()


def get_all_mcp_servers() -> List[Dict]:
    """获取所有MCP服务列表"""
    return get_mcp_server_loader().get_all_mcp_servers()


def create_mcp_server(**kwargs) -> Optional[Dict]:
    """
    创建新MCP服务（便捷函数）
        
    Returns:
        创建成功的MCP服务信息字典，失败返回 None
    """
    return get_mcp_server_loader().create_mcp_server(**kwargs)


def delete_mcp_server(identifier: str) -> Optional[Dict]:
    """
    删除MCP服务（便捷函数）
    
    Args:
        identifier: MCP服务标识符（UUID 或名称）
        force: 是否强制删除
        
    Returns:
        删除成功的MCP服务信息字典，失败返回 None
    """
    return get_mcp_server_loader().delete_mcp_server(identifier)
