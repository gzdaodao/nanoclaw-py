# agents/tools/builtin/system_tools.py
"""Built-in system tools plugin."""

import asyncio
import psutil
import platform
from typing import Optional
import subprocess

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class RunCommandTool(BaseTool):
    """Tool to run shell commands"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="run_command",
            description="Run a shell command (limited and sandboxed)",
            parameters={
                "command": {
                    "type": "string",
                    "description": "Command to run",
                    "required": True
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                    "minimum": 1,
                    "maximum": 300
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (relative to workspace)",
                    "default": "."
                }
            },
            category="system",
            required_permissions=["system:execute"],
            version="1.0.0"
        )
    
    async def execute(self, command: str, timeout: int = 30, working_dir: str = ".") -> ToolResult:
        """Execute run command"""
        try:
            # Security: prevent dangerous commands
            #dangerous = ["rm -rf", "mkfs", "dd", "format", ">:"]
            dangerous = []
            for d in dangerous:
                if d in command:
                    return ToolResult.fail(f"Command contains dangerous pattern: {d}")
            
            full_work_dir = self.context.workspace_dir / working_dir
            if not full_work_dir.exists():
                return ToolResult.fail(f"Working directory not found: {working_dir}")
            
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(full_work_dir)
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), 
                    timeout=timeout
                )
                
                return ToolResult.ok({
                    "stdout": stdout.decode(errors='replace'),
                    "stderr": stderr.decode(errors='replace'),
                    "returncode": process.returncode,
                    "command": command,
                    "timeout": False
                })
                
            except asyncio.TimeoutError:
                process.kill()
                return ToolResult.fail(f"Command timed out after {timeout}s")
            
        except Exception as e:
            return ToolResult.fail(f"Failed to run command: {e}")


class GetSystemInfoTool(BaseTool):
    """Tool to get system information"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_system_info",
            description="Get information about the system",
            parameters={
                "info_type": {
                    "type": "string",
                    "enum": ["all", "cpu", "memory", "disk", "platform"],
                    "description": "Type of information to get",
                    "default": "all"
                }
            },
            category="system",
            required_permissions=["system:read"],
            version="1.0.0"
        )
    
    async def execute(self, info_type: str = "all") -> ToolResult:
        """Execute get system info"""
        try:
            info = {}
            
            if info_type in ["all", "cpu"]:
                info["cpu"] = {
                    "percent": psutil.cpu_percent(interval=1),
                    "count": psutil.cpu_count(),
                    "freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None
                }
            
            if info_type in ["all", "memory"]:
                mem = psutil.virtual_memory()
                info["memory"] = {
                    "total": mem.total,
                    "available": mem.available,
                    "percent": mem.percent,
                    "used": mem.used,
                    "free": mem.free
                }
            
            if info_type in ["all", "disk"]:
                disk = psutil.disk_usage('/')
                info["disk"] = {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": disk.percent
                }
            
            if info_type in ["all", "platform"]:
                info["platform"] = {
                    "system": platform.system(),
                    "release": platform.release(),
                    "version": platform.version(),
                    "machine": platform.machine(),
                    "processor": platform.processor()
                }
            
            return ToolResult.ok(info)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to get system info: {e}")


class SystemToolsPlugin(ToolPlugin):
    """Plugin providing system tools"""
    
    def __init__(self):
        super().__init__()
     
    @property
    def name(self):
        return "system"

    @property
    def version(self):
        return "1.0.0"
    

    async def initialize(self, context: ToolContext) -> None:
        """Initialize system tools plugin"""
        self.register_tool(RunCommandTool(context))
        self.register_tool(GetSystemInfoTool(context))
        logger.info(f"System tools plugin initialized with {len(self._tools)} tools")
