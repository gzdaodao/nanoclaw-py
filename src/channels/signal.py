# channels/signal.py
"""Signal channel implementation using signal-cli."""

import asyncio
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from loguru import logger

from .base import Channel, InboundMessage
from .. import config


class SignalChannel(Channel):
    """Signal channel using signal-cli."""

    def __init__(
        self,
        on_message,
        on_chat_metadata,
        name: str = "signal",
        registered_groups=None,
        phone_number: Optional[str] = None,
        signal_cli_path: str = "signal-cli",
        message_limit: int = 4096
    ):
        super().__init__(on_message, on_chat_metadata, name, registered_groups=registered_groups)
        self.phone_number = phone_number or config.SIGNAL_PHONE_NUMBER
        self.signal_cli_path = signal_cli_path
        self.message_limit = message_limit
        self.data_dir = Path(config.DATA_DIR) / "signal"
        
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False

    async def connect(self) -> None:
        """Connect to Signal via signal-cli."""
        try:
            # 确保数据目录存在
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # 检查 signal-cli 是否可用
            await self._run_command([self.signal_cli_path, "--version"])
            
            # 检查是否已注册
            if not await self._is_registered():
                logger.info("Signal not registered. Please run registration first.")
                logger.info(f"Use: {self.signal_cli_path} -u {self.phone_number} register")
                logger.info(f"Then: {self.signal_cli_path} -u {self.phone_number} verify <CODE>")
                raise RuntimeError("Signal not registered")
            
            self._connected = True
            self._running = True
            logger.info(f"Signal connected: {self.phone_number}")
            
            # 启动接收循环
            self._receive_task = asyncio.create_task(self._receive_loop())
            
        except Exception as e:
            logger.error(f"Failed to connect to Signal: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Signal."""
        self._running = False
        self._connected = False
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Signal disconnected")

    def owns_jid(self, jid: str) -> bool:
        """Check if JID belongs to Signal."""
        return jid.startswith(f"{self.name}:")

    async def send_message(self, jid: str, text: str) -> bool:
        """Send message via Signal."""
        if not self._connected:
            logger.error("Signal not connected")
            return False
        
        try:
            recipient = self.get_chat_id_from_jid(jid)
            
            # 处理消息长度限制
            if len(text) > self.message_limit:
                # 分块发送
                for i in range(0, len(text), self.message_limit):
                    chunk = text[i:i + self.message_limit]
                    await self._send_chunk(recipient, chunk)
            else:
                await self._send_chunk(recipient, text)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Signal message: {e}")
            return False

    async def _send_chunk(self, recipient: str, text: str) -> None:
        """Send a message chunk."""
        cmd = [
            self.signal_cli_path,
            "-u", self.phone_number,
            "send",
            "-m", text,
            recipient
        ]
        await self._run_command(cmd)
        logger.debug(f"Message chunk sent to {recipient}")

    async def _receive_loop(self) -> None:
        """Main receive loop."""
        while self._running:
            try:
                # 接收消息
                cmd = [
                    self.signal_cli_path,
                    "-u", self.phone_number,
                    "receive",
                    "--json"
                ]
                
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await proc.communicate()
                
                if proc.returncode == 0 and stdout:
                    await self._process_messages(stdout.decode())
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Signal receive loop: {e}")
                await asyncio.sleep(5)

    async def _process_messages(self, data: str) -> None:
        """Process received messages."""
        try:
            # signal-cli 输出可能包含多个 JSON 对象
            for line in data.strip().split('\n'):
                if not line:
                    continue
                
                try:
                    msg_data = json.loads(line)
                    await self._process_message(msg_data)
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            logger.error(f"Error processing Signal messages: {e}")

    async def _process_message(self, msg_data: Dict[str, Any]) -> None:
        """Process a single message."""
        try:
            # 提取消息信息
            envelope = msg_data.get('envelope', {})
            data_message = envelope.get('dataMessage', {})
            
            if not data_message:
                return
            
            source = envelope.get('source', '')
            timestamp = envelope.get('timestamp', 0) / 1000  # 转换为秒
            
            # 确定聊天类型和ID
            is_group = 'groupInfo' in data_message
            if is_group:
                group_id = data_message['groupInfo'].get('groupId', '')
                chat_id = f"group:{group_id}"
                chat_name = data_message['groupInfo'].get('name', group_id)
            else:
                chat_id = source
                chat_name = source
            
            jid = self.create_jid(chat_id)
            # 创建标准消息
            msg = InboundMessage(
                id=data_message.get('timestamp', str(timestamp)),
                chat_id=jid,
                chat_name=chat_name,
                sender_id=source,
                sender_name=source.split('.')[0],  # 简化处理
                content=data_message.get('message', ''),
                timestamp=datetime.fromtimestamp(timestamp),
                is_from_me=False,  # signal-cli 不标记自己的消息
                is_group=is_group,
                raw_data=msg_data
            )
            
            # 处理消息
            await self._process_inbound_message(msg)
            
        except Exception as e:
            logger.error(f"Error processing Signal message: {e}")

    async def _is_registered(self) -> bool:
        """Check if Signal is registered."""
        try:
            cmd = [
                self.signal_cli_path,
                "-u", self.phone_number,
                "listIdentities"
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except:
            return False

    async def _run_command(self, cmd: List[str]) -> str:
        """Run a command and return output."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            error = stderr.decode()
            raise RuntimeError(f"Command failed: {error}")
        
        return stdout.decode()
