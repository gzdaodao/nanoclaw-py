# group_queue.py - 面向对象版本
import asyncio
import os
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Callable, Awaitable, Any

from .config import DATA_DIR, MAX_CONCURRENT_CONTAINERS
from .logger import logger


@dataclass
class QueuedTask:
    id: str
    group_jid: str
    fn: Callable[[], Awaitable[None]]


@dataclass
class GroupState:
    active: bool = False
    idle_waiting: bool = False
    is_task_container: bool = False
    pending_messages: bool = False
    pending_tasks: List[QueuedTask] = field(default_factory=list)
    process: Optional[Any] = None  # asyncio.subprocess.Process
    container_name: Optional[str] = None
    group_folder: Optional[str] = None
    retry_count: int = 0


class RetryPolicy:
    """Retry policy for failed operations"""
    
    MAX_RETRIES = 5
    BASE_RETRY_MS = 5000
    
    @classmethod
    def calculate_delay(cls, retry_count: int) -> int:
        """Calculate delay for retry"""
        return cls.BASE_RETRY_MS * (2 ** (retry_count - 1))


class InputWriter:
    """Write input to container via IPC"""
    
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
    
    def send_message(self, group_folder: str, text: str, group_jid: str) -> bool:
        """Send follow-up message to active container via IPC file"""
        logger.info(f'Group queue send_message: group_folder:{group_folder}, text:{text}')
        input_dir = self.data_dir / 'ipc' / group_folder / 'input'
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{int(datetime.now().timestamp() * 1000)}-{os.urandom(2).hex()}.json"
            filepath = input_dir / filename
            temp_path = filepath.with_suffix('.tmp')
            temp_path.write_text(json.dumps({'type': 'message', 'chatJid': group_jid, 'text': text}))
            temp_path.rename(filepath)
            return True
        except Exception as e:
            logger.debug(f'Failed to write input message: {e}')
            return False
    
    def close_stdin(self, group_folder: str) -> None:
        """Signal active container to wind down"""
        input_dir = self.data_dir / 'ipc' / group_folder / 'input'
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            (input_dir / '_close').write_text('')
        except Exception as e:
            logger.debug(f'Failed to close stdin: {e}')


class GroupQueue:
    """Queue for managing group containers"""
    
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_CONTAINERS):
        self._groups: Dict[str, GroupState] = {}
        self._active_count = 0
        self._waiting_groups: List[str] = []
        self._process_messages_fn: Optional[Callable[[str], Awaitable[bool]]] = None
        self._shutting_down = False
        self._lock = asyncio.Lock()
        self.max_concurrent = max_concurrent
        self.input_writer = InputWriter()
    
    def _get_group(self, group_jid: str) -> GroupState:
        """Get or create group state"""
        if group_jid not in self._groups:
            self._groups[group_jid] = GroupState()
        return self._groups[group_jid]
    
    def set_process_messages_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        """Set function to process messages"""
        self._process_messages_fn = fn
    
    def enqueue_message_check(self, group_jid: str) -> None:
        """Enqueue a message check for a group"""
        if self._shutting_down:
            return
        
        state = self._get_group(group_jid)
        
        if state.active:
            state.pending_messages = True
            logger.debug(f'Container active for {group_jid}, message queued')
            return
        
        if self._active_count >= self.max_concurrent:
            state.pending_messages = True
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(f'At concurrency limit for {group_jid}, message queued')
            return
        
        # Run immediately
        asyncio.create_task(self._run_for_group(group_jid, 'messages'))
    
    def enqueue_task(self, group_jid: str, task_id: str, fn: Callable[[], Awaitable[None]]) -> None:
        """Enqueue a task for a group"""
        if self._shutting_down:
            return
        
        state = self._get_group(group_jid)
        
        # Prevent double-queueing
        if any(t.id == task_id for t in state.pending_tasks):
            logger.debug(f'Task {task_id} already queued for {group_jid}, skipping')
            return
        
        if state.active:
            state.pending_tasks.append(QueuedTask(id=task_id, group_jid=group_jid, fn=fn))
            if state.idle_waiting:
                self._close_stdin(group_jid)
            logger.debug(f'Container active for {group_jid}, task {task_id} queued')
            return
        
        if self._active_count >= self.max_concurrent:
            state.pending_tasks.append(QueuedTask(id=task_id, group_jid=group_jid, fn=fn))
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(f'At concurrency limit for {group_jid}, task {task_id} queued')
            return
        
        # Run immediately
        asyncio.create_task(self._run_task(group_jid, QueuedTask(id=task_id, group_jid=group_jid, fn=fn)))
    
    def register_process(
        self,
        group_jid: str,
        proc: Any,
        container_name: str,
        group_folder: Optional[str] = None
    ) -> None:
        """Register a running process for a group"""
        state = self._get_group(group_jid)
        state.process = proc
        state.container_name = container_name
        if group_folder:
            state.group_folder = group_folder
    
    def notify_idle(self, group_jid: str) -> None:
        """Mark container as idle-waiting"""
        state = self._get_group(group_jid)
        state.idle_waiting = True
        if state.pending_tasks:
            self._close_stdin(group_jid)
    
    def send_message(self, group_jid: str, text: str) -> bool:
        """Send follow-up message to active container via IPC file"""
        state = self._get_group(group_jid)
        if not state.active or not state.group_folder or state.is_task_container:
            return False
        
        state.idle_waiting = False
        return self.input_writer.send_message(state.group_folder, text, group_jid)
    
    def _close_stdin(self, group_jid: str) -> None:
        """Signal active container to wind down"""
        state = self._get_group(group_jid)
        if not state.active or not state.group_folder:
            return
        self.input_writer.close_stdin(state.group_folder)
    
    async def _run_for_group(self, group_jid: str, reason: str) -> None:
        """Run message processing for a group"""
        async with self._lock:
            state = self._get_group(group_jid)
            state.active = True
            state.idle_waiting = False
            state.is_task_container = False
            state.pending_messages = False
            self._active_count += 1
        
        logger.debug(f'Starting container for group {group_jid} (reason={reason}, active={self._active_count})')
        
        try:
            if self._process_messages_fn:
                success = await self._process_messages_fn(group_jid)
                if success:
                    async with self._lock:
                        state.retry_count = 0
                else:
                    self._schedule_retry(group_jid, state)
        except Exception as e:
            logger.error(f'Error processing messages for group {group_jid}: {e}')
            self._schedule_retry(group_jid, state)
        finally:
            async with self._lock:
                state.active = False
                state.process = None
                state.container_name = None
                state.group_folder = None
                self._active_count -= 1
            await self._drain_group(group_jid)
    
    async def _run_task(self, group_jid: str, task: QueuedTask) -> None:
        """Run a queued task"""
        async with self._lock:
            state = self._get_group(group_jid)
            state.active = True
            state.idle_waiting = False
            state.is_task_container = True
            self._active_count += 1
        
        logger.debug(f'Running queued task {task.id} for group {group_jid} (active={self._active_count})')
        
        try:
            await task.fn()
        except Exception as e:
            logger.error(f'Error running task {task.id} for group {group_jid}: {e}')
        finally:
            async with self._lock:
                state.active = False
                state.is_task_container = False
                state.process = None
                state.container_name = None
                state.group_folder = None
                self._active_count -= 1
            await self._drain_group(group_jid)
    
    def _schedule_retry(self, group_jid: str, state: GroupState) -> None:
        """Schedule a retry for failed operation"""
        state.retry_count += 1
        if state.retry_count > RetryPolicy.MAX_RETRIES:
            logger.error(f'Max retries exceeded for {group_jid}, dropping messages')
            state.retry_count = 0
            return
        
        delay_ms = RetryPolicy.calculate_delay(state.retry_count)
        logger.info(f'Scheduling retry for {group_jid} (attempt {state.retry_count}) in {delay_ms}ms')
        
        async def retry():
            await asyncio.sleep(delay_ms / 1000)
            if not self._shutting_down:
                self.enqueue_message_check(group_jid)
        
        asyncio.create_task(retry())
    
    async def _drain_group(self, group_jid: str) -> None:
        """Process pending items for a group"""
        if self._shutting_down:
            return
        
        async with self._lock:
            state = self._get_group(group_jid)
            
            # Tasks first
            if state.pending_tasks:
                task = state.pending_tasks.pop(0)
                asyncio.create_task(self._run_task(group_jid, task))
                return
            
            # Then pending messages
            if state.pending_messages:
                asyncio.create_task(self._run_for_group(group_jid, 'drain'))
                return
        
        # Nothing pending for this group, check waiting groups
        await self._drain_waiting()
    
    async def _drain_waiting(self) -> None:
        """Process waiting groups"""
        async with self._lock:
            while (self._waiting_groups and
                   self._active_count < self.max_concurrent):
                next_jid = self._waiting_groups.pop(0)
                state = self._get_group(next_jid)
                
                if state.pending_tasks:
                    task = state.pending_tasks.pop(0)
                    asyncio.create_task(self._run_task(next_jid, task))
                elif state.pending_messages:
                    asyncio.create_task(self._run_for_group(next_jid, 'drain'))
    
    async def shutdown(self, grace_period_ms: int) -> None:
        """Gracefully shutdown the queue"""
        self._shutting_down = True
        
        # Count active containers
        active_containers = []
        for jid, state in self._groups.items():
            if state.active and state.process and state.container_name:
                active_containers.append(state.container_name)
        
        logger.info(
            f'GroupQueue shutting down (active={self._active_count}, '
            f'containers={active_containers})'
        )
