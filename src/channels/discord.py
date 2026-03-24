# channels/discord.py
"""Discord channel implementation using discord.py."""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from loguru import logger
import discord

from .base import Channel, InboundMessage
from .. import config


class DiscordChannel(Channel):
    """Discord bot channel."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "discord",
        registered_groups=None,
        token: Optional[str] = None,
        message_limit: int = 2000
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        self.token = token or config.DISCORD_BOT_TOKEN
        self.message_limit = message_limit
        
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        self.client = discord.Client(intents=intents)
        self._ready = asyncio.Event()

    async def connect(self) -> None:
        """Connect to Discord."""
        try:
            if not self.token:
                raise ValueError("Discord bot token is required")
            
            # 设置事件处理器
            @self.client.event
            async def on_ready():
                logger.info(f"Discord bot connected: {self.client.user}")
                self._connected = True
                self._ready.set()
            
            @self.client.event
            async def on_message(message):
                if message.author == self.client.user:
                    return
                await self._handle_message(message)
            
            @self.client.event
            async def on_guild_join(guild):
                await self._handle_guild_join(guild)
            
            # 启动客户端
            asyncio.create_task(self.client.start(self.token))
            
            # 等待连接
            await asyncio.wait_for(self._ready.wait(), timeout=30)
            
        except Exception as e:
            logger.error(f"Failed to connect to Discord: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Discord."""
        if self.client:
            await self.client.close()
        self._connected = False
        logger.info("Discord disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to Discord."""
        return jid.startswith(f"{self.name}:")

    async def send_message(self, jid: str, text: str) -> bool:
        """Send message to Discord."""
        if not self._connected or not self.client:
            logger.error("Discord not connected")
            return False
        
        try:
            channel_id = self.get_chat_id_from_jid(jid)
            channel = self.client.get_channel(int(channel_id))
            
            if not channel:
                logger.error(f"Channel {channel_id} not found")
                return False
            
            # 处理消息长度限制
            if len(text) > self.message_limit:
                for i in range(0, len(text), self.message_limit):
                    chunk = text[i:i + self.message_limit]
                    await channel.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await channel.send(text)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")
            return False

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator."""
        if not self._connected:
            return
        
        try:
            channel_id = self.get_chat_id_from_jid(jid)
            channel = self.client.get_channel(int(channel_id))
            
            if channel and is_typing:
                async with channel.typing():
                    await asyncio.sleep(1)  # 短暂显示 typing
        except Exception as e:
            logger.debug(f"Failed to set typing: {e}")

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming message."""
        try:
            # 创建标准消息
            jid = self.create_jid(str(message.channel.id))
            inbound = InboundMessage(
                id=str(message.id),
                chat_id=str(jid),
                chat_name=message.channel.name,
                sender_id=str(message.author.id),
                sender_name=message.author.display_name,
                content=message.content,
                timestamp=message.created_at.replace(tzinfo=None),
                is_from_me=message.author == self.client.user,
                is_group=True,  # Discord channels are always groups
                raw_data={
                    'author': str(message.author),
                    'channel': message.channel.name,
                    'guild': message.guild.name if message.guild else None
                }
            )
            
            # 处理消息
            await self._process_inbound_message(inbound)
            
        except Exception as e:
            logger.error(f"Error handling Discord message: {e}")

    async def _handle_guild_join(self, guild: discord.Guild) -> None:
        """Handle joining a new guild."""
        try:
            # 更新元数据
            for channel in guild.text_channels:
                jid = self.create_jid(str(channel.id))
                await self.on_chat_metadata(
                    jid,
                    datetime.now().isoformat(),
                    f"{guild.name}#{channel.name}",
                    self.name,
                    True
                )
        except Exception as e:
            logger.error(f"Error handling guild join: {e}")
