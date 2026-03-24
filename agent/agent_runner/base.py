# agents/base.py
"""Base classes and interfaces for the agent system."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Callable, Awaitable, Union
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import asyncio


@dataclass
class AgentContext:
    """Agent execution context"""
    session_id: str = ""
    group_folder: str = ""
    chat_id: str = ""
    is_main: bool = False
    assistant_name: str = "Assistant"
    channel_info: Optional[Dict[str, Any]] = None
    available_channels: Optional[List[Dict[str, Any]]] = None
    workspace_dir: Path = field(default_factory=lambda: Path('/workspace/group'))
    data_dir: Path = field(default_factory=lambda: Path('/workspace/group/.data'))
    
    def __post_init__(self):
        if self.channel_info is None:
            self.channel_info = {}
        if self.available_channels is None:
            self.available_channels = []
        self.workspace_dir = Path(self.workspace_dir)
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class AgentMessage:
    """Message in agent conversation"""
    role: str  # 'system', 'user', 'assistant', 'tool'
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API"""
        msg = {
            "role": self.role,
            "content": self.content
        }
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentMessage':
        """Create from dictionary"""
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name")
        )


@dataclass
class AgentResponse:
    """Agent response"""
    content: str = ""
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "content": self.content,
            "session_id": self.session_id,
            "metadata": self.metadata,
            "error": self.error,
            "tool_results": self.tool_results
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentResponse':
        """Create from dictionary"""
        return cls(
            content=data.get("content", ""),
            session_id=data.get("session_id"),
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            tool_results=data.get("tool_results", [])
        )
    
    def is_success(self) -> bool:
        """Check if response is successful"""
        return self.error is None


class Agent(ABC):
    """Base agent class"""
    
    def __init__(
        self,
        name: str,
        context: Optional[AgentContext] = None,
        max_history: int = 100
    ):
        self.name = name
        self.context = context or AgentContext()
        self.max_history = max_history
        self.history: List[AgentMessage] = []
        self._running = False
        self._processing = False
        self._last_activity: Optional[datetime] = None
        self._metadata: Dict[str, Any] = {}
    
    @property
    def is_running(self) -> bool:
        """Check if agent is running"""
        return self._running
    
    @property
    def is_processing(self) -> bool:
        """Check if agent is processing"""
        return self._processing
    
    @property
    def last_activity(self) -> Optional[datetime]:
        """Get last activity timestamp"""
        return self._last_activity
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the agent"""
        self._running = True
        self._last_activity = datetime.now()
    
    @abstractmethod
    async def process_messages(self, messages: List[str], **kwargs) -> AgentResponse:
        """Process multiple messages"""
        pass
    
    async def process_message(self, message: str, **kwargs) -> AgentResponse:
        """Process a single message"""
        return await self.process_messages([message], **kwargs)
    
    @abstractmethod
    async def stream_response(
        self,
        message: str,
        callback: Callable[[str], Awaitable[None]],
        **kwargs
    ) -> AgentResponse:
        """Stream response token by token"""
        pass
    
    async def stop(self) -> None:
        """Stop the agent"""
        self._running = False
        self._last_activity = datetime.now()
    
    def add_to_history(self, message: AgentMessage) -> None:
        """Add message to history"""
        self.history.append(message)
        self._last_activity = datetime.now()
        
        # Trim history if needed
        if len(self.history) > self.max_history:
            # Keep system messages
            system_msgs = [m for m in self.history if m.role == "system"]
            other_msgs = [m for m in self.history if m.role != "system"]
            self.history = system_msgs + other_msgs[-(self.max_history - len(system_msgs)):]
    
    def clear_history(self, keep_system: bool = True) -> None:
        """Clear conversation history"""
        if keep_system:
            self.history = [m for m in self.history if m.role == "system"]
        else:
            self.history = []
        self._last_activity = datetime.now()
    
    def get_recent_history(self, limit: Optional[int] = None) -> List[AgentMessage]:
        """Get recent history"""
        if limit is None:
            return self.history.copy()
        return self.history[-limit:]
    
    def get_status(self) -> Dict[str, Any]:
        """Get agent status"""
        return {
            "name": self.name,
            "running": self._running,
            "processing": self._processing,
            "history_length": len(self.history),
            "last_activity": self._last_activity.isoformat() if self._last_activity else None,
            "metadata": self._metadata,
            "context": {
                "group_folder": self.context.group_folder,
                "chat_id": self.context.chat_id,
                "is_main": self.context.is_main,
                "assistant_name": self.context.assistant_name
            }
        }


class AgentFactory:
    """Factory for creating agents"""
    
    _agents: Dict[str, type] = {}
    
    @classmethod
    def register(cls, name: str):
        """Decorator to register an agent type"""
        def decorator(agent_class: type) -> type:
            cls._agents[name] = agent_class
            return agent_class
        return decorator
    
    @classmethod
    def create(
        cls,
        agent_type: str,
        name: str,
        context: Optional[AgentContext] = None,
        **kwargs
    ) -> Agent:
        """Create an agent instance"""
        if agent_type not in cls._agents:
            raise ValueError(f"Unknown agent type: {agent_type}. Available: {list(cls._agents.keys())}")
        
        agent_class = cls._agents[agent_type]
        return agent_class(name=name, context=context, **kwargs)
    
    @classmethod
    def list_agents(cls) -> List[str]:
        """List all registered agent types"""
        return list(cls._agents.keys())
    
    @classmethod
    def get_agent_info(cls, agent_type: str) -> Dict[str, Any]:
        """Get information about an agent type"""
        if agent_type not in cls._agents:
            return {}
        
        agent_class = cls._agents[agent_type]
        return {
            "name": agent_type,
            "class": agent_class.__name__,
            "module": agent_class.__module__,
            "doc": agent_class.__doc__
        }