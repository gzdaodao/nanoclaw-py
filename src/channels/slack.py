# channels/slack.py
"""Slack channel implementation using slack-sdk."""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Set, Callable
from loguru import logger
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest

from .base import Channel, InboundMessage
from .. import config


class SlackChannel(Channel):
    """Slack bot channel using Socket Mode."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "slack",
        registered_groups=None,
        app_token: Optional[str] = None,
        bot_token: Optional[str] = None,
        message_limit: int = 40000
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        self.app_token = app_token or config.SLACK_APP_TOKEN
        self.bot_token = bot_token or config.SLACK_BOT_TOKEN
        self.message_limit = message_limit
        
        self.web_client: Optional[AsyncWebClient] = None
        self.socket_client: Optional[SocketModeClient] = None
        self._known_channels: Set[str] = set()

    async def connect(self) -> None:
        """Connect to Slack."""
        try:
            if not self.app_token or not self.bot_token:
                raise ValueError("Slack app token and bot token are required")
            
            # 创建 Web 客户端
            self.web_client = AsyncWebClient(token=self.bot_token)
            
            # 创建 Socket Mode 客户端
            self.socket_client = SocketModeClient(
                app_token=self.app_token,
                web_client=self.web_client
            )
            
            # 设置消息处理器
            self.socket_client.socket_mode_request_listeners.append(
                self._handle_socket_request
            )
            
            # 连接
            await self.socket_client.connect()
            
            # 获取机器人信息
            auth_test = await self.web_client.auth_test()
            bot_info = await self.web_client.bot_info(bot=auth_test['bot_id'])
            
            self._connected = True
            logger.info(f"Slack bot connected: {bot_info['bot']['name']}")
            
            # 获取已知频道
            await self._load_channels()
            
        except Exception as e:
            logger.error(f"Failed to connect to Slack: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Slack."""
        if self.socket_client:
            await self.socket_client.close()
        if self.web_client:
            await self.web_client.close()
        
        self._connected = False
        logger.info("Slack disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to Slack."""
        return jid.startswith(f"{self.name}:")

    async def send_message(self, jid: str, text: str) -> bool:
        """Send message to Slack."""
        if not self._connected or not self.web_client:
            logger.error("Slack not connected")
            return False
        
        try:
            channel_id = self.get_chat_id_from_jid(jid)
            
            # 处理消息长度限制
            if len(text) > self.message_limit:
                # 创建消息块
                blocks = []
                for i in range(0, len(text), self.message_limit):
                    chunk = text[i:i + self.message_limit]
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": chunk}
                    })
                
                await self.web_client.chat_postMessage(
                    channel=channel_id,
                    blocks=blocks
                )
            else:
                await self.web_client.chat_postMessage(
                    channel=channel_id,
                    text=text,
                    mrkdwn=True
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Slack message: {e}")
            return False

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator."""
        if not self._connected or not self.web_client:
            return
        
        try:
            channel_id = self.get_chat_id_from_jid(jid)
            if is_typing:
                await self.web_client.conversations_typing(channel=channel_id)
        except Exception as e:
            logger.debug(f"Failed to set typing: {e}")

    async def _handle_socket_request(self, client: SocketModeClient, req: SocketModeRequest) -> None:
        """Handle incoming socket mode request."""
        if req.type == "events_api":
            # 确认事件
            await client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
            
            # 处理事件
            event = req.payload.get("event", {})
            await self._handle_event(event)

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Handle Slack event."""
        try:
            event_type = event.get("type")
            
            if event_type == "message" and "subtype" not in event:
                await self._handle_message(event)
            elif event_type == "channel_created":
                await self._handle_channel_created(event)
                
        except Exception as e:
            logger.error(f"Error handling Slack event: {e}")

    async def _handle_message(self, event: Dict[str, Any]) -> None:
        """Handle incoming message."""
        try:
            # 获取频道信息
            channel_id = event.get("channel")
            channel_info = await self.web_client.conversations_info(channel=channel_id)
            channel_name = channel_info["channel"]["name"]
            
            # 获取用户信息
            user_id = event.get("user")
            user_info = await self.web_client.users_info(user=user_id)
            user_name = user_info["user"]["real_name"] or user_info["user"]["name"]
            
            # 创建标准消息
            jid = self.create_jid(channel_id)
            inbound = InboundMessage(
                id=event.get("ts", "").replace(".", ""),
                chat_id=jid,
                chat_name=channel_name,
                sender_id=user_id,
                sender_name=user_name,
                content=event.get("text", ""),
                timestamp=datetime.fromtimestamp(float(event.get("ts", 0))),
                is_from_me=False,
                is_group=True,
                raw_data=event
            )
            
            # 处理消息
            await self._process_inbound_message(inbound)
            
        except Exception as e:
            logger.error(f"Error handling Slack message: {e}")

    async def _handle_channel_created(self, event: Dict[str, Any]) -> None:
        """Handle channel creation."""
        try:
            channel = event.get("channel", {})
            channel_id = channel.get("id")
            channel_name = channel.get("name")
            
            if channel_id and channel_name:
                jid = self.create_jid(channel_id)
                await self.on_chat_metadata(
                    jid,
                    datetime.now().isoformat(),
                    channel_name,
                    self.name,
                    True
                )
                
        except Exception as e:
            logger.error(f"Error handling channel creation: {e}")

    async def _load_channels(self) -> None:
        """Load list of accessible channels."""
        try:
            async for response in await self.web_client.conversations_list(
                types="public_channel,private_channel"
            ):
                for channel in response["channels"]:
                    self._known_channels.add(channel["id"])
                    
                    jid = self.create_jid(channel["id"])
                    self.on_chat_metadata(
                        jid,
                        datetime.now().isoformat(),
                        channel["name"],
                        self.name,
                        True
                    )
            
            logger.info(f"Loaded {len(self._known_channels)} Slack channels")
            
        except Exception as e:
            logger.error(f"Failed to load Slack channels: {e}")
