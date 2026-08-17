# agents/ipc_client.py - 简化版

class IpcClient:
    def __init__(self, ipc_dir: str = '/workspace/ipc'):
        self.ipc_dir = Path(ipc_dir)
        self.messages_dir = self.ipc_dir / 'messages'  # 唯一目录
        self._running = False
    
    async def connect(self):
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("IPC client connected")
    
    async def send_request(self, data: Dict[str, Any]):
        """发送任何请求到主系统"""
        # 确保有 type 字段
        if 'type' not in data:
            data['type'] = 'request'
        
        # 添加时间戳
        data['timestamp'] = datetime.now().isoformat()
        
        filename = f"{int(datetime.now().timestamp() * 1000)}-{os.urandom(2).hex()}.json"
        filepath = self.messages_dir / filename
        
        temp_path = filepath.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data))
        temp_path.rename(filepath)
        
        logger.debug(f"Request sent: {filename} type={data.get('type')}")
    
    async def send_output(self, text: str, chat_id: str, command_id: str = None):
        """发送聊天消息（兼容旧接口）"""
        await self.send_request({
            'type': 'message',
            'chatJid': chat_id,
            'text': text
        })
    
    async def send_task_result(self, task_id: str, result: Dict[str, Any], chat_id: str):
        """发送任务结果（兼容旧接口）"""
        await self.send_request({
            'type': 'task_result',
            'chatJid': chat_id,
            'task_id': task_id,
            'result': result
        })