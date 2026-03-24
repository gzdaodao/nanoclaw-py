# channels/__init__.py
"""Message channels for Nanoclaw-Python."""

from .base import Channel, InboundMessage
from .factory import ChannelFactory

# 导出主要类
__all__ = ["Channel", "InboundMessage", "ChannelFactory"]

# 版本信息
__version__ = "1.0.0"
