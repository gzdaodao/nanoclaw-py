# channels/whatsapp.py
"""WhatsApp channel implementation using Playwright."""

import asyncio
import json
import base64
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from loguru import logger
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from .base import Channel, InboundMessage
from .. import config


class WhatsAppChannel(Channel):
    """WhatsApp Web channel using Playwright."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "whatsapp",
        registered_groups=None,
        session_dir: Optional[str] = None,
        headless: bool = False,
        message_limit: int = 4096,
        proxy: Optional[str] = None
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        self.session_dir = Path(session_dir or config.WHATSAPP_SESSION_DIR)
        self.headless = headless
        self.message_limit = message_limit
        self.proxy = proxy
        
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        self._message_handler_task: Optional[asyncio.Task] = None
        self._running = False
        self._known_chats: Dict[str, str] = {}  # chat_id -> name

    async def connect(self) -> None:
        """Connect to WhatsApp Web."""
        try:
            logger.info(f"Starting WhatsApp Web connection (headless: {self.headless})...")
            
            # 确保会话目录存在
            self.session_dir.mkdir(parents=True, exist_ok=True)
            
            # 启动 Playwright
            self.playwright = await async_playwright().start()
            
            # 配置浏览器启动参数
            launch_args = ['--no-sandbox', '--disable-dev-shm-usage']
            if self.proxy:
                launch_args.append(f'--proxy-server={self.proxy}')
            
            # 启动浏览器
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=launch_args
            )
            
            # 加载已保存的会话
            storage_state = None
            storage_file = self.session_dir / "storage.json"
            if storage_file.exists():
                with open(storage_file, 'r') as f:
                    storage_state = json.load(f)
                logger.info("Loaded existing WhatsApp session")
            
            # 创建上下文
            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 800},
                storage_state=storage_state,
                locale='en-US'
            )
            
            self.page = await self.context.new_page()
            
            # 设置消息监听
            await self._setup_message_listener()
            
            # 导航到 WhatsApp Web
            await self.page.goto('https://web.whatsapp.com', wait_until='domcontentloaded')
            
            # 检查登录状态
            await self._wait_for_login()
            
            # 保存会话状态
            await self._save_session()
            
            # 获取已知聊天
            await self._load_known_chats()
            
            self._connected = True
            self._running = True
            logger.info("WhatsApp Web connected successfully")
            
            # 启动消息处理循环
            self._message_handler_task = asyncio.create_task(self._message_loop())
            
        except Exception as e:
            logger.error(f"Failed to connect to WhatsApp: {e}")
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from WhatsApp."""
        self._running = False
        self._connected = False
        
        if self._message_handler_task:
            self._message_handler_task.cancel()
            try:
                await self._message_handler_task
            except asyncio.CancelledError:
                pass
        
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        
        logger.info("WhatsApp disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to WhatsApp."""
        return jid.startswith(f"{self.name}:") and (
            jid.endswith("@g.us") or jid.endswith("@s.whatsapp.net")
        )

    async def send_message(self, jid: str, text: str) -> bool:
        """Send message to WhatsApp chat."""
        if not self._connected or not self.page:
            logger.error("WhatsApp not connected")
            return False
        
        try:
            chat_id = self.get_chat_id_from_jid(jid)
            
            # 打开聊天
            await self._open_chat(chat_id)
            
            # 输入消息
            input_box = self.page.locator('div[contenteditable="true"][data-tab="10"]')
            await input_box.click()
            
            # 分段发送长消息
            if len(text) > self.message_limit:
                for i in range(0, len(text), self.message_limit):
                    chunk = text[i:i + self.message_limit]
                    await input_box.fill(chunk)
                    await self.page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)
            else:
                await input_box.fill(text)
                await self.page.keyboard.press("Enter")
            
            logger.debug(f"Message sent to {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            return False

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        """Set typing indicator."""
        if not self._connected or not self.page:
            return
        
        try:
            chat_id = self.get_chat_id_from_jid(jid)
            await self._open_chat(chat_id)
            
            # WhatsApp 没有直接的 typing API，但我们可以模拟
            # 这里通过发送输入事件来触发 typing 指示器
            input_box = self.page.locator('div[contenteditable="true"][data-tab="10"]')
            await input_box.click()
            
            if is_typing:
                # 发送一个输入事件
                await input_box.type(' ', delay=100)
                # 立即删除
                await self.page.keyboard.press("Backspace")
            
        except Exception as e:
            logger.debug(f"Failed to set typing indicator: {e}")

    async def _wait_for_login(self) -> None:
        """Wait for user to scan QR code and login."""
        # 检查是否已登录
        try:
            await self.page.wait_for_selector('div[data-testid="chat-list"]', timeout=5000)
            logger.info("Already logged in!")
            return
        except:
            pass
        
        logger.info("Please scan the QR code with WhatsApp to login")
        
        # 等待 QR 码出现
        qr_selector = 'div[data-testid="qrcode"] canvas'
        try:
            await self.page.wait_for_selector(qr_selector, timeout=10000)
            
            # 保存 QR 码
            qr_file = self.session_dir / "qr_code.png"
            await self.page.locator(qr_selector).screenshot(path=str(qr_file))
            logger.info(f"QR code saved to {qr_file}")
            
            # 尝试获取 QR 码文本（用于终端显示）
            qr_data = await self.page.evaluate("""
                () => {
                    const canvas = document.querySelector('canvas');
                    if (!canvas) return null;
                    
                    // 尝试从 alt 文本获取
                    const parent = canvas.closest('[data-ref]');
                    if (parent) return parent.getAttribute('data-ref');
                    
                    return canvas.toDataURL();
                }
            """)
            
            if qr_data:
                if qr_data.startswith('data:image'):
                    logger.info(f"QR Code data URL: {qr_data[:100]}...")
                else:
                    logger.info(f"QR Code ref: {qr_data}")
                    
        except Exception as e:
            logger.warning(f"Could not capture QR code: {e}")
        
        # 等待登录成功
        try:
            await self.page.wait_for_selector('div[data-testid="chat-list"]', timeout=60000)
            logger.info("Login successful!")
        except Exception as e:
            logger.error("Login timeout")
            raise

    async def _save_session(self) -> None:
        """Save browser session state."""
        storage_file = self.session_dir / "storage.json"
        storage = await self.context.storage_state()
        with open(storage_file, 'w') as f:
            json.dump(storage, f, indent=2)
        logger.debug(f"Session saved to {storage_file}")

    async def _setup_message_listener(self) -> None:
        """Setup JavaScript message listener."""
        await self.page.evaluate("""
            // 存储消息的数组
            window.nanoclawMessages = [];
            
            // 监听新消息
            const observer = new MutationObserver((mutations) => {
                const messages = document.querySelectorAll('[data-testid="msg-container"]');
                messages.forEach(msg => {
                    if (!msg.dataset.nanoclawProcessed) {
                        msg.dataset.nanoclawProcessed = 'true';
                        
                        try {
                            // 提取消息信息
                            const content = msg.querySelector('[data-testid="msg-body"]')?.textContent || '';
                            const sender = msg.querySelector('[data-testid="msg-author"]')?.textContent || '';
                            const time = msg.querySelector('time')?.getAttribute('datetime') || new Date().toISOString();
                            const isFromMe = msg.querySelector('[data-testid="msg-doublecheck"]') !== null;
                            
                            window.nanoclawMessages.push({
                                id: msg.id || `msg-${Date.now()}-${Math.random()}`,
                                content: content,
                                sender: sender,
                                time: time,
                                isFromMe: isFromMe
                            });
                        } catch (e) {
                            console.error('Error parsing message:', e);
                        }
                    }
                });
            });
            
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
        """)

    async def _message_loop(self) -> None:
        """Main message processing loop."""
        while self._running:
            try:
                # 从页面获取新消息
                messages = await self.page.evaluate("""
                    () => {
                        const msgs = [...window.nanoclawMessages];
                        window.nanoclawMessages = [];
                        return msgs;
                    }
                """)
                
                for msg_data in messages:
                    await self._process_message(msg_data)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in message loop: {e}")
                await asyncio.sleep(5)

    async def _process_message(self, msg_data: Dict[str, Any]) -> None:
        """Process a single message."""
        try:
            # 获取当前聊天
            chat_info = await self._get_current_chat()
            if not chat_info:
                return
            
            chat_id, chat_name = chat_info
            
            jid = self.create_jid(chat_id)
            # 创建标准消息
            msg = InboundMessage(
                id=msg_data['id'],
                chat_id=jid,
                chat_name=chat_name,
                sender_id=msg_data['sender'],
                sender_name=msg_data['sender'] or 'Unknown',
                content=msg_data['content'],
                timestamp=datetime.fromisoformat(msg_data['time'].replace('Z', '+00:00')),
                is_from_me=msg_data['isFromMe'],
                is_group=True,  # WhatsApp Web 主要是群组
                raw_data=msg_data
            )
            
            # 处理消息
            await self._process_inbound_message(msg)
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _get_current_chat(self) -> Optional[tuple]:
        """Get current active chat ID and name."""
        try:
            # 获取当前选中的聊天
            chat_title = await self.page.locator('header div[title]').first.text_content()
            if chat_title:
                # 从标题中提取聊天ID（这里简化处理）
                chat_id = chat_title.lower().replace(' ', '_')
                self._known_chats[chat_id] = chat_title
                return chat_id, chat_title
        except:
            pass
        return None

    async def _open_chat(self, chat_id: str) -> None:
        """Open a chat by ID."""
        try:
            # 搜索聊天
            search_box = self.page.locator('div[contenteditable="true"][data-tab="3"]')
            await search_box.click()
            await search_box.fill('')
            await search_box.fill(chat_id)
            await asyncio.sleep(1)
            
            # 点击第一个结果
            await self.page.locator(f'[title="{self._known_chats.get(chat_id, chat_id)}"]').first.click()
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Failed to open chat {chat_id}: {e}")
            raise

    async def _load_known_chats(self) -> None:
        """Load list of known chats."""
        try:
            # 这里可以加载已保存的聊天列表
            known_file = self.session_dir / "chats.json"
            if known_file.exists():
                with open(known_file, 'r') as f:
                    self._known_chats = json.load(f)
                logger.info(f"Loaded {len(self._known_chats)} known chats")
        except Exception as e:
            logger.debug(f"No known chats loaded: {e}")
