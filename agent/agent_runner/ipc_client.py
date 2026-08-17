# agents/ipc_client.py
"""IPC client for agent communication."""

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Union
from datetime import datetime
import os

from .logger import logger


class IpcClient:
    """IPC client for agent communication"""
    
    def __init__(self, ipc_dir: str = '/workspace/ipc'):
        self.ipc_dir = Path(ipc_dir)
        self.messages_dir = self.ipc_dir / 'messages'
        self.output_dir = self.ipc_dir / 'messages'
        self._running = False
    
    async def connect(self):
        """Connect to IPC (create directories)"""
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("IPC client connected")
    
    async def disconnect(self):
        """Disconnect from IPC"""
        self._running = False
        logger.debug("IPC client disconnected")
    
    async def send_request(self, data: Union[Dict[str, Any], str]) -> None:
        """
        发送请求到主系统（统一使用 messages 目录）
        
        Args:
            data: 请求数据，可以是字典或 JSON 字符串
        """
        # 如果传入的是字符串，解析为字典
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON string: {data}")
                raise ValueError(f"Invalid JSON string: {data}")
        
        # 确保是字典类型
        if not isinstance(data, dict):
            logger.error(f"Data must be dict or JSON string, got {type(data)}")
            raise TypeError(f"Data must be dict or JSON string, got {type(data)}")
        
        # 确保有 type 字段
        if 'type' not in data:
            data['type'] = 'request'
        
        # 添加时间戳
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now().isoformat()
        
        filename = f"{int(datetime.now().timestamp() * 1000)}-{os.urandom(2).hex()}.json"
        filepath = self.messages_dir / filename
        
        # 原子写入
        temp_path = filepath.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data, ensure_ascii=False))
        temp_path.rename(filepath)
        
        logger.debug(f"Request sent: {filename} type={data.get('type')}")
    
    async def send_output(self, text: str, chat_id: str, command_id: str = None):
        """Send output message"""
        await self.send_request({
            'type': 'message',
            'chatJid': chat_id,
            'text': text,
            'timestamp': datetime.now().isoformat()
        })
    
    async def send_error(self, error: str, chat_id: str, message_id: str = None):
        """Send error message"""
        await self.send_request({
            'type': 'error',
            'chatJid': chat_id,
            'error': error,
            'timestamp': datetime.now().isoformat()
        })
    
    async def send_task_result(self, task_id: str, result: Dict[str, Any], chat_id: str):
        """Send task result"""
        await self.send_request({
            'type': 'task_result',
            'chatJid': chat_id,
            'task_id': task_id,
            'result': result,
            'timestamp': datetime.now().isoformat()
        })
    
    async def request_channels(self) -> Optional[Dict]:
        """Request channel information"""
        return None
