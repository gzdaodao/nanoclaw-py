# agents/models.py
"""Data models for agent system."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any


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