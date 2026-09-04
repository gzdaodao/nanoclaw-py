# main.py - 改进版，会话 ID 在 Main 中生成和管理
import asyncio
import signal
import json
import re
import uuid
from pathlib import Path
from typing import Dict, Set, List, Optional, Any, Callable
from datetime import datetime
import sys

from .config import (
    ASSISTANT_NAME, MAIN_GROUP_FOLDER, POLL_INTERVAL,
    TRIGGER_PATTERN, IDLE_TIMEOUT
)
from .logger import logger
from .db import Database, get_db
from .group_queue import GroupQueue
from .group_folder import GroupFolderResolver
from .container_runner import ContainerRunner, ContainerOutput, ContainerInput
from .snapshot import SnapshotWriter
from .container_runtime import ContainerRuntime
from .router import MessageFormatter, ChannelRouter, OutboundFormatter
from .ipc import IpcWatcher, IpcDeps
from .task_scheduler import SchedulerLoop, SchedulerDeps
from .dtypes import RegisteredGroup, NewMessage, Channel


class NanoClawApplication:
    """Main application class for NanoClaw with improved session management"""
    
    def __init__(self):
        # State
        self.last_timestamp: str = ''
        self.sessions: Dict[str, str] = {}  # group_folder -> session_id
        self.session_metadata: Dict[str, Dict[str, Any]] = {}  # 会话元数据
        self.registered_groups: Dict[str, RegisteredGroup] = {}
        self.last_agent_timestamp: Dict[str, str] = {}
        self.message_loop_running: bool = False
        
        # Core components
        self.db = get_db()
        self.container_runtime = ContainerRuntime()
        self.container_runner = ContainerRunner()
        self.group_folder_resolver = GroupFolderResolver()
        self.snapshot_writer = SnapshotWriter(self.group_folder_resolver)
        self.message_formatter = MessageFormatter()
        self.channel_router = ChannelRouter()
        self.outbound_formatter = OutboundFormatter()
        
        # Channels
        self.channels: List[Channel] = []
        
        # Queue
        self.queue = GroupQueue()
        
        # Control
        self._shutdown_event = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
    
    # ========================================================================
    # Session Management
    # ========================================================================
    
    def _generate_session_id(self, group_folder: str) -> str:
        """
        Generate a unique session ID for a group.
        This ensures uniqueness across groups and time.
        """
        unique_id = uuid.uuid4().hex
        return unique_id
    
    def ensure_session(self, group_folder: str, force_new: bool = False) -> str:
        """
        Ensure a group has a valid session ID.
        
        Args:
            group_folder: The group folder name
            force_new: If True, create a new session even if one exists
        
        Returns:
            The session ID for the group
        """
        # Force create new session
        if force_new:
            session_id = self._generate_session_id(group_folder)
            self.sessions[group_folder] = session_id
            self.db.set_session(group_folder, session_id)
            
            # Initialize session metadata
            self.session_metadata[group_folder] = {
                'created_at': datetime.now().isoformat(),
                'last_activity': datetime.now().isoformat(),
                'message_count': 0,
                'force_created': True
            }
            
            logger.info(f'Created new session (forced) for {group_folder}: {session_id}')
            return session_id
        
        # Check memory cache
        if group_folder in self.sessions:
            session_id = self.sessions[group_folder]
            
            # Update last activity
            if group_folder in self.session_metadata:
                self.session_metadata[group_folder]['last_activity'] = datetime.now().isoformat()
            
            logger.debug(f'Using cached session for {group_folder}: {session_id}')
            return session_id
        
        # Check database
        session_id = self.db.get_session(group_folder)
        if session_id:
            self.sessions[group_folder] = session_id
            
            # Load existing metadata if any
            self.session_metadata[group_folder] = {
                'loaded_from_db': True,
                'loaded_at': datetime.now().isoformat()
            }
            
            logger.info(f'Loaded existing session from DB for {group_folder}: {session_id}')
            return session_id
        
        # Create new session
        session_id = self._generate_session_id(group_folder)
        self.sessions[group_folder] = session_id
        self.db.set_session(group_folder, session_id)
        
        # Initialize metadata
        self.session_metadata[group_folder] = {
            'created_at': datetime.now().isoformat(),
            'created_by': 'auto',
            'message_count': 0
        }
        
        logger.info(f'Created new session for {group_folder}: {session_id}')
        return session_id
    
    def get_session_info(self, group_folder: str) -> Dict[str, Any]:
        """Get detailed information about a session"""
        session_id = self.ensure_session(group_folder)
        
        # Get session stats from database
        stats = {}
        try:
            db_stats = self.db.get_session_stats(group_folder)
            if db_stats and 'error' not in db_stats:
                stats = db_stats
        except Exception as e:
            logger.debug(f'Could not get session stats: {e}')
        
        return {
            'session_id': session_id,
            'group_folder': group_folder,
            'metadata': self.session_metadata.get(group_folder, {}),
            'stats': stats,
            'in_memory': group_folder in self.sessions,
            'in_database': session_id is not None
        }
    
    def reset_session(self, group_folder: str) -> str:
        """Reset session for a group (start fresh)"""
        # Generate new session ID
        new_session_id = self._generate_session_id(group_folder)
        
        # Update memory
        self.sessions[group_folder] = new_session_id
        
        # Update database
        self.db.set_session(group_folder, new_session_id)
        
        # Update metadata
        self.session_metadata[group_folder] = {
            'created_at': datetime.now().isoformat(),
            'reset_from': self.session_metadata.get(group_folder, {}).get('session_id'),
            'reset_reason': 'manual_reset'
        }
        
        logger.info(f'Reset session for {group_folder}: {new_session_id}')
        return new_session_id
    
    def delete_session(self, group_folder: str) -> bool:
        """Delete session for a group"""
        if group_folder in self.sessions:
            del self.sessions[group_folder]
        
        if group_folder in self.session_metadata:
            del self.session_metadata[group_folder]
        
        # Delete from database
        try:
            self.db.delete_session(group_folder)
            logger.info(f'Deleted session for {group_folder}')
            return True
        except Exception as e:
            logger.error(f'Failed to delete session for {group_folder}: {e}')
            return False
    
    def list_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """List all active sessions"""
        result = {}
        
        # Get all registered groups
        for jid, group in self.registered_groups.items():
            folder = group.folder
            session_info = self.get_session_info(folder)
            result[folder] = {
                'jid': jid,
                'name': group.name,
                **session_info
            }
        
        return result
    
    # ========================================================================
    # Channel Management
    # ========================================================================
    
    async def _init_channels(self):
        """Initialize channels from environment variables."""
        logger.info("Initializing channels from environment variables...")
        
        async def on_message(chat_id: str, msg: NewMessage):
            self.db.store_message(msg)
        
        async def on_chat_metadata(
            chat_id: str,
            timestamp: str,
            name: Optional[str] = None,
            channel: Optional[str] = None,
            is_group: Optional[bool] = None
        ):
            base_name = chat_id
            register_folder = re.sub(r'[^a-zA-Z0-9_]', '_', base_name.lower())
            register_folder = register_folder[:50]
            register_trigger = f'@{ASSISTANT_NAME}'
            register_requires_trigger = is_group

            group = RegisteredGroup(
                name=name or chat_id,
                folder=register_folder,
                trigger=register_trigger,
                added_at=datetime.now().isoformat(),
                requiresTrigger=register_requires_trigger,
                preferred_channel=channel,
                allowed_channels=[channel] if channel else None,
            )
            self.register_group(chat_id, group)
            logger.info(f"Group auto-registered: {chat_id} as {register_folder}")

            self.db.store_chat_metadata(chat_id, timestamp, name, channel, is_group)
        
        channel_opts = {
            'on_message': on_message,
            'on_chat_metadata': on_chat_metadata,
            'registered_groups': lambda: self.registered_groups
        }
        
        # Use ChannelFactory to create channels
        from .channels import ChannelFactory
        factory = ChannelFactory()
        self.channels = factory.create_channels(**channel_opts)
        
        # Connect all enabled channels
        for channel in self.channels:
            try:
                await channel.connect()
                logger.info(f"Channel {channel.__class__.__name__} connected")
            except Exception as e:
                logger.error(f"Failed to connect channel {channel.__class__.__name__}: {e}")
        
        if not self.channels:
            logger.warning(
                "No channels enabled! Set WHATSAPP_ENABLED=true or TELEGRAM_ENABLED=true "
                "in environment variables"
            )
    
    # ========================================================================
    # State Management
    # ========================================================================
    
    def load_state(self) -> None:
        """Load state from database"""
        self.last_timestamp = self.db.get_router_state('last_timestamp') or ''
        
        agent_ts = self.db.get_router_state('last_agent_timestamp')
        try:
            self.last_agent_timestamp = json.loads(agent_ts) if agent_ts else {}
        except Exception as e:
            logger.warn('Corrupted last_agent_timestamp in DB, resetting')
            self.last_agent_timestamp = {}
        
        # Load sessions from database
        self.sessions = self.db.get_all_sessions()
        logger.info(f'Loaded {len(self.sessions)} sessions from database')
        
        # Log sessions for debugging
        for folder, session_id in self.sessions.items():
            logger.debug(f'  Session: {folder} -> {session_id}')
        
        self.registered_groups = self.db.get_all_registered_groups()
        logger.info(f'State loaded: {len(self.registered_groups)} groups')
    
    def save_state(self) -> None:
        """Save state to database"""
        self.db.set_router_state('last_timestamp', self.last_timestamp)
        self.db.set_router_state('last_agent_timestamp', json.dumps(self.last_agent_timestamp))
        # Sessions are saved immediately when created/updated
    
    # ========================================================================
    # Group Management
    # ========================================================================
    
    def register_group(self, jid: str, group: RegisteredGroup) -> None:
        """Register a new group"""
        existing = self.db.get_registered_group(jid)
        if existing:
            if not self.registered_groups.get(jid):
                all_grps = self.db.get_all_registered_groups()
                self.registered_groups.update(all_grps)
            return

        try:
            group_dir = self.group_folder_resolver.resolve_group_folder(group.folder)
        except Exception as e:
            logger.warn(f'Rejecting group registration with invalid folder: {group.folder} - {e}')
            return
        
        self.registered_groups[jid] = group
        self.db.set_registered_group(jid, group)
        
        # Create group folder
        (group_dir / 'logs').mkdir(parents=True, exist_ok=True)
        
        # Ensure session exists for this new group
        self.ensure_session(group.folder)
        
        logger.info(f'Group registered: {jid} ({group.name}) as {group.folder}')
    
    def get_available_groups(self) -> List[Dict[str, Any]]:
        """Get available groups for agent"""
        chats = self.db.get_all_chats()
        registered_jids = set(self.registered_groups.keys())
        
        result = []
        for c in chats:
            if c['jid'] != '__group_sync__' and c.get('is_group'):
                result.append({
                    'jid': c['jid'],
                    'name': c['name'],
                    'lastActivity': c['last_message_time'],
                    'isRegistered': c['jid'] in registered_jids
                })
        
        return result
    
    # ========================================================================
    # Message Processing
    # ========================================================================
    
    async def _handle_idle_timeout(self, chat_id: str, idle_event: asyncio.Event) -> None:
        """Handle idle timeout for a group"""
        try:
            await asyncio.sleep(IDLE_TIMEOUT / 1000)
            if not idle_event.is_set():
                logger.debug(f'Idle timeout for {chat_id}, closing stdin')
                self.queue.close_stdin(chat_id)
        except asyncio.CancelledError:
            pass
    
    async def process_group_messages(self, chat_id: str) -> bool:
        """Process all pending messages for a group"""
        group = self.registered_groups.get(chat_id)
        if not group:
            return True
        
        channel = self.channel_router.find_channel(self.channels, chat_id)
        if not channel:
            logger.warn(f'No channel owns JID {chat_id}, skipping messages')
            return True
        
        is_main_group = group.folder == MAIN_GROUP_FOLDER
        
        since = self.last_agent_timestamp.get(chat_id, '')
        missed = self.db.get_messages_since(chat_id, since, ASSISTANT_NAME)
        
        if not missed:
            return True
        
        # Check trigger for non-main groups
        if not is_main_group and group.requiresTrigger is not False:
            has_trigger = any(TRIGGER_PATTERN.match(m.content.strip()) for m in missed)
            if not has_trigger:
                return True
        
        prompt = self.message_formatter.format_messages(missed)
        
        # Advance cursor
        #previous_cursor = self.last_agent_timestamp.get(chat_id, '')
        #self.last_agent_timestamp[chat_id] = missed[-1].timestamp
        self.save_state()
        
        logger.info(f'Processing {len(missed)} messages for group {group.name}')
        
        # Idle timer
        idle_timer: Optional[asyncio.Task] = None
        idle_event = asyncio.Event()
        
        async def reset_idle():
            nonlocal idle_timer
            if idle_timer:
                idle_timer.cancel()
            idle_timer = asyncio.create_task(
                self._handle_idle_timeout(chat_id, idle_event)
            )
        
        if hasattr(channel, 'set_typing') and channel.set_typing:
            await channel.set_typing(chat_id, True)
        
        had_error = False
        output_sent = False
        
        async def on_output(result: ContainerOutput):
            nonlocal output_sent, had_error
            if result.result:
                raw = result.result if isinstance(result.result, str) else json.dumps(result.result)
                text = self.outbound_formatter.format(raw)
                logger.info(f'Agent output for {group.name}: {raw[:200]}')
                if text:
                    await channel.send_message(chat_id, text)
                    output_sent = True
                await reset_idle()
            
            if result.status == 'success':
                self.queue.notify_idle(chat_id)
            
            if result.status == 'error':
                had_error = True
        
        output = await self._run_agent(group, prompt, chat_id, on_output)
        
        if hasattr(channel, 'set_typing') and channel.set_typing:
            await channel.set_typing(chat_id, False)
        
        if idle_timer:
            idle_timer.cancel()
        
        if output == 'error' or had_error:
            if output_sent:
                logger.warn(f'Agent error after output sent for {group.name}, skipping cursor rollback')
                return True
            
            # Roll back cursor
            #self.last_agent_timestamp[chat_id] = previous_cursor
            self.save_state()
            logger.warn(f'Agent error, rolled back cursor for {group.name}')
            return False
        
        return True
    
    async def _run_agent(
        self,
        group: RegisteredGroup,
        prompt: str,
        chat_id: str,
        on_output: Optional[Callable] = None
    ) -> str:
        """
        Run agent in container.
        
        Session ID is managed here - generated/retrieved before container starts,
        passed to container, and never expected to be returned.
        """
        is_main = group.folder == MAIN_GROUP_FOLDER
        
        # Get or create session ID - this is the key improvement
        session_id = self.ensure_session(group.folder)
        
        logger.info(
            f'Running agent for group {group.name} ({group.folder}) '
            f'with session: {session_id}'
        )
        
        # Update tasks snapshot
        tasks = self.db.get_all_tasks()
        self.snapshot_writer.write_tasks_snapshot(
            group.folder,
            is_main,
            [{
                'id': t.id,
                'groupFolder': t.group_folder,
                'prompt': t.prompt,
                'schedule_type': t.schedule_type.value if hasattr(t.schedule_type, 'value') else t.schedule_type,
                'schedule_value': t.schedule_value,
                'status': t.status.value if hasattr(t.status, 'value') else t.status,
                'next_run': t.next_run
            } for t in tasks]
        )
        
        # Update groups snapshot
        available = self.get_available_groups()
        self.snapshot_writer.write_groups_snapshot(
            group.folder,
            is_main,
            available,
            set(self.registered_groups.keys())
        )
        
        try:
            output = await self.container_runner.run_agent(
                group,
                ContainerInput(
                    prompt=prompt,
                    sessionId=session_id,  # Pass session ID directly
                    groupFolder=group.folder,
                    chatJid=chat_id,
                    isMain=is_main,
                    assistantName=ASSISTANT_NAME
                ),
                lambda proc, name: self.queue.register_process(chat_id, proc, name, group.folder),
                on_output
            )
            
            # No need to process newSessionId from container
            # Session ID is already managed by the main app
            
            if output.status == 'error':
                logger.error(f'Container agent error for {group.name}: {output.error}')
                return 'error'
            
            # Update session activity timestamp
            if group.folder in self.session_metadata:
                self.session_metadata[group.folder]['last_activity'] = datetime.now().isoformat()
                if 'message_count' in self.session_metadata[group.folder]:
                    self.session_metadata[group.folder]['message_count'] += 1
            
            logger.info(f'Agent completed successfully for {group.name}')
            return 'success'
            
        except Exception as e:
            logger.error(f'Agent error for {group.name}: {e}')
            return 'error'
    
    # ========================================================================
    # Main Loop
    # ========================================================================
    
    async def _message_loop(self) -> None:
        """Main message processing loop"""
        if self.message_loop_running:
            logger.debug('Message loop already running')
            return
        
        self.message_loop_running = True
        logger.info(f'NanoClaw running (trigger: @{ASSISTANT_NAME})')
        
        while not self._shutdown_event.is_set():
            try:
                jids = list(self.registered_groups.keys())
                for jid in jids:
                    #last_ts = self.last_agent_timestamp.get(jid, '0')
                    #logger.debug(f"Checking JID {jid} with last_ts={last_ts}")
                    messages, new_timestamp = self.db.get_new_messages(
                        [jid], self.last_agent_timestamp.get(jid, '0'), ASSISTANT_NAME
                    )
                    #logger.debug(f"Found {len(messages)} new messages for {jid}")
                    
                    if messages:
                        logger.info(f'New messages: {len(messages)}')
                        
                        self.last_timestamp = new_timestamp
                        self.save_state()
                        
                        # Group by chat
                        by_group = {}
                        for msg in messages:
                            if msg.chat_id not in by_group:
                                by_group[msg.chat_id] = []
                            by_group[msg.chat_id].append(msg)
                        
                        for chat_id, group_msgs in by_group.items():
                            group = self.registered_groups.get(chat_id)
                            if not group:
                                logger.info(f'Group not registered: {group}')
                                continue
                            
                            channel = self.channel_router.find_channel(self.channels, chat_id)
                            if not channel:
                                logger.warn(f'No channel for {chat_id}, skipping')
                                continue
                            
                            is_main_group = group.folder == MAIN_GROUP_FOLDER
                            needs_trigger = not is_main_group and group.requiresTrigger is not False
                            is_system_message = any(
                                msg.sender_id == 'system' for msg in group_msgs
                            )
 
                            
                            if needs_trigger and not is_system_message:
                                has_trigger = any(
                                    TRIGGER_PATTERN.match(m.content.strip())
                                    for m in group_msgs if m.sender_id != 'system'
                                )
                                if not has_trigger:
                                    self.last_agent_timestamp[chat_id] = group_msgs[-1].timestamp
                                    continue
                            
                            group_atts = []
                            for msg in group_msgs:
                                if msg.attachments:
                                    group_atts.extend(msg.attachments)
                           
                            formatted = self.message_formatter.format_messages(group_msgs)
                            if self.queue.send_message(chat_id, formatted, attachments=group_atts):
                                self.last_agent_timestamp[chat_id] = group_msgs[-1].timestamp
                                self.save_state()
                                logger.info(f'Updated cursor for {chat_id} to {group_msgs[-1].timestamp}')
                            else:
                                self.queue.enqueue_message_check(chat_id)

                        
                            # 设置输入状态
                            if hasattr(channel, 'set_typing') and channel.set_typing:
                                try:
                                    await channel.set_typing(chat_id, True)
                                except Exception as e:
                                    logger.warn(f'Failed to set typing for {chat_id}: {e}')
 
                    
                    await asyncio.sleep(POLL_INTERVAL / 1000)
                
            except Exception as e:
                logger.error(f'Error in message loop: {e}')
                await asyncio.sleep(POLL_INTERVAL / 1000)
    
    def recover_pending_messages(self) -> None:
        """Recover unprocessed messages after crash"""
        for chat_id, group in self.registered_groups.items():
            since = self.last_agent_timestamp.get(chat_id, '')
            pending = self.db.get_messages_since(chat_id, since, ASSISTANT_NAME)
            if pending:
                logger.info(f'Recovery: found {len(pending)} unprocessed messages for {group.name}')
                self.queue.enqueue_message_check(chat_id)
    
    # ========================================================================
    # IPC and Scheduler Dependencies
    # ========================================================================
    
    def _get_channels_info(self) -> List[Dict[str, Any]]:
        """Get information about all channels"""
        channels_info = []
        for channel in self.channels:
            channel_info = {
                'name': channel.name,
                'type': channel.__class__.__name__,
                'connected': channel.is_connected if hasattr(channel, 'is_connected') else False,
                'features': {
                    'typing': hasattr(channel, 'set_typing'),
                    'read_receipts': hasattr(channel, 'mark_as_read'),
                    'sync': hasattr(channel, 'sync_group_metadata'),
                    'reactions': hasattr(channel, 'add_reaction'),
                    'edit': hasattr(channel, 'edit_message'),
                    'delete': hasattr(channel, 'delete_message')
                }
            }
            channels_info.append(channel_info)
        return channels_info
    
    def _create_ipc_deps(self) -> IpcDeps:
        """Create IPC dependencies with multi-channel support"""
        
        async def send_message(jid: str, text: str, channel_name: Optional[str] = None) -> None:
            """Send message to specified channel or auto-detect"""
            if channel_name:
                # Send to specific channel
                channel = next((ch for ch in self.channels if ch.name == channel_name), None)
                if channel:
                    await channel.send_message(jid, text)
                else:
                    logger.error(f"Channel not found: {channel_name}")
            else:
                # Auto-detect
                channel = self.channel_router.find_channel(self.channels, jid)
                if channel:
                    await channel.send_message(jid, text)
                else:
                    logger.error(f"No channel found for JID: {jid}")
        
        async def sync_metadata(force: bool = False, channel_name: Optional[str] = None) -> None:
            """Sync metadata for all or specific channel"""
            channels_to_sync = []
            if channel_name:
                channel = next((ch for ch in self.channels if ch.name == channel_name), None)
                if channel:
                    channels_to_sync = [channel]
            else:
                channels_to_sync = self.channels
            
            for channel in channels_to_sync:
                if hasattr(channel, 'sync_group_metadata'):
                    try:
                        await channel.sync_group_metadata(force)
                    except Exception as e:
                        logger.error(f"Failed to sync metadata for {channel.name}: {e}")
        
        return IpcDeps(
            send_message=send_message,
            registered_groups=lambda: self.registered_groups,
            register_group=self.register_group,
            sync_group_metadata=sync_metadata,
            get_available_groups=self.get_available_groups,
            write_groups_snapshot=self.snapshot_writer.write_groups_snapshot,
            get_channels_info=self._get_channels_info
        )
    
    def _create_scheduler_deps(self) -> SchedulerDeps:
        """Create scheduler dependencies with pure multi-channel support"""
        
        async def send_message_to_chat(jid: str, text: str, channel_name: Optional[str] = None) -> None:
            """Send message to appropriate channel"""
            if channel_name:
                channel = next((ch for ch in self.channels if ch.name == channel_name), None)
                if not channel:
                    logger.error(f"Channel not found: {channel_name}")
                    return
                try:
                    await channel.send_message(jid, text)
                    logger.debug(f"Message sent via channel {channel_name} to {jid}")
                    return
                except Exception as e:
                    logger.error(f"Failed to send via channel {channel_name}: {e}")
                    return
            
            channel = self.channel_router.find_channel(self.channels, jid)
            if not channel:
                logger.error(f"No channel found for JID: {jid}")
                return
            
            try:
                await channel.send_message(jid, text)
                logger.debug(f"Message sent via channel {channel.name} to {jid}")
            except Exception as e:
                logger.error(f"Failed to send via channel {channel.name}: {e}")
        
        return SchedulerDeps(
            registered_groups=lambda: self.registered_groups,
            get_sessions=lambda: self.sessions,  # Provide sessions dictionary
            queue=self.queue,
            on_process=lambda jid, proc, name, folder: self.queue.register_process(jid, proc, name, folder),
            send_message=send_message_to_chat,
            get_channels_info=self._get_channels_info
        )
    
    # ========================================================================
    # Shutdown and Cleanup
    # ========================================================================
    
    async def shutdown(self, signal_name: str) -> None:
        """Graceful shutdown"""
        logger.info(f'Shutdown signal received: {signal_name}')
        self._shutdown_event.set()
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        await self.queue.shutdown(10000)
        
        for ch in self.channels:
            try:
                await ch.disconnect()
                logger.info(f"Channel {ch.__class__.__name__} disconnected")
            except Exception as e:
                logger.warning(f"Error disconnecting {ch.__class__.__name__}: {e}")

        # Log session summary before shutdown
        active_sessions = len(self.sessions)
        logger.info(f'Shutting down with {active_sessions} active sessions')
        
        # Close database connection
        self.db.close()
        logger.info("Database connection closed")
    
    # ========================================================================
    # Main Run Method
    # ========================================================================
    
    async def run(self):
        """Main entry point to run the application"""
        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self.shutdown(s.name))
            )
        
        try:
            # Initialize
            self.container_runtime.ensure_running()
            self.container_runtime.cleanup_orphans()
            logger.info('Database initialized')
            
            self.load_state()
            
            # Setup channels
            await self._init_channels()
            logger.info(f"Initialized {len(self.channels)} channels")
            
            # Create dependencies
            ipc_deps = self._create_ipc_deps()
            scheduler_deps = self._create_scheduler_deps()
            
            # Create subsystem runners
            ipc_watcher = IpcWatcher(ipc_deps)
            scheduler_loop = SchedulerLoop(scheduler_deps)
            
            # Start subsystems
            self._tasks.extend([
                asyncio.create_task(scheduler_loop.start()),
                asyncio.create_task(ipc_watcher.start())
            ])
            
            self.queue.set_process_messages_fn(self.process_group_messages)
            self.recover_pending_messages()
            
            # Log session information
            session_count = len(self.sessions)
            logger.info(f'Starting main loop with {session_count} active sessions')
            if session_count > 0:
                logger.debug('Active sessions:')
                for folder, sid in list(self.sessions.items())[:5]:  # Show first 5
                    logger.debug(f'  {folder} -> {sid}')
            
            # Start main message loop
            await self._message_loop()
            
        except Exception as e:
            logger.error(f'Error in main loop: {e}')
            await self.shutdown('error')


async def main():
    """Entry point function"""
    app = NanoClawApplication()
    await app.run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Interrupted by user')
    except Exception as e:
        logger.error(f'Failed to start: {e}')
