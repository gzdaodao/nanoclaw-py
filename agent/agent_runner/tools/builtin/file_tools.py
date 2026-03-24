# agents/tools/builtin/file_tools.py
"""Built-in file operation tools plugin."""

import aiofiles
from pathlib import Path
from typing import Optional, List
import os
import shutil

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class ReadFileTool(BaseTool):
    """Tool to read files"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="read_file",
            description="Read content from a file in the workspace",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace)",
                    "required": True
                },
                "encoding": {
                    "type": "string",
                    "description": "File encoding (default: utf-8)",
                    "enum": ["utf-8", "ascii", "latin-1"],
                    "default": "utf-8"
                }
            },
            category="filesystem",
            required_permissions=["filesystem:read"],
            version="1.0.0"
        )
    
    async def execute(self, path: str, encoding: str = "utf-8") -> ToolResult:
        """Execute read file"""
        try:
            # Security: prevent path traversal
            if '..' in path or path.startswith('/'):
                return ToolResult.fail("Invalid path: path traversal detected")
            
            full_path = self.context.workspace_dir / path
            if not full_path.exists():
                return ToolResult.fail(f"File not found: {path}")
            
            if not full_path.is_file():
                return ToolResult.fail(f"Not a file: {path}")
            
            async with aiofiles.open(full_path, 'r', encoding=encoding) as f:
                content = await f.read()
            
            return ToolResult.ok({
                "content": content,
                "path": path,
                "size": len(content),
                "encoding": encoding
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to read file: {e}")


class WriteFileTool(BaseTool):
    """Tool to write files"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="write_file",
            description="Write content to a file in the workspace",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace)",
                    "required": True
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                    "required": True
                },
                "mode": {
                    "type": "string",
                    "description": "Write mode",
                    "enum": ["write", "append"],
                    "default": "write"
                },
                "encoding": {
                    "type": "string",
                    "description": "File encoding",
                    "default": "utf-8"
                }
            },
            category="filesystem",
            required_permissions=["filesystem:write"],
            version="1.0.0"
        )
    
    async def execute(self, path: str, content: str, mode: str = "write", encoding: str = "utf-8") -> ToolResult:
        """Execute write file"""
        try:
            # Security: prevent path traversal
            if '..' in path or path.startswith('/'):
                return ToolResult.fail("Invalid path: path traversal detected")
            
            full_path = self.context.workspace_dir / path
            
            # Create parent directories if needed
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_mode = 'w' if mode == 'write' else 'a'
            async with aiofiles.open(full_path, file_mode, encoding=encoding) as f:
                await f.write(content)
            
            return ToolResult.ok({
                "path": path,
                "mode": mode,
                "size": len(content),
                "encoding": encoding
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to write file: {e}")


class ListFilesTool(BaseTool):
    """Tool to list files in directory"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_files",
            description="List files in a directory",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Directory path (relative to workspace)",
                    "default": "."
                },
                "pattern": {
                    "type": "string",
                    "description": "File pattern (e.g., '*.txt')",
                    "default": "*"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List recursively",
                    "default": False
                }
            },
            category="filesystem",
            required_permissions=["filesystem:read"],
            version="1.0.0"
        )
    
    async def execute(self, path: str = ".", pattern: str = "*", recursive: bool = False) -> ToolResult:
        """Execute list files"""
        try:
            # Security: prevent path traversal
            if '..' in path:
                return ToolResult.fail("Invalid path: path traversal detected")
            
            full_path = self.context.workspace_dir / path
            if not full_path.exists():
                return ToolResult.fail(f"Directory not found: {path}")
            
            if not full_path.is_dir():
                return ToolResult.fail(f"Not a directory: {path}")
            
            files = []
            if recursive:
                for root, dirs, filenames in os.walk(full_path):
                    rel_root = Path(root).relative_to(self.context.workspace_dir)
                    for f in filenames:
                        if Path(f).match(pattern):
                            files.append(str(rel_root / f))
            else:
                for f in full_path.glob(pattern):
                    if f.is_file():
                        files.append(str(f.relative_to(self.context.workspace_dir)))
            
            return ToolResult.ok({
                "files": files,
                "count": len(files),
                "path": path
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to list files: {e}")


class FileToolsPlugin(ToolPlugin):
    """Plugin providing file operation tools"""
    
    def __init__(self):
        super().__init__()
    
    @property
    def name(self):
        return "filesystem"

    @property
    def version(self):
        return "1.0.0"
    

    async def initialize(self, context: ToolContext) -> None:
        """Initialize file tools plugin"""
        self.register_tool(ReadFileTool(context))
        self.register_tool(WriteFileTool(context))
        self.register_tool(ListFilesTool(context))
        logger.info(f"File tools plugin initialized with {len(self._tools)} tools")
