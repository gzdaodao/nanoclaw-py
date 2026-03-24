# agents/tools/builtin/scheduler_tools.py
"""Built-in task scheduler tools plugin."""

from typing import Optional, Dict
from datetime import datetime

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class ScheduleTaskTool(BaseTool):
    """Tool to schedule tasks"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="schedule_task",
            description="Schedule a task to run later",
            parameters={
                "prompt": {
                    "type": "string",
                    "description": "Task prompt/description",
                    "required": True
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["cron", "interval", "once", "daily", "weekly"],
                    "description": "Type of schedule",
                    "required": True
                },
                "schedule_value": {
                    "type": "string",
                    "description": "Schedule value (cron expression, interval ms, timestamp, or time)",
                    "required": True
                },
                "target_jid": {
                    "type": "string",
                    "description": "Target chat JID for results",
                    "required": True
                },
                "context_mode": {
                    "type": "string",
                    "enum": ["group", "isolated"],
                    "description": "Whether to share context with group",
                    "default": "isolated"
                }
            },
            category="scheduler",
            required_permissions=["scheduler:create"],
            version="1.0.0"
        )
    
    async def execute(self, prompt: str, schedule_type: str, schedule_value: str, 
                     target_jid: str, context_mode: str = "isolated") -> ToolResult:
        """Execute schedule task"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            result = await self.context.ipc_client.request({
                "type": "schedule_task",
                "prompt": prompt,
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "target_jid": target_jid,
                "context_mode": context_mode,
                "source_group": self.context.group_folder
            })
            
            return ToolResult.ok(result)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to schedule task: {e}")


class ListTasksTool(BaseTool):
    """Tool to list scheduled tasks"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_tasks",
            description="List scheduled tasks",
            parameters={
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "all"],
                    "description": "Filter by status",
                    "default": "active"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tasks to return",
                    "default": 50
                }
            },
            category="scheduler",
            required_permissions=["scheduler:read"],
            version="1.0.0"
        )
    
    async def execute(self, status: str = "active", limit: int = 50) -> ToolResult:
        """Execute list tasks"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            result = await self.context.ipc_client.request({
                "type": "list_tasks",
                "status": status,
                "limit": limit,
                "group_folder": self.context.group_folder
            })
            
            return ToolResult.ok(result)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to list tasks: {e}")


class CancelTaskTool(BaseTool):
    """Tool to cancel a scheduled task"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="cancel_task",
            description="Cancel a scheduled task",
            parameters={
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to cancel",
                    "required": True
                }
            },
            category="scheduler",
            required_permissions=["scheduler:delete"],
            version="1.0.0"
        )
    
    async def execute(self, task_id: str) -> ToolResult:
        """Execute cancel task"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            result = await self.context.ipc_client.request({
                "type": "cancel_task",
                "task_id": task_id,
                "group_folder": self.context.group_folder
            })
            
            return ToolResult.ok(result)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to cancel task: {e}")


class SchedulerToolsPlugin(ToolPlugin):
    """Plugin providing task scheduler tools"""
    
    def __init__(self):
        super().__init__()
     
    @property
    def name(self):
        return "scheduler"

    @property
    def version(self):
        return "1.0.0"
    

    async def initialize(self, context: ToolContext) -> None:
        """Initialize scheduler tools plugin"""
        self.register_tool(ScheduleTaskTool(context))
        self.register_tool(ListTasksTool(context))
        self.register_tool(CancelTaskTool(context))
        logger.info(f"Scheduler tools plugin initialized with {len(self._tools)} tools")
