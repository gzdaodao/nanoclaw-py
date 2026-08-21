# agents/tools/builtin/scheduler_tools.py
"""Built-in task scheduler tools plugin."""

import json
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
            description="Schedule a task to run later. The task will be executed in the current chat context.",
            parameters={
                "id": {
                    "type": "string",
                    "description": "Unique Task ID",
                    "required": True
                },
                "prompt": {
                    "type": "string",
                    "description": "Task prompt/description",
                    "required": True
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["cron", "interval", "once"],
                    "description": "Type of schedule, cron: use cron expression to schedule, interval: Repeat execution at intervals, once: Execute once at schedule_value isotime",
                    "required": True
                },
                "schedule_value": {
                    "type": "string",
                    "description": "Schedule value (cron expression, interval ms, or timestamp)",
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
    
    async def execute(self, id: str, prompt: str, schedule_type: str, schedule_value: str, 
                     context_mode: str = "isolated") -> ToolResult:
        """Execute schedule task - target_jid is automatically taken from session context"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            # 🟢 从上下文获取 chat_id，不让 Agent 自己指定
            target_jid = self.context.chat_id
            
            # 构建消息
            message = {
                "type": "schedule_task",
                "id": id,
                "prompt": prompt,
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "targetJid": target_jid,  # 从上下文获取
                "context_mode": context_mode,
                "source_group": self.context.group_folder,
                "chatJid": self.context.chat_id
            }
            
            # 使用 send_request 发送到 messages 目录（主系统监听）
            await self.context.ipc_client.send_request(message)
            
            logger.info(f"Schedule task request sent: {schedule_type}:{schedule_value} for {target_jid}")
            
            return ToolResult.ok({
                "status": "submitted",
                "message": f"Task scheduled for current chat",
                "schedule": f"{schedule_type}: {schedule_value}",
                "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt
            })
            
        except Exception as e:
            logger.error(f"Failed to schedule task: {e}")
            return ToolResult.fail(f"Failed to schedule task: {e}")


class ListTasksTool(BaseTool):
    """Tool to list scheduled tasks"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_tasks",
            description="List scheduled tasks for current chat",
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
        """Execute list tasks - automatically uses current chat context"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            # 🟢 从上下文获取 group_folder
            message = {
                "type": "list_tasks",
                "status": status,
                "limit": limit,
                "group_folder": self.context.group_folder,
                "chatJid": self.context.chat_id
            }
            
            # 发送请求
            await self.context.ipc_client.send_request(message)
            
            logger.info(f"List tasks request sent for {self.context.group_folder}")
            
            return ToolResult.ok({
                "status": "requested",
                "message": "Task list request sent. Check response in chat.",
                "filter": status
            })
            
        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
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
            
            # 🟢 从上下文获取 group_folder
            message = {
                "type": "cancel_task",
                "taskId": task_id,
                "group_folder": self.context.group_folder,
                "chatJid": self.context.chat_id
            }
            
            # 发送请求
            await self.context.ipc_client.send_request(message)
            
            logger.info(f"Cancel task request sent for {task_id}")
            
            return ToolResult.ok({
                "status": "submitted",
                "message": f"Cancel request submitted for task: {task_id}",
                "task_id": task_id
            })
            
        except Exception as e:
            logger.error(f"Failed to cancel task: {e}")
            return ToolResult.fail(f"Failed to cancel task: {e}")


class PauseTaskTool(BaseTool):
    """Tool to pause a scheduled task"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="pause_task",
            description="Pause a scheduled task",
            parameters={
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to pause",
                    "required": True
                }
            },
            category="scheduler",
            required_permissions=["scheduler:update"],
            version="1.0.0"
        )
    
    async def execute(self, task_id: str) -> ToolResult:
        """Execute pause task"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            # 🟢 从上下文获取 group_folder
            message = {
                "type": "pause_task",
                "taskId": task_id,
                "group_folder": self.context.group_folder,
                "chatJid": self.context.chat_id
            }
            
            # 发送请求
            await self.context.ipc_client.send_request(message)
            
            logger.info(f"Pause task request sent for {task_id}")
            
            return ToolResult.ok({
                "status": "submitted",
                "message": f"Pause request submitted for task: {task_id}",
                "task_id": task_id
            })
            
        except Exception as e:
            logger.error(f"Failed to pause task: {e}")
            return ToolResult.fail(f"Failed to pause task: {e}")


class ResumeTaskTool(BaseTool):
    """Tool to resume a paused scheduled task"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="resume_task",
            description="Resume a paused scheduled task",
            parameters={
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to resume",
                    "required": True
                }
            },
            category="scheduler",
            required_permissions=["scheduler:update"],
            version="1.0.0"
        )
    
    async def execute(self, task_id: str) -> ToolResult:
        """Execute resume task"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            # 🟢 从上下文获取 group_folder
            message = {
                "type": "resume_task",
                "taskId": task_id,
                "group_folder": self.context.group_folder,
                "chatJid": self.context.chat_id
            }
            
            # 发送请求
            await self.context.ipc_client.send_request(message)
            
            logger.info(f"Resume task request sent for {task_id}")
            
            return ToolResult.ok({
                "status": "submitted",
                "message": f"Resume request submitted for task: {task_id}",
                "task_id": task_id
            })
            
        except Exception as e:
            logger.error(f"Failed to resume task: {e}")
            return ToolResult.fail(f"Failed to resume task: {e}")


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
        self.register_tool(PauseTaskTool(context))
        self.register_tool(ResumeTaskTool(context))
        logger.info(f"Scheduler tools plugin initialized with {len(self._tools)} tools")
