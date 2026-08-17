# db.py - 更新版本，添加连接管理
import json
import sqlite3
import atexit
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Generator, Union

from .config import ASSISTANT_NAME, DATA_DIR, STORE_DIR
from .group_folder import GroupFolderValidator
from .logger import logger
from .dtypes import NewMessage, RegisteredGroup, ScheduledTask, TaskRunLog, TaskStatus, ScheduleType
from traceback import format_exc
import re

class Database:
    """Database manager for NanoClaw"""
    
    def __init__(self, db_path: Optional[Path] = None, auto_close: bool = True):
        """Initialize database manager
        
        Args:
            db_path: Path to database file
            auto_close: Whether to register atexit handler to close connection
        """
        self._db: Optional[sqlite3.Connection] = None
        self.db_path = db_path or (STORE_DIR / 'messages.db')
        self.folder_validator = GroupFolderValidator()
        self._transaction_depth = 0
        self._ensure_db_path()
        
        if auto_close:
            atexit.register(self.close)
    
    def _ensure_db_path(self) -> None:
        """Ensure database directory exists"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection (cached)"""
        if self._db is None:
            self._db = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,  # Wait up to 10 seconds for lock
                check_same_thread=False  # Allow use across threads (with caution)
            )
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging for better concurrency
            self._create_schema()
            self._migrate_json_state()
            logger.debug(f"Database connection opened: {self.db_path}")
        return self._db
    
    def close(self) -> None:
        """Close database connection"""
        if self._db is not None:
            try:
                # Rollback any pending transaction
                if self._transaction_depth > 0:
                    self._db.rollback()
                self._db.close()
                logger.debug(f"Database connection closed: {self.db_path}")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
                logger.error(format_exc())
            finally:
                self._db = None
                self._transaction_depth = 0
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection"""
        self.close()
    
    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions"""
        conn = self.get_connection()
        self._transaction_depth += 1
        try:
            yield conn
            if self._transaction_depth == 1:
                conn.commit()
        except Exception:
            logger.error(format_exc())
            if self._transaction_depth == 1:
                conn.rollback()
            raise
        finally:
            self._transaction_depth -= 1
    
    @contextmanager
    def cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for a database cursor with automatic cleanup"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
    
    def execute(self, sql: str, params: Tuple = ()) -> sqlite3.Cursor:
        """Execute a query and return cursor"""
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor
    
    def execute_in_transaction(self, sql: str, params: Tuple = ()) -> sqlite3.Cursor:
        """Execute a query within a transaction"""
        with self.transaction():
            return self.execute(sql, params)
    
    def executemany(self, sql: str, params: List[Tuple]) -> sqlite3.Cursor:
        """Execute many queries"""
        with self.cursor() as cursor:
            cursor.executemany(sql, params)
            return cursor
    
    def executemany_in_transaction(self, sql: str, params: List[Tuple]) -> sqlite3.Cursor:
        """Execute many queries within a transaction"""
        with self.transaction():
            return self.executemany(sql, params)
    
    def executescript(self, script: str) -> None:
        """Execute a script"""
        with self.transaction():
            self.get_connection().executescript(script)
    
    def _create_schema(self) -> None:
        """Create database schema"""
        self.executescript('''
            CREATE TABLE IF NOT EXISTS chats (
                jid TEXT PRIMARY KEY,
                name TEXT,
                last_message_time TEXT,
                channel TEXT,
                is_group INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT,
                chat_id TEXT,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT,
                timestamp TEXT,
                is_from_me INTEGER,
                is_bot_message INTEGER DEFAULT 0,
                PRIMARY KEY (id, chat_id),
                FOREIGN KEY (chat_id) REFERENCES chats(jid) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_is_bot ON messages(is_bot_message);
            
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                group_folder TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                schedule_value TEXT NOT NULL,
                context_mode TEXT DEFAULT 'isolated',
                next_run TEXT,
                last_run TEXT,
                last_result TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON scheduled_tasks(next_run);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_group_folder ON scheduled_tasks(group_folder);
            
            CREATE TABLE IF NOT EXISTS task_run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                run_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_run_logs(task_id);
            CREATE INDEX IF NOT EXISTS idx_task_logs_run_at ON task_run_logs(run_at);
            
            CREATE TABLE IF NOT EXISTS router_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS sessions (
                group_folder TEXT PRIMARY KEY,
                session_id TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_group_folder ON sessions(group_folder);
            
            CREATE TABLE IF NOT EXISTS registered_groups (
                jid TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                folder TEXT NOT NULL UNIQUE,
                trigger_pattern TEXT NOT NULL,
                added_at TEXT NOT NULL,
                container_config TEXT,
                requires_trigger INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_registered_groups_folder ON registered_groups(folder);
        ''')
        
        self._add_missing_columns()
    
    def _add_missing_columns(self) -> None:
        """Add missing columns to existing tables (migrations)"""
        conn = self.get_connection()
        
        # Add context_mode column if not exists
        try:
            conn.execute('ALTER TABLE scheduled_tasks ADD COLUMN context_mode TEXT DEFAULT "isolated"')
            conn.commit()
            logger.info("Added context_mode column to scheduled_tasks")
        except sqlite3.OperationalError:
            logger.error(format_exc())
            pass
        
        # Add is_bot_message column if not exists
        try:
            conn.execute('ALTER TABLE messages ADD COLUMN is_bot_message INTEGER DEFAULT 0')
            # Backfill
            conn.execute('UPDATE messages SET is_bot_message = 1 WHERE content LIKE ?', 
                        (f'{ASSISTANT_NAME}:%',))
            conn.commit()
            logger.info("Added is_bot_message column to messages")
        except sqlite3.OperationalError:
            logger.error(format_exc())
            pass
        
        # Add channel and is_group columns if not exists
        try:
            conn.execute('ALTER TABLE chats ADD COLUMN channel TEXT')
            conn.execute('ALTER TABLE chats ADD COLUMN is_group INTEGER DEFAULT 0')
            # Backfill
            conn.execute('UPDATE chats SET channel = "whatsapp", is_group = 1 WHERE jid LIKE "%@g.us"')
            conn.execute('UPDATE chats SET channel = "whatsapp", is_group = 0 WHERE jid LIKE "%@s.whatsapp.net"')
            conn.execute('UPDATE chats SET channel = "discord", is_group = 1 WHERE jid LIKE "dc:%"')
            conn.execute('UPDATE chats SET channel = "telegram", is_group = 1 WHERE jid LIKE "tg:%"')
            conn.commit()
            logger.info("Added channel and is_group columns to chats")
        except sqlite3.OperationalError:
            logger.error(format_exc())
            pass
    
    # --- Chat operations ---
    
    def store_chat_metadata(
        self,
        chat_id: str,
        timestamp: str,
        name: Optional[str] = None,
        channel: Optional[str] = None,
        is_group: Optional[bool] = None,
    ) -> None:
        """Store chat metadata"""
        ch = channel
        group = 1 if is_group else 0 if is_group is not None else None
        logger.info(f"store_chat_metadata: chat_id:{chat_id}, name:{name}, channel:{channel}")
        
        with self.transaction():
            if name:
                self.execute('''
                    INSERT INTO chats (jid, name, last_message_time, channel, is_group)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(jid) DO UPDATE SET
                        name = excluded.name,
                        last_message_time = MAX(last_message_time, excluded.last_message_time),
                        channel = COALESCE(excluded.channel, channel),
                        is_group = COALESCE(excluded.is_group, is_group)
                ''', (chat_id, name, timestamp, ch, group))
            else:
                self.execute('''
                    INSERT INTO chats (jid, name, last_message_time, channel, is_group)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(jid) DO UPDATE SET
                        last_message_time = MAX(last_message_time, excluded.last_message_time),
                        channel = COALESCE(excluded.channel, channel),
                        is_group = COALESCE(excluded.is_group, is_group)
                ''', (chat_id, chat_id, timestamp, ch, group))

   
    
    def update_chat_name(self, chat_id: str, name: str) -> None:
        """Update chat name"""
        with self.transaction():
            self.execute('''
                INSERT INTO chats (jid, name, last_message_time)
                VALUES (?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET name = excluded.name
            ''', (chat_id, name, datetime.now().isoformat()))
    
    def get_all_chats(self) -> List[Dict[str, Any]]:
        """Get all known chats"""
        with self.cursor() as cursor:
            cursor.execute('''
                SELECT jid, name, last_message_time, channel, is_group
                FROM chats
                ORDER BY last_message_time DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_last_group_sync(self) -> Optional[str]:
        """Get timestamp of last group metadata sync"""
        with self.cursor() as cursor:
            cursor.execute('SELECT last_message_time FROM chats WHERE jid = "__group_sync__"')
            row = cursor.fetchone()
            return row['last_message_time'] if row else None
    
    def set_last_group_sync(self) -> None:
        """Record that group metadata was synced"""
        now = datetime.now().isoformat()
        with self.transaction():
            self.execute(
                'INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)',
                ('__group_sync__', '__group_sync__', now)
            )
    
    # --- Message operations ---
    
    def store_message(self, msg: NewMessage) -> None:
        """Store a message"""
        logger.info(f"store_message: msg:{msg}")
        with self.transaction():
            self.execute('''
                INSERT OR REPLACE INTO messages 
                (id, chat_id, sender_id, sender_name, content, timestamp, is_from_me, is_bot_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                msg.id, msg.chat_id, msg.sender_id, msg.sender_name, msg.content,
                msg.timestamp, 1 if msg.is_from_me else 0, 1 if msg.is_bot_message else 0
            ))
    
    def store_message_direct(
        self,
        id: str,
        chat_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        timestamp: str,
        is_from_me: bool,
        is_bot_message: bool = False
    ) -> None:
        """Store a message directly"""
        self.store_message(NewMessage(
            id=id, chat_id=chat_id, sender_id=sender_id, sender_name=sender_name,
            content=content, timestamp=timestamp, is_from_me=is_from_me,
            is_bot_message=is_bot_message
        ))
    
    def get_new_messages(
        self,
        jids: List[str],
        last_timestamp: str,
        bot_prefix: str
    ) -> Tuple[List[NewMessage], str]:
        """Get new messages for multiple JIDs"""
        if not jids:
            return [], last_timestamp
        
        placeholders = ','.join(['?'] * len(jids))
        with self.cursor() as cursor:
            cursor.execute(f'''
                SELECT id, chat_id, sender_id, sender_name, content, timestamp
                FROM messages
                WHERE timestamp > ? AND chat_id IN ({placeholders})
                  AND is_bot_message = 0 AND content NOT LIKE ?
                  AND content != '' AND content IS NOT NULL
                ORDER BY timestamp
            ''', [last_timestamp] + jids + [f'{bot_prefix}:%'])
            
            messages = []
            new_timestamp = last_timestamp
            for row in cursor.fetchall():
                msg = NewMessage(**dict(row))
                messages.append(msg)
                if msg.timestamp > new_timestamp:
                    new_timestamp = msg.timestamp
            
            return messages, new_timestamp
    
    def get_messages_since(
        self,
        chat_id: str,
        since_timestamp: str,
        bot_prefix: str
    ) -> List[NewMessage]:
        """Get messages for a specific chat since timestamp"""
        if not since_timestamp:
            since_timestamp = ''
        
        with self.cursor() as cursor:
            cursor.execute('''
                SELECT id, chat_id, sender_id, sender_name, content, timestamp
                FROM messages
                WHERE chat_id = ? AND timestamp > ?
                  AND is_bot_message = 0 AND content NOT LIKE ?
                  AND content != '' AND content IS NOT NULL
                ORDER BY timestamp
            ''', (chat_id, since_timestamp, f'{bot_prefix}:%'))
            
            return [NewMessage(**dict(row)) for row in cursor.fetchall()]
    
    # --- Task operations ---
    
    def create_task(self, task: ScheduledTask) -> None:
        """Create a scheduled task"""
        with self.transaction():
            self.execute('''
                INSERT INTO scheduled_tasks (
                    id, group_folder, chat_id, prompt, schedule_type, schedule_value,
                    context_mode, next_run, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.id, task.group_folder, task.chat_id, task.prompt,
                task.schedule_type.value if isinstance(task.schedule_type, ScheduleType) else task.schedule_type,
                task.schedule_value, task.context_mode, task.next_run,
                task.status.value if isinstance(task.status, TaskStatus) else task.status,
                task.created_at
            ))
    
    def get_task_by_id(self, task_id: str) -> Optional[ScheduledTask]:
        """Get task by ID"""
        with self.cursor() as cursor:
            cursor.execute('SELECT * FROM scheduled_tasks WHERE id = ?', (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            return self._row_to_task(data)
    
    def get_tasks_for_group(self, group_folder: str) -> List[ScheduledTask]:
        """Get tasks for a group"""
        with self.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM scheduled_tasks WHERE group_folder = ? ORDER BY created_at DESC',
                (group_folder,)
            )
            return [self._row_to_task(dict(row)) for row in cursor.fetchall()]
    
    def get_all_tasks(self) -> List[ScheduledTask]:
        """Get all tasks"""
        with self.cursor() as cursor:
            cursor.execute('SELECT * FROM scheduled_tasks ORDER BY created_at DESC')
            return [self._row_to_task(dict(row)) for row in cursor.fetchall()]
    
    def _row_to_task(self, data: dict) -> ScheduledTask:
        """Convert database row to ScheduledTask"""
        # Convert string enums back to Enum if needed
        if isinstance(data.get('schedule_type'), str):
            data['schedule_type'] = ScheduleType(data['schedule_type'])
        if isinstance(data.get('status'), str):
            data['status'] = TaskStatus(data['status'])
        return ScheduledTask(**data)
    
    def update_task(self, task_id: str, **updates) -> None:
        """Update task fields"""
        if not updates:
            return
        
        fields = []
        values = []
        
        for key, value in updates.items():
            if value is not None:
                fields.append(f'{key} = ?')
                values.append(value)
        
        values.append(task_id)
        
        with self.transaction():
            self.execute(f'UPDATE scheduled_tasks SET {", ".join(fields)} WHERE id = ?', tuple(values))
    
    def delete_task(self, task_id: str) -> None:
        """Delete a task"""
        with self.transaction():
            self.execute('DELETE FROM task_run_logs WHERE task_id = ?', (task_id,))
            self.execute('DELETE FROM scheduled_tasks WHERE id = ?', (task_id,))
    
    def get_due_tasks(self) -> List[ScheduledTask]:
        """Get tasks that are due to run"""
        now = datetime.now().isoformat()
        with self.cursor() as cursor:
            cursor.execute('''
                SELECT * FROM scheduled_tasks
                WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?
                ORDER BY next_run
            ''', (now,))
            return [self._row_to_task(dict(row)) for row in cursor.fetchall()]
    
    def update_task_after_run(self, task_id: str, next_run: Optional[str], last_result: str) -> None:
        """Update task after a run"""
        now = datetime.now().isoformat()
        with self.transaction():
            self.execute('''
                UPDATE scheduled_tasks
                SET next_run = ?, last_run = ?, last_result = ?,
                    status = CASE WHEN ? IS NULL THEN 'completed' ELSE status END
                WHERE id = ?
            ''', (next_run, now, last_result, next_run, task_id))
    
    def log_task_run(self, log: TaskRunLog) -> None:
        """Log a task run"""
        with self.transaction():
            self.execute('''
                INSERT INTO task_run_logs (task_id, run_at, duration_ms, status, result, error)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (log.task_id, log.run_at, log.duration_ms, log.status, log.result, log.error))
    
    # --- Router state operations ---
    
    def get_router_state(self, key: str) -> Optional[str]:
        """Get router state value"""
        with self.cursor() as cursor:
            cursor.execute('SELECT value FROM router_state WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row['value'] if row else None
    
    def set_router_state(self, key: str, value: str) -> None:
        """Set router state value"""
        with self.transaction():
            self.execute('INSERT OR REPLACE INTO router_state (key, value) VALUES (?, ?)', (key, value))
    
    # --- Session operations ---
    
    def get_session(self, group_folder: str) -> Optional[str]:
        """Get session for group"""
        with self.cursor() as cursor:
            cursor.execute('SELECT session_id FROM sessions WHERE group_folder = ?', (group_folder,))
            row = cursor.fetchone()
            return row['session_id'] if row else None
    
    def set_session(self, group_folder: str, session_id: str) -> None:
        """Set session for group"""
        with self.transaction():
            self.execute('INSERT OR REPLACE INTO sessions (group_folder, session_id) VALUES (?, ?)',
                        (group_folder, session_id))
    
    def get_all_sessions(self) -> Dict[str, str]:
        """Get all sessions"""
        with self.cursor() as cursor:
            cursor.execute('SELECT group_folder, session_id FROM sessions')
            return {row['group_folder']: row['session_id'] for row in cursor.fetchall()}
    
    # --- Registered group operations ---
    
    def get_registered_group(self, jid: str) -> Optional[Dict[str, Any]]:
        """Get registered group by JID"""
        with self.cursor() as cursor:
            cursor.execute('SELECT * FROM registered_groups WHERE jid = ?', (jid,))
            row = cursor.fetchone()
            if not row:
                return None
            
            data = dict(row)
            if not self.folder_validator.is_valid(data['folder']):
                logger.warn(f"Skipping registered group with invalid folder: {data['folder']}")
                return None
            
            result = {
                'jid': data['jid'],
                'name': data['name'],
                'folder': data['folder'],
                'trigger': data['trigger_pattern'],
                'added_at': data['added_at'],
                'requiresTrigger': None if data['requires_trigger'] is None else bool(data['requires_trigger'])
            }
            if data['container_config']:
                config_data = json.loads(data['container_config'])
                result['containerConfig'] = json.loads(data['container_config'])
                if 'preferred_channel' in config_data:
                    result['preferred_channel'] = config_data['preferred_channel']
                if 'allowed_channels' in config_data:
                    result['allowed_channels'] = config_data['allowed_channels']
            return result
    
    def set_registered_group(self, jid: str, group: RegisteredGroup) -> None:
        """Set registered group"""
        if not self.folder_validator.is_valid(group.folder):
            raise ValueError(f'Invalid group folder "{group.folder}" for JID {jid}')
        
        with self.transaction():
            self.execute('''
                INSERT OR REPLACE INTO registered_groups
                (jid, name, folder, trigger_pattern, added_at, container_config, requires_trigger)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                jid, group.name, group.folder, group.trigger, group.added_at,
                json.dumps(group.containerConfig.__dict__) if group.containerConfig else None,
                1 if group.requiresTrigger is None or group.requiresTrigger else 0
            ))
    
    def get_all_registered_groups(self) -> Dict[str, RegisteredGroup]:
        """Get all registered groups"""
        with self.cursor() as cursor:
            cursor.execute('SELECT * FROM registered_groups')
            result = {}
            for row in cursor.fetchall():
                data = dict(row)
                if not self.folder_validator.is_valid(data['folder']):
                    logger.warn(f"Skipping registered group with invalid folder: {data['folder']}")
                    continue
                
                container_config = None
                preferred_channel = None
                allowed_channels = None

                if data['container_config']:
                    config_data = json.loads(data['container_config'])
                    from dtypes import ContainerConfig
                    container_config = ContainerConfig(**config_data)
                    if 'preferred_channel' in config_data:
                        preferred_channel = config_data['preferred_channel']

                
                result[data['jid']] = RegisteredGroup(
                    name=data['name'],
                    folder=data['folder'],
                    trigger=data['trigger_pattern'],
                    added_at=data['added_at'],
                    containerConfig=container_config,
                    requiresTrigger=None if data['requires_trigger'] is None else bool(data['requires_trigger']),
                    preferred_channel=preferred_channel,
                    allowed_channels=allowed_channels,
                )
            return result
    
    # --- JSON migration ---
    
    def _migrate_json_state(self) -> None:
        """Migrate from JSON files to database"""
        def migrate_file(filename: str) -> Optional[Any]:
            file_path = DATA_DIR / filename
            if not file_path.exists():
                return None
            try:
                data = json.loads(file_path.read_text())
                file_path.rename(f'{file_path}.migrated')
                return data
            except Exception as e:
                logger.error(format_exc())
                logger.warn(f"Failed to migrate {filename}: {e}")
                return None
        
        # Migrate router_state.json
        router_state = migrate_file('router_state.json')
        if router_state and isinstance(router_state, dict):
            with self.transaction():
                if router_state.get('last_timestamp'):
                    self.set_router_state('last_timestamp', router_state['last_timestamp'])
                if router_state.get('last_agent_timestamp'):
                    self.set_router_state('last_agent_timestamp', json.dumps(router_state['last_agent_timestamp']))
        
        # Migrate sessions.json
        sessions = migrate_file('sessions.json')
        if sessions and isinstance(sessions, dict):
            with self.transaction():
                for folder, session_id in sessions.items():
                    self.set_session(folder, session_id)
        
        # Migrate registered_groups.json
        groups = migrate_file('registered_groups.json')
        if groups and isinstance(groups, dict):
            with self.transaction():
                for jid, group_data in groups.items():
                    try:
                        from dtypes import RegisteredGroup
                        group = RegisteredGroup(**group_data)
                        self.set_registered_group(jid, group)
                    except Exception as e:
                        logger.warn(f"Skipping migrated registered group {jid}: {e}")
    
    # --- Test utilities ---
    
    def init_test_database(self) -> None:
        """Initialize in-memory database for tests"""
        self.close()  # Close any existing connection
        self._db = sqlite3.connect(':memory:')
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
    
    def vacuum(self) -> None:
        """Vacuum the database to reclaim space"""
        with self.transaction():
            self.execute("VACUUM")
        logger.info("Database vacuum completed")
    
    def backup(self, backup_path: Path) -> None:
        """Create a backup of the database"""
        import shutil
        self.close()  # Close connection before backup
        try:
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"Database backed up to {backup_path}")
        finally:
            # Reopen connection
            self.get_connection()


# Global database instance management
_db_instance: Optional[Database] = None
_db_instance_refcount = 0


def get_db() -> Database:
    """Get global database instance (with reference counting)"""
    global _db_instance, _db_instance_refcount
    if _db_instance is None:
        _db_instance = Database()
    _db_instance_refcount += 1
    return _db_instance


def release_db() -> None:
    """Release global database instance"""
    global _db_instance, _db_instance_refcount
    _db_instance_refcount -= 1
    if _db_instance_refcount <= 0 and _db_instance is not None:
        _db_instance.close()
        _db_instance = None
        _db_instance_refcount = 0


# Context manager for database usage
@contextmanager
def db_session() -> Generator[Database, None, None]:
    """Context manager for database session (auto releases)"""
    db = get_db()
    try:
        yield db
    finally:
        release_db()


# Maintain backward compatibility functions
def init_database() -> None:
    """Initialize database (backward compatibility)"""
    get_db()._create_schema()


def _init_test_database() -> None:
    """Initialize test database"""
    get_db().init_test_database()


def store_chat_metadata(chat_id: str, timestamp: str, name: Optional[str] = None,
                       channel: Optional[str] = None, is_group: Optional[bool] = None) -> None:
    get_db().store_chat_metadata(chat_id, timestamp, name, channel, is_group)


def update_chat_name(chat_id: str, name: str) -> None:
    get_db().update_chat_name(chat_id, name)


def get_all_chats() -> List[Dict[str, Any]]:
    return get_db().get_all_chats()


def get_last_group_sync() -> Optional[str]:
    return get_db().get_last_group_sync()


def set_last_group_sync() -> None:
    get_db().set_last_group_sync()


def store_message(msg: NewMessage) -> None:
    get_db().store_message(msg)


def store_message_direct(id: str, chat_id: str, sender_id: str, sender_name: str,
                        content: str, timestamp: str, is_from_me: bool,
                        is_bot_message: bool = False) -> None:
    get_db().store_message_direct(id, chat_id, sender_id, sender_name, content,
                                 timestamp, is_from_me, is_bot_message)


def get_new_messages(jids: List[str], last_timestamp: str, bot_prefix: str) -> Tuple[List[NewMessage], str]:
    return get_db().get_new_messages(jids, last_timestamp, bot_prefix)


def get_messages_since(chat_id: str, since_timestamp: str, bot_prefix: str) -> List[NewMessage]:
    return get_db().get_messages_since(chat_id, since_timestamp, bot_prefix)


def create_task(task: ScheduledTask) -> None:
    get_db().create_task(task)


def get_task_by_id(task_id: str) -> Optional[ScheduledTask]:
    return get_db().get_task_by_id(task_id)


def get_tasks_for_group(group_folder: str) -> List[ScheduledTask]:
    return get_db().get_tasks_for_group(group_folder)


def get_all_tasks() -> List[ScheduledTask]:
    return get_db().get_all_tasks()


def update_task(task_id: str, **updates) -> None:
    get_db().update_task(task_id, **updates)


def delete_task(task_id: str) -> None:
    get_db().delete_task(task_id)


def get_due_tasks() -> List[ScheduledTask]:
    return get_db().get_due_tasks()


def update_task_after_run(task_id: str, next_run: Optional[str], last_result: str) -> None:
    get_db().update_task_after_run(task_id, next_run, last_result)


def log_task_run(log: TaskRunLog) -> None:
    get_db().log_task_run(log)


def get_router_state(key: str) -> Optional[str]:
    return get_db().get_router_state(key)


def set_router_state(key: str, value: str) -> None:
    get_db().set_router_state(key, value)


def get_session(group_folder: str) -> Optional[str]:
    return get_db().get_session(group_folder)


def set_session(group_folder: str, session_id: str) -> None:
    get_db().set_session(group_folder, session_id)


def get_all_sessions() -> Dict[str, str]:
    return get_db().get_all_sessions()


def get_registered_group(jid: str) -> Optional[Dict[str, Any]]:
    return get_db().get_registered_group(jid)


def set_registered_group(jid: str, group: RegisteredGroup) -> None:
    get_db().set_registered_group(jid, group)


def get_all_registered_groups() -> Dict[str, RegisteredGroup]:
    return get_db().get_all_registered_groups()
