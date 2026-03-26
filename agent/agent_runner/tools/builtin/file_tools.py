# agents/tools/builtin/file_tools.py
"""Built-in file operation tools plugin."""

import aiofiles
from pathlib import Path
from typing import Optional, List
import os
import shutil
import base64

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class PathSecurity:
    """Security utilities for paths"""
    
    @staticmethod
    def ensure_within_base(base_dir: Path, resolved_path: Path) -> None:
        """Ensure path is within base directory"""
        try:
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"File tools' Path not allow to escapes base directory: {resolved_path}")


path_security = PathSecurity()


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
                "base64_content": {
                    "type": "boolean",
                    "description": "return file content with base64 encode",
                    "default": True
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
    
    async def execute(self, path: str, base64_content: bool = True, encoding: str = "utf-8") -> ToolResult:
        """Execute read file"""
        try:
            path = Path(path)
            path_security.ensure_within_base(self.context.workspace_dir, path)
            
            if not path.exists():
                return ToolResult.fail(f"File not found: {path}")
            
            if not path.is_file():
                return ToolResult.fail(f"Not a file: {path}")
            
            if base64_content:
                async with aiofiles.open(path, 'rb') as f:
                    content = await f.read()
                    content = base64.b64encode(content).decode('utf-8')
            else:
                async with aiofiles.open(path, 'r', encoding=encoding) as f:
                    content = await f.read()
 
            
            return ToolResult.ok({
                "content": content,
                "path": str(path),
                "size": len(content),
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
                "base64_content": {
                    "type": "boolean",
                    "description": "If pass true,the Input file conent should base64 encoded",
                    "default": True
                },
                "encoding": {
                    "type": "string",
                    "description": "File encoding (default: utf-8)",
                    "enum": ["utf-8", "ascii", "latin-1"],
                    "default": "utf-8"
                }


            },
            category="filesystem",
            required_permissions=["filesystem:write"],
            version="1.0.0"
        )
    
    async def execute(self, path: str, content: str, mode: str = "write", base64_content: bool = True, encoding: str = 'utf-8') -> ToolResult:
        """Execute write file"""
        try:
            path = Path(path)
            path_security.ensure_within_base(self.context.workspace_dir, path)
            if base64_content:
                content = base64.b64decode(content).decode(encoding)

            # Create parent directories if needed
            path.parent.mkdir(parents=True, exist_ok=True)
            
            file_mode = 'wb' if mode == 'write' else 'a'
            async with aiofiles.open(path, file_mode) as f:
                await f.write(content)
            
            return ToolResult.ok({
                "path": path,
                "mode": mode,
                "size": len(content),
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
            path = Path(path)
            path_security.ensure_within_base(self.context.workspace_dir, path)
 
            
            if not path.exists():
                return ToolResult.fail(f"Directory not found: {path}")
            
            if not path.is_dir():
                return ToolResult.fail(f"Not a directory: {path}")
            
            files = []
            if recursive:
                for root, dirs, filenames in os.walk(path):
                    rel_root = Path(root).relative_to(self.context.workspace_dir)
                    for f in filenames:
                        if Path(f).match(pattern):
                            files.append(str(rel_root / f))
            else:
                for f in path.glob(pattern):
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
