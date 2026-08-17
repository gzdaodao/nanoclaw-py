# ipc.py
import asyncio
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Callable, Awaitable, Set, Any
from datetime import datetime

from croniter import croniter

from .config import DATA_DIR, IPC_POLL_INTERVAL, MAIN_GROUP_FOLDER
from .logger import logger
from .dtypes import RegisteredGroup, ScheduleType, TaskStatus, ScheduledTask
from .group_folder import GroupFolderValidator
from .db import db_session, Database


class TaskSchedulerIPC:
    """Handle task scheduling operations from IPC"""
    
    def __init__(self):
        self.db = Database()  # Will use connection pooling
    
    def create_task(self, data: dict, target_folder: str, source_group: str, is_main: bool) -> Optional[str]:
        """Create a new scheduled task"""
        required = ['prompt', 'schedule_type', 'schedule_value', 'targetJid']
        if not all(k in data for k in required):
            logger.warn('Invalid schedule_task request: missing fields')
            return None
        
        target_jid = data['targetJid']
        
        schedule_type = data['schedule_type']
        schedule_value = data['schedule_value']
        
        next_run = self._calculate_next_run(schedule_type, schedule_value)
        if next_run is None:
            return None
        
        task_id = f'task-{int(datetime.now().timestamp() * 1000)}-{uuid.uuid4().hex[:6]}'
        
        context_mode = data.get('context_mode', 'isolated')
        if context_mode not in ('group', 'isolated'):
            context_mode = 'isolated'
        
        task = ScheduledTask(
            id=task_id,
            group_folder=target_folder,
            chat_id=target_jid,
            prompt=data['prompt'],
            schedule_type=ScheduleType(schedule_type),
            schedule_value=schedule_value,
            context_mode=context_mode,
            next_run=next_run,
            status=TaskStatus.ACTIVE,
            created_at=datetime.now().isoformat()
        )
        
        with db_session() as db:
            db.create_task(task)
        
        logger.info(f'Task created via IPC: {task_id} for {target_folder} (mode={context_mode})')
        return task_id
    
    def _calculate_next_run(self, schedule_type: str, schedule_value: str) -> Optional[str]:
        """Calculate next run time based on schedule"""
        try:
            if schedule_type == 'cron':
                base_time = datetime.now()
                iter = croniter(schedule_value, base_time)
                return iter.get_next(datetime).isoformat()
            elif schedule_type == 'interval':
                ms = int(schedule_value)
                if ms <= 0:
                    raise ValueError('Interval must be positive')
                next_run = datetime.now().timestamp() * 1000 + ms
                return datetime.fromtimestamp(next_run / 1000).isoformat()
            elif schedule_type == 'once':
                scheduled = datetime.fromisoformat(schedule_value)
                return scheduled.isoformat()
        except Exception as e:
            logger.warn(f'Invalid schedule {schedule_type}={schedule_value}: {e}')
            return None
    
    def pause_task(self, task_id: str, task_folder: str, is_main: bool) -> bool:
        """Pause a task"""
        with db_session() as db:
            task = db.get_task_by_id(task_id)
            if task and (is_main or task.group_folder == task_folder):
                db.update_task(task_id, status='paused')
                logger.info(f'Task paused via IPC: {task_id}')
                return True
            else:
                logger.warn(f'Unauthorized task pause attempt: {task_id}')
                return False
    
    def resume_task(self, task_id: str, task_folder: str, is_main: bool) -> bool:
        """Resume a task"""
        with db_session() as db:
            task = db.get_task_by_id(task_id)
            if task and (is_main or task.group_folder == task_folder):
                db.update_task(task_id, status='active')
                logger.info(f'Task resumed via IPC: {task_id}')
                return True
            else:
                logger.warn(f'Unauthorized task resume attempt: {task_id}')
                return False
    
    def cancel_task(self, task_id: str, task_folder: str, is_main: bool) -> bool:
        """Cancel a task"""
        with db_session() as db:
            task = db.get_task_by_id(task_id)
            if task and (is_main or task.group_folder == task_folder):
                db.delete_task(task_id)
                logger.info(f'Task cancelled via IPC: {task_id}')
                return True
            else:
                logger.warn(f'Unauthorized task cancel attempt: {task_id}')
                return False


class GroupRegistrationIPC:
    """Handle group registration operations from IPC"""
    
    def __init__(self, register_callback: Callable[[str, RegisteredGroup], None], 
                 get_channels_info: Callable[[], List[Dict[str, Any]]]):
        self.register_callback = register_callback
        self.get_channels_info = get_channels_info
        self.validator = GroupFolderValidator()
    
    def register_group(self, data: dict, source_group: str, is_main: bool) -> bool:
        """Register a new group"""
        if not is_main:
            logger.warn(f'Unauthorized register_group attempt from {source_group}')
            return False
        
        required = ['jid', 'name', 'folder', 'trigger']
        if not all(k in data for k in required):
            logger.warn('Invalid register_group request: missing fields')
            return False
        
        if not self.validator.is_valid(data['folder']):
            logger.warn(f'Invalid register_group request: unsafe folder {data["folder"]}')
            return False
        
        # 验证通道配置
        preferred_channel = data.get('preferred_channel')
        allowed_channels = data.get('allowed_channels', [])
        
        available_channels = [ch['name'] for ch in self.get_channels_info()]
        
        if preferred_channel and preferred_channel not in available_channels:
            logger.warn(f'Preferred channel {preferred_channel} not available, ignoring')
            preferred_channel = None
        
        if allowed_channels:
            valid_channels = [ch for ch in allowed_channels if ch in available_channels]
            if len(valid_channels) != len(allowed_channels):
                logger.warn(f'Some allowed channels not available: {set(allowed_channels) - set(valid_channels)}')
            allowed_channels = valid_channels
        
        group = RegisteredGroup(
            name=data['name'],
            folder=data['folder'],
            trigger=data['trigger'],
            added_at=datetime.now().isoformat(),
            containerConfig=data.get('containerConfig'),
            requiresTrigger=data.get('requiresTrigger'),
            preferred_channel=preferred_channel,
            allowed_channels=allowed_channels
        )
        
        self.register_callback(data['jid'], group)
        logger.info(f'Group registered via IPC: {data["jid"]} as {data["folder"]}' +
                   (f' (preferred: {preferred_channel})' if preferred_channel else '') +
                   (f' (allowed: {allowed_channels})' if allowed_channels else ''))
        return True



class MessageIPC:
    """Handle message operations from IPC"""
    
    def __init__(self, send_message: Callable[[str, str, Optional[str]], Awaitable[None]]):
        self.send_message = send_message
    
    async def process_message(self, data: dict, target_group: Optional[RegisteredGroup], 
                             source_group: str, is_main: bool) -> bool:
        """Process a message via IPC"""
        #logger.error('process_message: 1')
        if data.get('type') != 'message' or not data.get('chatJid') or not data.get('text'):
            logger.error(f'process_message data error:{data}')
            return False
        
        #logger.error('process_message: 2')
        target_jid = data['chatJid']
        channel_name = data.get('channel')  # 可选，指定通道
        
        # 权限检查：非主组只能给自己组的聊天发消息
        if not is_main and (not target_group or target_group.folder != source_group):
            # 检查是否在允许的通道列表中
            if target_group and target_group.allowed_channels:
                if channel_name not in target_group.allowed_channels:
                    logger.warn(f'Unauthorized IPC message attempt: {target_jid} from {source_group}')
                    return False
            else:
                logger.warn(f'Unauthorized IPC message attempt: {target_jid} from {source_group}')
                return False
        
        #logger.error('process_message: 3')
        await self.send_message(target_jid, data['text'], channel_name)
        logger.info(f'IPC message sent: {target_jid} from {source_group}' + 
                   (f' via {channel_name}' if channel_name else ''))
        return True


class IpcDeps:
    def __init__(
        self,
        send_message: Callable[[str, str, Optional[str]], Awaitable[None]],  # 添加 channel 参数
        registered_groups: Callable[[], Dict[str, RegisteredGroup]],
        register_group: Callable[[str, RegisteredGroup], None],
        sync_group_metadata: Callable[[bool, Optional[str]], Awaitable[None]],  # 添加 channel 参数
        get_available_groups: Callable[[], List[Dict[str, Any]]],
        write_groups_snapshot: Callable[[str, bool, List[Dict[str, Any]], Set[str]], None],
        get_channels_info: Callable[[], List[Dict[str, Any]]]  # 新增：获取通道信息
    ):
        self.send_message = send_message
        self.registered_groups = registered_groups
        self.register_group = register_group
        self.sync_group_metadata = sync_group_metadata
        self.get_available_groups = get_available_groups
        self.write_groups_snapshot = write_groups_snapshot
        self.get_channels_info = get_channels_info


class IPCProcessor:
    """Process IPC requests"""
    
    def __init__(self, deps: IpcDeps):
        self.deps = deps
        self.task_scheduler = TaskSchedulerIPC()
        self.group_registration = GroupRegistrationIPC(deps.register_group, deps.get_channels_info)
        self.message_handler = MessageIPC(deps.send_message)
    
    async def process_file(self, file: Path, source_group: str, is_main: bool) -> None:
        """Process a single IPC file"""
        try:
            data = json.loads(file.read_text())
            target_group = self.deps.registered_groups().get(data.get('chatJid'))
            
            task_type = data.get('type')
            success = False
            
            if task_type == 'message':
                success = await self.message_handler.process_message(data, target_group, source_group, is_main)
            
            elif task_type == 'schedule_task':
                if target_group:
                    target_folder = target_group.folder
                    if is_main or target_folder == source_group:
                        self.task_scheduler.create_task(data, target_folder, source_group, is_main)
                        success = True

            elif task_type == 'list_tasks':
                # 列出任务
                with db_session() as db:
                    tasks = db.get_tasks_for_group(source_group) if not is_main else db.get_all_tasks()
                    # 通过 send_message 返回结果
                    tasks_text = "📋 **Scheduled Tasks:**\n\n"
                    if tasks:
                        for t in tasks[:10]:
                            status_icon = "🟢" if t.status == "active" else "⏸️" if t.status == "paused" else "✅"
                            tasks_text += f"{status_icon} **{t.id}**\n"
                            tasks_text += f"  📝 {t.prompt[:50]}...\n"
                            tasks_text += f"  ⏰ {t.schedule_type}: {t.schedule_value}\n"
                            tasks_text += f"  📅 Next: {t.next_run or 'N/A'}\n\n"
                        if len(tasks) > 10:
                            tasks_text += f"... and {len(tasks) - 10} more tasks"
                    else:
                        tasks_text += "No tasks found."
                    
                    await self.deps.send_message(data.get('chatJid'), tasks_text, None)
                    success = True
            
            elif task_type == 'pause_task':
                task_id = data.get('taskId')
                if task_id:
                    success = self.task_scheduler.pause_task(task_id, source_group, is_main)
                    if success:
                        await self.deps.send_message(data.get('chatJid'), f"⏸️ Task {task_id} paused.", None)
                    else:
                        await self.deps.send_message(data.get('chatJid'), f"❌ Failed to pause task {task_id}.", None)
            
            elif task_type == 'resume_task':
                task_id = data.get('taskId')
                if task_id:
                    success = self.task_scheduler.resume_task(task_id, source_group, is_main)
                    if success:
                        await self.deps.send_message(data.get('chatJid'), f"▶️ Task {task_id} resumed.", None)
                    else:
                        await self.deps.send_message(data.get('chatJid'), f"❌ Failed to resume task {task_id}.", None)
           
            
            elif task_type == 'cancel_task':
                task_id = data.get('taskId')
                if task_id:
                    success = self.task_scheduler.cancel_task(task_id, source_group, is_main)
                    if success:
                        await self.deps.send_message(data.get('chatJid'), f"▶️ Task {task_id} cancelled.", None)
                    else:
                        await self.deps.send_message(data.get('chatJid'), f"❌ Failed to cancel task {task_id}.", None)
 
            
            elif task_type == 'refresh_groups':
                await self._handle_refresh_groups(source_group, is_main)
                success = True
            
            elif task_type == 'register_group':
                success = self.group_registration.register_group(data, source_group, is_main)


            elif task_type == 'list_groups':
                # 列出群组
                groups = self.deps.get_available_groups()
                groups_text = "📋 **Available Groups:**\n\n"
                if groups:
                    for g in groups[:20]:
                        status = "✅" if g['isRegistered'] else "⬜"
                        groups_text += f"{status} **{g['name']}**\n"
                        groups_text += f"  📱 JID: {g['jid']}\n"
                        groups_text += f"  📅 Last: {g['lastActivity'][:16] if g.get('lastActivity') else 'N/A'}\n\n"
                    if len(groups) > 20:
                        groups_text += f"... and {len(groups) - 20} more groups"
                else:
                    groups_text += "No groups found."
                
                await self.deps.send_message(data.get('chatJid'), groups_text, None)
                success = True
            
            elif task_type == 'get_channels':
                # 获取通道信息
                channels = self.deps.get_channels_info()
                channels_text = "📡 **Available Channels:**\n\n"
                if channels:
                    for ch in channels:
                        status = "🟢" if ch['connected'] else "🔴"
                        channels_text += f"{status} **{ch['name']}** ({ch['type']})\n"
                        if ch.get('features'):
                            features = [f"✅ {k}" for k, v in ch['features'].items() if v]
                            if features:
                                channels_text += f"  Features: {', '.join(features)}\n"
                        channels_text += "\n"
                else:
                    channels_text += "No channels available."
                
                await self.deps.send_message(data.get('chatJid'), channels_text, None)
                success = True
 
            else:
                logger.error(f'Not support type: {task_type} IPC file {file}')
            
            # Delete successful file
            if success:
                file.unlink()
            else:
                # Move to errors if not successful
                logger.error(f'Error handling IPC file {file}')
                self._move_to_error(file, source_group, "processing_failed")
            
        except json.JSONDecodeError as e:
            logger.error(f'Invalid JSON in IPC file {file}: {e}')
            self._move_to_error(file, source_group, "invalid_json")
        except Exception as e:
            logger.error(f'Error processing IPC file {file}: {e}')
            self._move_to_error(file, source_group, "exception")

    
    async def _handle_get_channels(self, source_group: str, is_main: bool) -> bool:
        """Handle get channels request"""
        if not is_main:
            logger.warn(f'Unauthorized get_channels attempt from {source_group}')
            return False
        
        channels_info = self.deps.get_channels_info()
        
        # 写入响应文件
        response_dir = Path(DATA_DIR) / 'ipc' / source_group / 'responses'
        response_dir.mkdir(parents=True, exist_ok=True)
        
        response_file = response_dir / f'channels_{int(datetime.now().timestamp() * 1000)}.json'
        response_file.write_text(json.dumps({
            'type': 'channels_info',
            'channels': channels_info,
            'timestamp': datetime.now().isoformat()
        }, indent=2))
        
        logger.info(f'Channels info sent to {source_group}')
        return True
    
    def _move_to_error(self, file: Path, source_group: str, reason: str) -> None:
        """Move file to error directory"""
        error_dir = Path(DATA_DIR) / 'ipc' / 'errors'
        error_dir.mkdir(exist_ok=True)
        new_name = f'{source_group}-{reason}-{file.name}'
        file.rename(error_dir / new_name)
    
    async def _handle_refresh_groups(self, source_group: str, is_main: bool) -> None:
        """Handle group refresh request"""
        if not is_main:
            logger.warn(f'Unauthorized refresh_groups attempt from {source_group}')
            return
        
        logger.info(f'Group metadata refresh requested via IPC from {source_group}')
        await self.deps.sync_group_metadata(True)
        available_groups = self.deps.get_available_groups()
        self.deps.write_groups_snapshot(
            source_group,
            True,
            available_groups,
            set(self.deps.registered_groups().keys())
        )


class IpcWatcher:
    """Watch IPC directories for requests"""
    
    def __init__(self, deps: IpcDeps):
        self.deps = deps
        self.ipc_base_dir = Path(DATA_DIR) / 'ipc'
        self.processor = IPCProcessor(deps)
        self._running = False
    
    async def start(self) -> None:
        """Start watching IPC directories"""
        if self._running:
            logger.debug('IPC watcher already running')
            return
        
        self._running = True
        self.ipc_base_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info('IPC watcher started')
        
        while self._running:
            try:
                await self._scan_once()
                await asyncio.sleep(IPC_POLL_INTERVAL / 1000)
            except Exception as e:
                logger.error(f'Error in IPC watcher loop: {e}')
                await asyncio.sleep(IPC_POLL_INTERVAL / 1000)
    
    async def stop(self) -> None:
        """Stop watching IPC directories"""
        self._running = False
    
    async def _scan_once(self) -> None:
        """Perform one scan of IPC directories"""
        # Get group folders
        group_folders = []
        try:
            for item in self.ipc_base_dir.iterdir():
                if item.is_dir() and item.name != 'errors':
                    group_folders.append(item.name)
        except Exception as e:
            logger.error(f'Error reading IPC base directory: {e}')
            return
        
        registered_groups = self.deps.registered_groups()
        
        for source_group in group_folders:
            is_main = source_group == MAIN_GROUP_FOLDER
            await self._process_group_directories(source_group, is_main, registered_groups)
    
    async def _process_group_directories(self, source_group: str, is_main: bool, 
                                         registered_groups: Dict[str, RegisteredGroup]) -> None:
        """Process message and task directories for a group"""
        messages_dir = self.ipc_base_dir / source_group / 'messages'
        tasks_dir = self.ipc_base_dir / source_group / 'tasks'
        
        # Process messages
        if messages_dir.exists():
            await self._process_directory(messages_dir, source_group, is_main)
        
        # Process tasks
        if tasks_dir.exists():
            await self._process_directory(tasks_dir, source_group, is_main)
    
    async def _process_directory(self, directory: Path, source_group: str, is_main: bool) -> None:
        """Process all files in a directory"""
        try:
            for file in sorted(directory.glob('*.json')):  # Process in order
                await self.processor.process_file(file, source_group, is_main)
        except Exception as e:
            logger.error(f'Error reading directory {directory} for {source_group}: {e}')


# For backward compatibility
_ipc_watcher_instance: Optional[IpcWatcher] = None


async def start_ipc_watcher(deps: IpcDeps) -> None:
    """Start IPC watcher (backward compatibility)"""
    global _ipc_watcher_instance
    _ipc_watcher_instance = IpcWatcher(deps)
    await _ipc_watcher_instance.start()
