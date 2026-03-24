# agents/tools/base.py
"""Base classes for plugin-based tools."""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
import importlib
import pkgutil
import inspect
from pathlib import Path


@dataclass
class ToolMetadata:
    """Tool metadata"""
    name: str
    description: str
    parameters: Dict[str, Any]
    required_permissions: List[str] = field(default_factory=list)
    category: str = "general"
    version: str = "1.0.0"
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class ToolContext:
    """Tool execution context"""
    agent_name: str
    group_folder: str
    chat_id: str
    is_main: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    permissions: List[str] = field(default_factory=list)
    workspace_dir: Optional[Path] = None
    ipc_client: Optional[Any] = None
    memory_client: Optional[Any] = None


class ToolResult:
    """Tool execution result"""
    
    def __init__(self, success: bool, data: Any = None, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error
        }
    
    @classmethod
    def ok(cls, data: Any = None) -> 'ToolResult':
        """Create success result"""
        return cls(True, data)
    
    @classmethod
    def fail(cls, error: str, data: Any = None) -> 'ToolResult':
        """Create failure result"""
        return cls(False, data, error)


class BaseTool(ABC):
    """Base class for all tools"""
    
    def __init__(self, context: Optional[ToolContext] = None):
        self.context = context
        self._metadata: Optional[ToolMetadata] = None
    
    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata:
        """Get tool metadata"""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool"""
        pass
    
    def validate_permissions(self) -> bool:
        """Check if current context has required permissions"""
        if not self.context:
            return False
        
        required = self.metadata.required_permissions
        if not required:
            return True
        
        user_permissions = set(self.context.permissions)
        return all(perm in user_permissions for perm in required)
    
    def get_openai_function_spec(self) -> Dict:
        """Get OpenAI function calling specification"""
        return {
            "type": "function",
            "function": {
                "name": self.metadata.name,
                "description": self.metadata.description,
                "parameters": {
                    "type": "object",
                    "properties": self.metadata.parameters,
                    "required": [
                        name for name, spec in self.metadata.parameters.items()
                        if spec.get("required", False)
                    ]
                }
            }
        }
    
    async def __call__(self, **kwargs) -> ToolResult:
        """Make tool callable"""
        if not self.validate_permissions():
            return ToolResult.fail(f"Missing required permissions: {self.metadata.required_permissions}")
        return await self.execute(**kwargs)


class ToolPlugin(ABC):
    """Base class for tool plugins (collections of tools)"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._initialized = False
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Get plugin name"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """Get plugin version"""
        pass
    
    @abstractmethod
    async def initialize(self, context: ToolContext) -> None:
        """Initialize the plugin"""
        pass
    
    async def cleanup(self) -> None:
        """Cleanup plugin resources"""
        pass
    
    def register_tool(self, tool: BaseTool) -> None:
        """Register a tool"""
        self._tools[tool.metadata.name] = tool
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get tool by name"""
        return self._tools.get(name)
    
    def list_tools(self) -> List[ToolMetadata]:
        """List all tools in plugin"""
        return [tool.metadata for tool in self._tools.values()]
    
    def get_openai_functions(self) -> List[Dict]:
        """Get OpenAI function specifications for all tools"""
        return [tool.get_openai_function_spec() for tool in self._tools.values()]