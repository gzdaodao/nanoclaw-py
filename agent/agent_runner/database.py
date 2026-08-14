# agents/database.py
"""Database management for agent conversations."""

import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from contextlib import contextmanager
import threading
import os

# Import only from models to avoid circular import
from .models import AgentMessage, AgentResponse


class ConversationDatabase:
    """SQLite database for storing conversation history"""
    
    def __init__(self, db_path: Union[str, Path], auto_init: bool = True):
        """
        Initialize database connection
        
        Args:
            db_path: Path to SQLite database file
            auto_init: Whether to automatically initialize tables
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._initialized = False
        
        if auto_init:
            self._init_tables()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with context manager"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_tables(self):
        """Initialize database tables"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    group_folder TEXT NOT NULL,
                    chat_id TEXT,
                    assistant_name TEXT,
                    is_main INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            # Messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    name TEXT,
                    metadata TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
            """)
            
            # Indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_id 
                ON messages(session_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp 
                ON messages(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_group_folder 
                ON sessions(group_folder)
            """)
            
            # Create trigger for updated_at
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_sessions_timestamp 
                AFTER UPDATE ON sessions
                BEGIN
                    UPDATE sessions SET updated_at = CURRENT_TIMESTAMP 
                    WHERE session_id = NEW.session_id;
                END
            """)
            
            self._initialized = True
    
    def ensure_initialized(self):
        """Ensure database is initialized"""
        if not self._initialized:
            self._init_tables()
    
    def create_session(
        self,
        session_id: str,
        group_folder: str,
        chat_id: Optional[str] = None,
        assistant_name: str = "Assistant",
        is_main: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Create a new conversation session
        
        Returns:
            True if created successfully, False if session already exists
        """
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if session exists
            cursor.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            if cursor.fetchone():
                return False
            
            cursor.execute("""
                INSERT INTO sessions 
                (session_id, group_folder, chat_id, assistant_name, is_main, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                group_folder,
                chat_id,
                assistant_name,
                1 if is_main else 0,
                json.dumps(metadata) if metadata else None
            ))
            return True
    
    def get_or_create_session(
        self,
        session_id: str,
        group_folder: str,
        chat_id: Optional[str] = None,
        assistant_name: str = "Assistant",
        is_main: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Get existing session or create new one
        
        Returns:
            True if session exists or was created
        """
        if self.get_session(session_id):
            return True
        return self.create_session(session_id, group_folder, chat_id, assistant_name, is_main, metadata)
    
    def save_message(self, session_id: str, message: AgentMessage) -> int:
        """
        Save a single message to database
        
        Returns:
            Message ID
        """
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO messages 
                (session_id, role, content, timestamp, tool_calls, tool_call_id, name, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                message.role,
                message.content,
                message.timestamp.isoformat(),
                json.dumps(message.tool_calls) if message.tool_calls else None,
                message.tool_call_id,
                message.name,
                json.dumps(message.metadata) if message.metadata else None
            ))
            
            # Update session's updated_at
            cursor.execute("""
                UPDATE sessions SET updated_at = CURRENT_TIMESTAMP 
                WHERE session_id = ?
            """, (session_id,))
            
            return cursor.lastrowid
    
    def save_messages(self, session_id: str, messages: List[AgentMessage]) -> List[int]:
        """
        Save multiple messages to database
        
        Returns:
            List of message IDs
        """
        ids = []
        for message in messages:
            msg_id = self.save_message(session_id, message)
            ids.append(msg_id)
        return ids
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information"""
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            result = dict(row)
            if result.get('metadata'):
                result['metadata'] = json.loads(result['metadata'])
            if result.get('created_at'):
                result['created_at'] = datetime.fromisoformat(result['created_at'])
            if result.get('updated_at'):
                result['updated_at'] = datetime.fromisoformat(result['updated_at'])
            return result
    
    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
        role: Optional[str] = None
    ) -> List[AgentMessage]:
        """
        Get messages from a session
        
        Args:
            session_id: Session ID
            limit: Maximum number of messages to return
            offset: Number of messages to skip
            role: Filter by role (optional)
        """
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM messages WHERE session_id = ?"
            params = [session_id]
            
            if role:
                query += " AND role = ?"
                params.append(role)
            
            query += " ORDER BY timestamp ASC"
            
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            
            cursor.execute(query, params)
            
            messages = []
            for row in cursor.fetchall():
                message = AgentMessage(
                    role=row['role'],
                    content=row['content'],
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                    tool_calls=json.loads(row['tool_calls']) if row['tool_calls'] else None,
                    tool_call_id=row['tool_call_id'],
                    name=row['name']
                )
                messages.append(message)
            
            return messages
    
    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 100
    ) -> List[AgentMessage]:
        """Get most recent messages from a session"""
        return self.get_messages(session_id, limit=limit)
    
    def get_conversation_history(
        self,
        session_id: str,
        max_tokens: Optional[int] = None
    ) -> List[AgentMessage]:
        """
        Get conversation history, optionally limiting by token count
        (Simplified version - you may want to use a proper tokenizer)
        """
        messages = self.get_messages(session_id)
        
        if max_tokens is None:
            return messages
        
        # Simple token count approximation (words + punctuation)
        total_tokens = 0
        result = []
        for msg in reversed(messages):
            # Approximate token count (1 token ≈ 4 characters)
            msg_tokens = len(msg.content) // 4
            if total_tokens + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            total_tokens += msg_tokens
        
        return result
    
    def list_sessions(
        self,
        group_folder: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List all sessions"""
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT s.*, COUNT(m.id) as message_count 
                FROM sessions s
                LEFT JOIN messages m ON s.session_id = m.session_id
            """
            params = []
            
            if group_folder:
                query += " WHERE s.group_folder = ?"
                params.append(group_folder)
            
            query += " GROUP BY s.session_id ORDER BY s.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            
            sessions = []
            for row in cursor.fetchall():
                session = dict(row)
                if session.get('metadata'):
                    session['metadata'] = json.loads(session['metadata'])
                if session.get('created_at'):
                    session['created_at'] = datetime.fromisoformat(session['created_at'])
                if session.get('updated_at'):
                    session['updated_at'] = datetime.fromisoformat(session['updated_at'])
                sessions.append(session)
            
            return sessions
    
    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and all its messages
        
        Returns:
            True if deleted, False if session not found
        """
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            return cursor.rowcount > 0
    
    def delete_messages(
        self,
        session_id: str,
        before_timestamp: Optional[datetime] = None,
        role: Optional[str] = None,
        except_role: Optional[str] = None
    ) -> int:
        """
        Delete messages from a session
        
        Returns:
            Number of messages deleted
        """
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = "DELETE FROM messages WHERE session_id = ?"
            params = [session_id]
            
            if before_timestamp:
                query += " AND timestamp < ?"
                params.append(before_timestamp.isoformat())
            
            if role:
                query += " AND role = ?"
                params.append(role)

            if except_role:
                query += " AND role != ?"
                params.append(except_role)

            
            cursor.execute(query, params)
            deleted = cursor.rowcount
            
            # Update session's updated_at if any messages were deleted
            if deleted > 0:
                cursor.execute("""
                    UPDATE sessions SET updated_at = CURRENT_TIMESTAMP 
                    WHERE session_id = ?
                """, (session_id,))
            
            return deleted
    
    def search_messages(
        self,
        query: str,
        group_folder: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search messages by content"""
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            sql = """
                SELECT m.*, s.group_folder, s.chat_id, s.assistant_name
                FROM messages m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE m.content LIKE ?
            """
            params = [f"%{query}%"]
            
            if group_folder:
                sql += " AND s.group_folder = ?"
                params.append(group_folder)
            
            sql += " ORDER BY m.timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['metadata'] = json.loads(result['metadata']) if result['metadata'] else {}
                result['tool_calls'] = json.loads(result['tool_calls']) if result['tool_calls'] else None
                results.append(result)
            
            return results
    
    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """Get statistics for a session"""
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Total messages count
            cursor.execute(
                "SELECT COUNT(*) as total FROM messages WHERE session_id = ?",
                (session_id,)
            )
            total = cursor.fetchone()['total']
            
            # Messages by role
            cursor.execute("""
                SELECT role, COUNT(*) as count 
                FROM messages 
                WHERE session_id = ? 
                GROUP BY role
            """, (session_id,))
            by_role = {row['role']: row['count'] for row in cursor.fetchall()}
            
            # First and last message timestamps
            cursor.execute("""
                SELECT MIN(timestamp) as first, MAX(timestamp) as last
                FROM messages
                WHERE session_id = ?
            """, (session_id,))
            timestamps = cursor.fetchone()
            
            return {
                "session_id": session_id,
                "total_messages": total,
                "messages_by_role": by_role,
                "first_message": timestamps['first'],
                "last_message": timestamps['last']
            }
    
    def export_session(
        self,
        session_id: str,
        format: str = "json"
    ) -> Dict[str, Any]:
        """Export a session in various formats"""
        session = self.get_session(session_id)
        if not session:
            return {"error": "Session not found"}
        
        messages = self.get_messages(session_id)
        
        if format == "json":
            return {
                "session": session,
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat(),
                        "metadata": msg.metadata,
                        "tool_calls": msg.tool_calls
                    }
                    for msg in messages
                ]
            }
        elif format == "text":
            # Return as text format
            text = f"Session: {session_id}\n"
            text += f"Group: {session.get('group_folder')}\n"
            text += f"Assistant: {session.get('assistant_name')}\n"
            text += f"Created: {session.get('created_at')}\n"
            text += "=" * 50 + "\n\n"
            
            for msg in messages:
                text += f"[{msg.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {msg.role.upper()}: {msg.content}\n\n"
            
            return {"text": text}
        
        return {"error": f"Unsupported format: {format}"}
    
    def vacuum(self) -> None:
        """Optimize database by reclaiming unused space"""
        self.ensure_initialized()
        
        with self._get_connection() as conn:
            conn.execute("VACUUM")
    
    def backup(self, backup_path: Union[str, Path]) -> None:
        """Create a backup of the database"""
        backup_path = Path(backup_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._get_connection() as conn:
            backup_conn = sqlite3.connect(str(backup_path))
            conn.backup(backup_conn)
            backup_conn.close()


# Singleton instance for global database access
_default_db: Optional[ConversationDatabase] = None


def init_default_database(db_path: Union[str, Path], auto_init: bool = True) -> ConversationDatabase:
    """Initialize the default database instance"""
    global _default_db
    _default_db = ConversationDatabase(db_path, auto_init=auto_init)
    return _default_db


def get_default_database() -> Optional[ConversationDatabase]:
    """Get the default database instance"""
    return _default_db
