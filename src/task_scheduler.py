# task_scheduler.py
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Callable, Awaitable, Any

from croniter import croniter

from .config import ASSISTANT_NAME, MAIN_GROUP_FOLDER, SCHEDULER_POLL_INTERVAL
from .logger import logger
from .dtypes import RegisteredGroup, ScheduledTask, TaskRunLog, NewMessage
from .db import db_session, get_db
from .group_folder import GroupFolderResolver
from .container_runner import ContainerRunner, ContainerOutput, ContainerInput
from .group_queue import GroupQueue
from .snapshot import SnapshotWriter
from .router import MessageFormatter


class SchedulerDeps:
    """Dependencies for scheduler"""
    def __init__(
        self,
        registered_groups: Callable[[], Dict[str, RegisteredGroup]],
        get_sessions: Callable[[], Dict[str, str]],
        queue: GroupQueue,
        on_process: Callable[[str, Any, str, str], None],
        send_message: Callable[[str, str, Optional[str]], Awaitable[None]],
        get_channels_info: Callable[[], List[Dict[str, Any]]]
    ):
        self.registered_groups = registered_groups
        self.get_sessions = get_sessions
        self.queue = queue
        self.on_process = on_process
        self.send_message = send_message
        self.get_channels_info = get_channels_info


class NextRunCalculator:
    """Calculate next run time for scheduled tasks"""
    
    @staticmethod
    def calculate(task: ScheduledTask) -> Optional[str]:
        """Calculate next run time based on schedule"""
        if task.schedule_type.value == 'cron':
            try:
                base = datetime.now()
                iter = croniter(task.schedule_value, base)
                return iter.get_next(datetime).isoformat()
            except Exception as e:
                logger.error(f"Error calculating cron next run: {e}")
                return None
        elif task.schedule_type.value == 'interval':
            try:
                ms = int(task.schedule_value)
                next_run = (datetime.now().timestamp() * 1000 + ms) / 1000
                return datetime.fromtimestamp(next_run).isoformat()
            except Exception as e:
                logger.error(f"Error calculating interval next run: {e}")
                return None
        return None


class TaskRunner:
    """Run scheduled tasks"""
    
    def __init__(self, deps: SchedulerDeps):
        self.deps = deps
        self.container_runner = ContainerRunner()
        self.folder_resolver = GroupFolderResolver()
        self.next_run_calculator = NextRunCalculator()
        self.snapshot_writer = SnapshotWriter(self.folder_resolver)
    
    async def run_task(self, task: ScheduledTask) -> None:
        """Run a scheduled task"""
        logger.info(f'[RUN_TASK] START: task {task.id} for group {task.group_folder}')
        start_time = datetime.now()
               
        # Validate group folder
        try:
            group_dir = self.folder_resolver.resolve_group_folder(task.group_folder)
            group_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            error = str(e)
            with db_session() as db:
                db.update_task(task.id, status='paused')
            logger.error(f'Task {task.id} has invalid group folder: {error}')
            self._log_task_run(task.id, start_time, 'error', error=error)
            return
        
        logger.info(f'Running scheduled task {task.id} for group {task.group_folder}')
        
        groups = self.deps.registered_groups()
        group = next((g for g in groups.values() if g.folder == task.group_folder), None)
        
        if not group:
            error = f'Group not found: {task.group_folder}'
            logger.error(f'Task {task.id}: {error}')
            self._log_task_run(task.id, start_time, 'error', error=error)
            return
        
        # Session handling
        sessions = self.deps.get_sessions()
        session_id = sessions.get(task.group_folder) if task.context_mode == 'group' else None
        
        # Update tasks snapshot
        with db_session() as db:
            all_tasks = db.get_all_tasks()
            is_main = task.group_folder == MAIN_GROUP_FOLDER
            self.snapshot_writer.write_tasks_snapshot(
                task.group_folder,
                is_main,
                [{
                    'id': t.id,
                    'groupFolder': t.group_folder,
                    'prompt': t.prompt,
                    'schedule_type': t.schedule_type.value if hasattr(t.schedule_type, 'value') else t.schedule_type,
                    'schedule_value': t.schedule_value,
                    'status': t.status.value if hasattr(t.status, 'value') else t.status,
                    'next_run': t.next_run
                } for t in all_tasks]
            )
        
        result_text = None
        error_text = None
        
        # Task close delay
        TASK_CLOSE_DELAY_MS = 10000
        close_timer: Optional[asyncio.Task] = None
        
        async def schedule_close():
            nonlocal close_timer
            if close_timer:
                return
            await asyncio.sleep(TASK_CLOSE_DELAY_MS / 1000)
            logger.debug(f'Closing task container for {task.id}')
            self.deps.queue.close_stdin(task.chat_id)
        
        try:
            #构造消息并存入数据库（和 channel 消息一样）
            task_msg = NewMessage(
                id=f'task_{task.id}_{int(datetime.now().timestamp() * 1000)}',
                chat_id=task.chat_id,
                sender_id='system',
                sender_name='Scheduler',
                content=f'[Task: {task.id}] {task.prompt}',
                timestamp=datetime.now(),
                is_from_me=True,
                is_bot_message=False
            )
            
            with db_session() as db:
                db.store_message(task_msg)
            
            logger.info(f'Task {task.id} Message stored.')
            
        except Exception as e:
            if close_timer:
                close_timer.cancel()
            error_text = str(e)
            logger.error(f'Task {task.id} failed: {e}')
        
        # Log task run
        self._log_task_run(
            task.id, 
            start_time, 
            'error' if error_text else 'success',
            result_text,
            error_text
        )
        
        # Update task after run
        with db_session() as db:
            next_run = self.next_run_calculator.calculate(task)
            result_summary = f'Error: {error_text}' if error_text else (result_text[:200] if result_text else 'Completed')
            db.update_task_after_run(task.id, next_run, result_summary)
       
    
    def _create_output_handler(self, task, result_text, error_text, close_timer, schedule_close):
        """Create output handler for container"""
                
        async def on_stream(output: ContainerOutput):
            nonlocal result_text, error_text
            
            if output.result:
                result_text = output.result
                
                # 直接使用 task.chat_id 发送，系统会自动路由到正确的频道
                await self.deps.send_message(
                    task.chat_id,
                    output.result,
                    None
                )
                
                if not close_timer:
                    close_timer = asyncio.create_task(schedule_close())
            
            if output.status == 'success':
                self.deps.queue.notify_idle(task.chat_id)
            
            if output.status == 'error':
                error_text = output.error or 'Unknown error'
        
        return on_stream
 
    def _log_task_run(self, task_id: str, start_time: datetime, status: str, 
                     result: Optional[str] = None, error: Optional[str] = None) -> None:
        """Log task run to database"""
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        log = TaskRunLog(
            task_id=task_id,
            run_at=datetime.now().isoformat(),
            duration_ms=int(duration_ms),
            status=status,
            result=result,
            error=error
        )
        with db_session() as db:
            db.log_task_run(log)


class SchedulerLoop:
    """Main scheduler loop"""
    
    def __init__(self, deps: SchedulerDeps):
        self.deps = deps
        self.task_runner = TaskRunner(deps)
        self._running = False
    
    async def start(self) -> None:
        """Start scheduler loop"""
        if self._running:
            logger.debug('Scheduler loop already running')
            return
        
        self._running = True
        logger.info('Scheduler loop started')
        
        while self._running:
            try:
                with db_session() as db:
                    due_tasks = db.get_due_tasks()
                
                if due_tasks:
                    logger.info(f'Found {len(due_tasks)} due tasks')
                
                for task in due_tasks:
                    # Re-check task status
                    with db_session() as db:
                        current = db.get_task_by_id(task.id)
                    
                    if not current or current.status != 'active':
                        continue
                    
                    self.deps.queue.enqueue_task(
                        current.chat_id,
                        current.id,
                        lambda t=task: self.task_runner.run_task(t)
                    )
                
                await asyncio.sleep(SCHEDULER_POLL_INTERVAL / 1000)
                
            except Exception as e:
                logger.error(f'Error in scheduler loop: {e}')
                await asyncio.sleep(SCHEDULER_POLL_INTERVAL / 1000)
    
    async def stop(self) -> None:
        """Stop scheduler loop"""
        self._running = False


# For backward compatibility
_scheduler_loop_instance: Optional[SchedulerLoop] = None


async def start_scheduler_loop(deps: SchedulerDeps) -> None:
    """Start scheduler loop (backward compatibility)"""
    global _scheduler_loop_instance
    _scheduler_loop_instance = SchedulerLoop(deps)
    await _scheduler_loop_instance.start()


def reset_scheduler_loop_for_tests() -> None:
    """Reset scheduler loop state for tests"""
    global _scheduler_loop_instance
    if _scheduler_loop_instance:
        _scheduler_loop_instance._running = False
    _scheduler_loop_instance = None
