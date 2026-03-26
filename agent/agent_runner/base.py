# agents/base.py
"""Base classes and interfaces for the agent system."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Callable, Awaitable, Union
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import asyncio
import uuid

# Import from models to avoid circular import
from .models import AgentMessage, AgentResponse
from .database import ConversationDatabase, get_default_database, init_default_database
from .logger import logger


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
    db: Optional[ConversationDatabase] = None
    auto_init_db: bool = True  # Whether to auto-initialize database
    auto_create_session: bool = True  # Whether to auto-create session
    
    def __post_init__(self):
        if self.channel_info is None:
            self.channel_info = {}
        if self.available_channels is None:
            self.available_channels = []
        self.workspace_dir = Path(self.workspace_dir)
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Auto-initialize database
        if self.auto_init_db:
            self._init_database()
        
        # Auto-create session if session_id is provided
        if self.auto_create_session and self.session_id and self.db:
            self._ensure_session_exists()
    
    def _init_database(self):
        """Initialize database connection"""
        if self.db is None:
            # Use default database path in data directory
            db_path = self.data_dir / "conversations.db"
            self.db = ConversationDatabase(db_path, auto_init=True)
        elif hasattr(self.db, 'ensure_initialized'):
            # Ensure existing database is initialized
            self.db.ensure_initialized()
    
    def _ensure_session_exists(self):
        """Ensure session exists in database"""
        if self.db and self.session_id:
            self.db.get_or_create_session(
                session_id=self.session_id,
                group_folder=self.group_folder,
                chat_id=self.chat_id,
                assistant_name=self.assistant_name,
                is_main=self.is_main
            )
    
    def generate_session_id(self) -> str:
        """Generate a new session ID"""
        self.session_id = f"{self.group_folder}_{uuid.uuid4().hex[:16]}"
        if self.auto_create_session and self.db:
            self._ensure_session_exists()
        return self.session_id


class Agent(ABC):
    """Base agent class with automatic database persistence"""
    
    def __init__(
        self,
        name: str,
        context: Optional[AgentContext] = None,
        max_history: int = 100,
        persist_history: bool = True,
        auto_load_history: bool = True
    ):
        self.name = name
        self.context = context or AgentContext()
        self.max_history = max_history
        self.persist_history = persist_history
        self.auto_load_history = auto_load_history
        self.history: List[AgentMessage] = []
        self._running = False
        self._processing = False
        self._last_activity: Optional[datetime] = None
        self._metadata: Dict[str, Any] = {}
        
        # Auto-initialize database and load history
        self._auto_initialize()
    
    def _auto_initialize(self):
        """Automatically initialize database and load history"""
        # Ensure database is initialized
        if self.context.auto_init_db and self.context.db is None:
            self.context._init_database()
        
        # Auto-create session if needed
        if self.context.auto_create_session and self.context.session_id and self.context.db:
            self.context._ensure_session_exists()
        
        # Auto-load history from database
        if (self.persist_history and self.auto_load_history and 
            self.context.session_id and self.context.db):
            self._load_history_from_db()
    
    def _load_history_from_db(self, limit: Optional[int] = None) -> None:
        """Load conversation history from database"""
        if not self.context.db:
            return
        
        try:
            messages = self.context.db.get_messages(
                self.context.session_id,
                limit=limit or self.max_history
            )
            self.history = messages
        except Exception as e:
            # Log error but don't crash
            logger.debug(f"Warning: Failed to load history from database: {e}")
    
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
        
        # Add system message if not already present
        #if not any(msg.role == "system" for msg in self.history):
        #    system_msg = AgentMessage(
        #        role="system",
        #        content=f"You are {self.name}, a helpful assistant."
        #    )
        #    self.add_to_history(system_msg)
    
    @abstractmethod
    async def process_messages(self, messages: List[str], **kwargs) -> AgentResponse:
        """Process multiple messages"""
        pass
    
    async def process_message(self, message: str, **kwargs) -> AgentResponse:
        """Process a single message"""
        if '<p>/new</p>' in message:
            self.clear_history()
 
            return AgentResponse(
                content='Started new session.',
            )

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
        """Add message to history and persist to database"""
        self.history.append(message)
        self._last_activity = datetime.now()
        
        logger.debug(f"persist_history: {self.persist_history}, session_id: {self.context.session_id}, db: {self.context.db}")
        # Persist to database
        if self.persist_history and self.context.session_id and self.context.db:
            try:
                self.context.db.save_message(self.context.session_id, message)
            except Exception as e:
                logger.debug(f"Warning: Failed to save message to database: {e}")
        
        # Trim history if needed
        if len(self.history) > self.max_history:
            # Keep system messages
            system_msgs = [m for m in self.history if m.role == "system"]
            other_msgs = [m for m in self.history if m.role != "system"]
            self.history = system_msgs + other_msgs[-(self.max_history - len(system_msgs)):]
    
    def add_messages_to_history(self, messages: List[AgentMessage]) -> None:
        """Add multiple messages to history"""
        for message in messages:
            self.add_to_history(message)
    
    def clear_history(self, keep_system: bool = True, persist_delete: bool = True) -> None:
        """
        Clear conversation history
        
        Args:
            keep_system: Whether to keep system messages
            persist_delete: Whether to delete from database
        """
        if persist_delete and self.context.session_id and self.context.db:
            try:
                # Delete messages from database
                self.context.db.delete_messages(
                    self.context.session_id,
                    role=None if not keep_system else "system"
                )
            except Exception as e:
                logger.debug(f"Warning: Failed to delete messages from database: {e}")
        
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
    
    async def load_history_from_db(self, limit: Optional[int] = None) -> None:
        """Reload history from database"""
        if self.context.db:
            self._load_history_from_db(limit)
    
    async def sync_history_to_db(self) -> None:
        """Synchronize current history to database"""
        if not (self.persist_history and self.context.session_id and self.context.db):
            return
        
        try:
            # Clear existing messages in database
            self.context.db.delete_messages(self.context.session_id)
            # Save all messages
            self.context.db.save_messages(self.context.session_id, self.history)
        except Exception as e:
            logger.debug(f"Warning: Failed to sync history to database: {e}")
    
    async def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics for current session"""
        if self.context.session_id and self.context.db:
            try:
                return self.context.db.get_session_stats(self.context.session_id)
            except Exception as e:
                return {"error": str(e)}
        return {"error": "No session ID or database available"}
    
    async def export_session(self, format: str = "json") -> Dict[str, Any]:
        """Export current session"""
        if self.context.session_id and self.context.db:
            try:
                return self.context.db.export_session(self.context.session_id, format)
            except Exception as e:
                return {"error": str(e)}
        return {"error": "No session ID or database available"}
    
    def get_status(self) -> Dict[str, Any]:
        """Get agent status"""
        return {
            "name": self.name,
            "running": self._running,
            "processing": self._processing,
            "history_length": len(self.history),
            "last_activity": self._last_activity.isoformat() if self._last_activity else None,
            "metadata": self._metadata,
            "persist_history": self.persist_history,
            "auto_load_history": self.auto_load_history,
            "context": {
                "session_id": self.context.session_id,
                "group_folder": self.context.group_folder,
                "chat_id": self.context.chat_id,
                "is_main": self.context.is_main,
                "assistant_name": self.context.assistant_name,
                "auto_init_db": self.context.auto_init_db,
                "auto_create_session": self.context.auto_create_session
            }
        }


class AgentFactory:
    """Factory for creating agents"""
    
    _agents: Dict[str, type] = {}
    _default_db_path: Optional[Path] = None
    
    @classmethod
    def set_default_db_path(cls, db_path: Union[str, Path]) -> None:
        """Set default database path for all agents"""
        cls._default_db_path = Path(db_path)
        init_default_database(cls._default_db_path)
    
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
        
        # Use default database if context doesn't have one
        if context is None:
            context = AgentContext()
        
        if context.db is None and cls._default_db_path:
            context.db = get_default_database()
        
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
