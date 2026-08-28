# dtypes.py - 数据类
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Awaitable
from enum import Enum
from datetime import datetime

@dataclass
class AdditionalMount:
    """Additional mount configuration"""
    hostPath: str
    containerPath: Optional[str] = None
    readonly: bool = True

@dataclass
class AllowedRoot:
    """Allowed root directory for mounts"""
    path: str
    allowReadWrite: bool
    description: Optional[str] = None

@dataclass
class MountAllowlist:
    """Mount allowlist configuration"""
    allowedRoots: List[AllowedRoot]
    blockedPatterns: List[str]
    nonMainReadOnly: bool

@dataclass
class ContainerConfig:
    """Container configuration"""
    additionalMounts: List[AdditionalMount] = field(default_factory=list)
    timeout: Optional[int] = None

@dataclass
class RegisteredGroup:
    """Registered group information"""
    name: str
    folder: str
    trigger: str
    added_at: str
    containerConfig: Optional[ContainerConfig] = None
    requiresTrigger: Optional[bool] = None
    preferred_channel: Optional[str] = None
    allowed_channels: Optional[List[str]] = None

@dataclass
class MessageAttachment:
    """消息附件"""
    filename: str
    content_base64: str  # base64 编码的内容
    file_size: Optional[int] = None
    mime_type: Optional[str] = None

@dataclass
class NewMessage:
    """Incoming message"""
    id: str
    chat_id: str
    sender_id: str
    sender_name: str
    content: str
    timestamp: str
    is_from_me: bool = False
    is_bot_message: bool = False
    attachments: Optional[List[MessageAttachment]] = None  # 新增

class ScheduleType(str, Enum):
    CRON = 'cron'
    INTERVAL = 'interval'
    ONCE = 'once'

class TaskStatus(str, Enum):
    ACTIVE = 'active'
    PAUSED = 'paused'
    COMPLETED = 'completed'

@dataclass
class ScheduledTask:
    """Scheduled task"""
    id: str
    group_folder: str
    chat_id: str
    prompt: str
    schedule_type: ScheduleType
    schedule_value: str
    context_mode: str  # 'group' or 'isolated'
    next_run: Optional[str]
    last_run: Optional[str] = None
    last_result: Optional[str] = None
    status: TaskStatus = TaskStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class TaskRunLog:
    """Task run log"""
    task_id: str
    run_at: str
    duration_ms: int
    status: str  # 'success' or 'error'
    result: Optional[str]
    error: Optional[str]

class Channel:
    """Channel interface"""
    
    @property
    def name(self) -> str:
        raise NotImplementedError
    
    async def connect(self) -> None:
        raise NotImplementedError
    
    async def send_message(self, jid: str, text: str) -> None:
        raise NotImplementedError
    
    def is_connected(self) -> bool:
        raise NotImplementedError
    
    def owns_jid(self, jid: str) -> bool:
        raise NotImplementedError
    
    async def disconnect(self) -> None:
        raise NotImplementedError
    
    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Optional typing indicator"""
        pass

# Callback types
OnInboundMessage = Callable[[str, NewMessage], Awaitable[None]]
OnChatMetadata = Callable[[str, str, Optional[str], Optional[str], Optional[bool]], Awaitable[None]]