# channels/telegram.py
"""Telegram channel implementation using python-telegram-bot."""

from datetime import datetime
from typing import Optional, Dict, Any, Callable
from loguru import logger
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from .base import Channel, InboundMessage
from .. import config


class TelegramChannel(Channel):
    """Telegram bot channel."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "telegram",
        registered_groups=None,
        token: Optional[str] = None,
        message_limit: int = 4096,
        proxy: Optional[str] = None
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.message_limit = message_limit
        self.proxy = proxy
        
        self.app: Optional[Application] = None
        self._bot_info: Optional[Dict[str, Any]] = None

    async def connect(self) -> None:
        """Connect to Telegram Bot API."""
        try:
            if not self.token:
                raise ValueError("Telegram bot token is required")
            
            # 创建应用
            builder = Application.builder().token(self.token)
            
            if self.proxy:
                builder.proxy(self.proxy)
            
            self.app = builder.build()
            
            # 添加消息处理器
            self.app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )
            self.app.add_handler(
                MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._handle_new_chat)
            )
            
            # 启动
            await self.app.initialize()
            await self.app.start()
            
            # 开始轮询
            await self.app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES
            )
            
            # 获取机器人信息
            self._bot_info = await self.app.bot.get_me()
            
            self._connected = True
            logger.info(f"Telegram bot connected: @{self._bot_info['username']}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Telegram."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        
        self._connected = False
        logger.info("Telegram disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to Telegram."""
        return jid.startswith(f"{self.name}:")

    async def send_message(self, jid: str, text: str) -> bool:
        """Send a message via Telegram."""
        if not self._connected or not self.app:
            logger.error("Telegram not connected")
            return False
        
        try:
            chat_id = self.get_chat_id_from_jid(jid)
            
            # 处理消息长度限制
            if len(text) > self.message_limit:
                # 分块发送
                for i in range(0, len(text), self.message_limit):
                    chunk = text[i:i + self.message_limit]
                    await self._send_chunk(chat_id, chunk)
            else:
                await self._send_chunk(chat_id, text)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator."""
        if not self._connected or not self.app:
            return
        
        try:
            chat_id = self.get_chat_id_from_jid(jid)
            if is_typing:
                await self.app.bot.send_chat_action(
                    chat_id=int(chat_id),
                    action="typing"
                )
        except Exception as e:
            logger.debug(f"Failed to set typing: {e}")

    async def _send_chunk(self, chat_id: str, text: str) -> None:
        """Send a message chunk."""
        await self.app.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        logger.debug(f"Message chunk sent to {chat_id}")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming message."""
        try:
            if not update.message or not update.message.text:
                return
            
            msg = update.message
            chat = msg.chat
            
            jid = self.create_jid(str(chat.id))
            # 创建标准消息
            inbound = InboundMessage(
                id=str(msg.message_id),
                chat_id=jid,
                chat_name=chat.title or f"{chat.first_name} {chat.last_name or ''}".strip(),
                sender_id=str(msg.from_user.id),
                sender_name=msg.from_user.full_name or msg.from_user.username or str(msg.from_user.id),
                content=msg.text,
                timestamp=msg.date.replace(tzinfo=None),
                is_from_me=msg.from_user.is_bot,
                is_group=chat.type in ['group', 'supergroup'],
                raw_data=msg.to_dict()
            )
            
            # 处理消息
            await self._process_inbound_message(inbound)
            
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}")

    async def _handle_new_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle new chat members."""
        try:
            if not update.message or not update.message.new_chat_members:
                return
            
            msg = update.message
            chat = msg.chat
            
            # 更新聊天元数据
            jid = self.create_jid(str(chat.id))
            await self.on_chat_metadata(
                jid,
                msg.date.isoformat(),
                chat.title,
                self.name,
                True  # is_group
            )
            
        except Exception as e:
            logger.error(f"Error handling new chat: {e}")
