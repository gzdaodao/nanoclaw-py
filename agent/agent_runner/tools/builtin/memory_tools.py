# agents/tools/builtin/memory_tools.py
"""Built-in memory tools plugin."""

import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class VectorMemory:
    """Simple vector memory using SQLite"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE,
                    value TEXT,
                    tags TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_key ON memories(key)")
    
    def save(self, key: str, value: str, tags: List[str] = None):
        """Save memory"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories (key, value, tags, created_at, updated_at)
                VALUES (?, ?, ?, COALESCE(
                    (SELECT created_at FROM memories WHERE key = ?),
                    CURRENT_TIMESTAMP
                ), CURRENT_TIMESTAMP)
            """, (key, value, json.dumps(tags or []), key))
    
    def get(self, key: str) -> Optional[Dict]:
        """Get memory by key"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT key, value, tags, created_at, updated_at FROM memories WHERE key = ?",
                (key,)
            ).fetchone()
            
            if row:
                return {
                    "key": row[0],
                    "value": row[1],
                    "tags": json.loads(row[2]),
                    "created_at": row[3],
                    "updated_at": row[4]
                }
        return None
    
    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Simple text search"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT key, value, tags, created_at, updated_at
                FROM memories
                WHERE key LIKE ? OR value LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (f'%{query}%', f'%{query}%', limit)).fetchall()
            
            return [{
                "key": r[0],
                "value": r[1],
                "tags": json.loads(r[2]),
                "created_at": r[3],
                "updated_at": r[4]
            } for r in rows]
    
    def delete(self, key: str):
        """Delete memory"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memories WHERE key = ?", (key,))


class SaveMemoryTool(BaseTool):
    """Tool to save information to memory"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="save_memory",
            description="Save important information to long-term memory",
            parameters={
                "key": {
                    "type": "string",
                    "description": "Unique key for the memory",
                    "required": True
                },
                "value": {
                    "type": "string",
                    "description": "Value to store",
                    "required": True
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization",
                    "required": False
                }
            },
            category="memory",
            required_permissions=["memory:write"],
            version="1.0.0"
        )
    
    async def execute(self, key: str, value: str, tags: List[str] = None) -> ToolResult:
        """Execute save memory"""
        try:
            if not self.context.memory_client:
                return ToolResult.fail("Memory client not available")
            
            self.context.memory_client.save(key, value, tags)
            
            return ToolResult.ok({
                "key": key,
                "tags": tags,
                "status": "saved"
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to save memory: {e}")


class SearchMemoryTool(BaseTool):
    """Tool to search memory"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="search_memory",
            description="Search through long-term memory",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Search query",
                    "required": True
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100
                }
            },
            category="memory",
            required_permissions=["memory:read"],
            version="1.0.0"
        )
    
    async def execute(self, query: str, limit: int = 10) -> ToolResult:
        """Execute search memory"""
        try:
            if not self.context.memory_client:
                return ToolResult.fail("Memory client not available")
            
            results = self.context.memory_client.search(query, limit)
            
            return ToolResult.ok({
                "query": query,
                "results": results,
                "count": len(results)
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to search memory: {e}")


class GetMemoryTool(BaseTool):
    """Tool to get memory by key"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_memory",
            description="Get a specific memory by key",
            parameters={
                "key": {
                    "type": "string",
                    "description": "Memory key",
                    "required": True
                }
            },
            category="memory",
            required_permissions=["memory:read"],
            version="1.0.0"
        )
    
    async def execute(self, key: str) -> ToolResult:
        """Execute get memory"""
        try:
            if not self.context.memory_client:
                return ToolResult.fail("Memory client not available")
            
            memory = self.context.memory_client.get(key)
            
            if memory:
                return ToolResult.ok(memory)
            else:
                return ToolResult.ok({"key": key, "found": False})
            
        except Exception as e:
            return ToolResult.fail(f"Failed to get memory: {e}")


class DeleteMemoryTool(BaseTool):
    """Tool to delete memory by key"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="delete_memory",
            description="Get a specific memory by key",
            parameters={
                "key": {
                    "type": "string",
                    "description": "Memory key",
                    "required": True
                }
            },
            category="memory",
            required_permissions=["memory:delete"],
            version="1.0.0"
        )
    
    async def execute(self, key: str) -> ToolResult:
        """Execute get memory"""
        try:
            if not self.context.memory_client:
                return ToolResult.fail("Memory client not available")
            
            self.context.memory_client.delete(key)
            return ToolResult.ok(f'Memory:{key} deleted.')
            
        except Exception as e:
            return ToolResult.fail(f"Failed to get memory: {e}")


class MemoryToolsPlugin(ToolPlugin):
    """Plugin providing memory tools"""
    
    def __init__(self):
        super().__init__()
        self.memory_client: Optional[VectorMemory] = None
  
    @property
    def name(self):
        return "memory"

    @property
    def version(self):
        return "1.0.0"
    

   
    async def initialize(self, context: ToolContext) -> None:
        """Initialize memory tools plugin"""
        memory_dir = context.workspace_dir / '.memory'
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        self.memory_client = VectorMemory(memory_dir / 'memory.db')
        
        # Update context with memory client
        context.memory_client = self.memory_client
        
        self.register_tool(SaveMemoryTool(context))
        self.register_tool(DeleteMemoryTool(context))
        self.register_tool(SearchMemoryTool(context))
        self.register_tool(GetMemoryTool(context))
        
        logger.info(f"Memory tools plugin initialized with {len(self._tools)} tools")
