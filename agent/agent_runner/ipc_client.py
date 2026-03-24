# agents/ipc_client.py
"""IPC client for agent communication."""

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from datetime import datetime
import os

from .logger import logger


class IpcClient:
    """IPC client for agent communication"""
    
    def __init__(self, ipc_dir: str = '/workspace/ipc'):
        self.ipc_dir = Path(ipc_dir)
        self.output_dir = self.ipc_dir / 'messages'
        self.task_dir = self.ipc_dir / 'tasks'
        self._running = False
    
    async def connect(self):
        """Connect to IPC (create directories)"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("IPC client connected")
    
    async def disconnect(self):
        """Disconnect from IPC"""
        self._running = False
        logger.debug("IPC client disconnected")
    
    async def send_output(self, text: str, chat_id: str, command_id: str = None):
        """Send output message"""
        filename = f"{int(datetime.now().timestamp() * 1000)}-{os.urandom(2).hex()}.json"
        filepath = self.output_dir / filename
        
        data = {
            #'type': 'output',
            'type': 'message',
            'chatJid': chat_id,
            'text': text,
            'timestamp': datetime.now().isoformat()
        }
        
        # Write atomically
        temp_path = filepath.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data))
        temp_path.rename(filepath)
        
        logger.debug(f"Output sent: {filename}")
    
    async def send_error(self, error: str, chat_id: str, message_id: str = None):
        """Send error message"""
        filename = f"{int(datetime.now().timestamp() * 1000)}-{os.urandom(2).hex()}.json"
        filepath = self.output_dir / filename
        
        data = {
            #'type': 'error',
            'type': 'message',
            'chatJid': chat_id,
            'error': error,
            'timestamp': datetime.now().isoformat()
        }
        
        temp_path = filepath.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data))
        temp_path.rename(filepath)
        
        logger.error(f"Error sent: {error}")
    
    async def send_task_result(self, task_id: str, result: Dict[str, Any], chat_id: str):
        """Send task result"""
        filename = f"result-{task_id}-{int(datetime.now().timestamp() * 1000)}.json"
        filepath = self.task_dir / filename
        
        data = {
            'type': 'task_result',
            'chatJid': chat_id,
            'task_id': task_id,
            'result': result,
            'timestamp': datetime.now().isoformat()
        }
        
        temp_path = filepath.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data))
        temp_path.rename(filepath)
        
        logger.debug(f"Task result sent for {task_id}")
    
    async def request_channels(self) -> Optional[Dict]:
        """Request channel information"""
        # This would be implemented via main system
        return None
