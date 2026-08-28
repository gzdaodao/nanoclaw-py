# channels/base.py
"""Base channel interface with standardized message handling."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, Any, List, Awaitable
from datetime import datetime
from dataclasses import dataclass
import asyncio


@dataclass
class InboundMessage:
    """标准化入站消息格式"""
    id: str
    chat_id: str  # 通道内的聊天ID
    chat_name: Optional[str]  # 聊天名称（群名或用户名）
    sender_id: str
    sender_name: str
    content: str
    timestamp: datetime
    is_from_me: bool = False
    is_group: bool = False
    raw_data: Optional[Dict[str, Any]] = None
    is_bot_message: bool = False
    files: Optional[Dict[str, Any]] = None #base64 encode fields


class Channel(ABC):
    """Abstract base class for message channels."""

    def __init__(
        self,
        on_message: Callable[[str, InboundMessage], Awaitable[None]],  # 改为异步
        on_chat_metadata: Callable[[str, str, Optional[str], Optional[str], Optional[bool]], Awaitable[None]],  # 改为异步
        name: str,
        registered_groups: Optional[Callable] = None
    ):
        """
        Args:
            on_message: 消息回调 (channel_name, message) - 异步函数
            on_chat_metadata: 元数据回调 (chat_id, timestamp, name, channel, is_group) - 异步函数
            name: 通道名称
        """
        self.on_message = on_message
        self.on_chat_metadata = on_chat_metadata
        self._name = name
        self._connected = False
        self._message_queue: List[InboundMessage] = []
        self.registered_groups = registered_groups

    @property
    def name(self) -> str:
        """Get channel name."""
        return self._name

    @property
    def is_connected(self) -> bool:
        """Check if channel is connected."""
        return self._connected

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the channel."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the channel."""
        pass

    @abstractmethod
    def owns_jid(self, jid: str) -> bool:
        """
        Check if this channel owns the given JID.
        
        JID格式: {channel}:{chat_id}
        例如: whatsapp:1234567890@g.us
              telegram:-100123456789
              signal:recipient123
        """
        pass

    @abstractmethod
    async def send_message(self, jid: str, text: str) -> bool:
        """Send a message to the given JID. Returns success status."""
        pass

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator (optional)."""
        pass

    async def mark_as_read(self, jid: str, message_id: str) -> None:
        """Mark message as read (optional)."""
        pass

    def get_chat_id_from_jid(self, jid: str) -> str:
        """Extract chat ID from JID."""
        if not jid.startswith(f"{self.name}:"):
            raise ValueError(f"JID {jid} does not belong to channel {self.name}")
        return jid[len(self.name) + 1:]

    def create_jid(self, chat_id: str) -> str:
        """Create JID from chat ID."""
        return f"{self.name}:{chat_id}"

    async def _process_inbound_message(self, msg: InboundMessage) -> None:
        """Process inbound message (callbacks and queue)."""
        # 创建JID
        
        #jid = self.create_jid(msg.chat_id)
        jid = msg.chat_id
        
        try:
            # 通知元数据 - 使用 await
            await self.on_chat_metadata(
                jid,
                msg.timestamp.isoformat(),
                msg.chat_name,
                self.name,
                msg.is_group
            )
            
            # 调用消息回调 - 使用 await
            await self.on_message(jid, msg)
            
        except Exception as e:
            # 错误处理，避免单个消息处理失败影响其他消息
            import logging
            logging.error(f"Error processing message in {self.name}: {e}")
        
        # 添加到队列（用于重试等）
        self._message_queue.append(msg)
