# agents/tools/builtin/communication_tools.py
"""Built-in communication tools plugin."""

import json
from typing import Optional, List, Dict

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger


class SendMessageTool(BaseTool):
    """Tool to send messages to chats"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="send_message",
            description="Send a message to a chat",
            parameters={
                "jid": {
                    "type": "string",
                    "description": "JID of the target chat (format: channel:chat_id)",
                    "required": True
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                    "required": True
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel name override",
                    "required": False
                },
                "format": {
                    "type": "string",
                    "description": "Message format",
                    "enum": ["plain", "markdown", "html"],
                    "default": "plain"
                }
            },
            category="communication",
            required_permissions=["communication:send"],
            version="1.0.0"
        )
    
    async def execute(self, jid: str, text: str, channel: Optional[str] = None, format: str = "plain") -> ToolResult:
        """Execute send message"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            # Send message via IPC
            await self.context.ipc_client.send_request({
                "type": "send_message",
                "jid": jid,
                "text": text,
                "channel": channel,
                "format": format
            })
            
            return ToolResult.ok({
                "jid": jid,
                "channel": channel or "auto",
                "format": format
            })
            
        except Exception as e:
            return ToolResult.fail(f"Failed to send message: {e}")


class GetChannelInfoTool(BaseTool):
    """Tool to get channel information"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_channel_info",
            description="Get information about available channels",
            parameters={
                "channel_name": {
                    "type": "string",
                    "description": "Optional specific channel name",
                    "required": False
                }
            },
            category="communication",
            required_permissions=["communication:read"],
            version="1.0.0"
        )
    
    async def execute(self, channel_name: Optional[str] = None) -> ToolResult:
        """Execute get channel info"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            if channel_name:
                # Get specific channel
                result = await self.context.ipc_client.request({
                    "type": "get_channel",
                    "name": channel_name
                })
            else:
                # Get all channels
                result = await self.context.ipc_client.request({
                    "type": "get_channels"
                })
            
            return ToolResult.ok(result)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to get channel info: {e}")


class ListGroupsTool(BaseTool):
    """Tool to list available groups"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_groups",
            description="List available groups",
            parameters={
                "filter": {
                    "type": "string",
                    "description": "Optional filter by group name",
                    "required": False
                },
                "channel": {
                    "type": "string",
                    "description": "Filter by channel",
                    "required": False
                }
            },
            category="communication",
            required_permissions=["communication:read"],
            version="1.0.0"
        )
    
    async def execute(self, filter: Optional[str] = None, channel: Optional[str] = None) -> ToolResult:
        """Execute list groups"""
        try:
            if not self.context.ipc_client:
                return ToolResult.fail("IPC client not available")
            
            result = await self.context.ipc_client.request({
                "type": "list_groups",
                "filter": filter,
                "channel": channel
            })
            
            return ToolResult.ok(result)
            
        except Exception as e:
            return ToolResult.fail(f"Failed to list groups: {e}")


class CommunicationToolsPlugin(ToolPlugin):
    """Plugin providing communication tools"""
    
    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "communication"

    @property
    def version(self):
        return "1.0.0"
    
    async def initialize(self, context: ToolContext) -> None:
        """Initialize communication tools plugin"""
        self.register_tool(SendMessageTool(context))
        self.register_tool(GetChannelInfoTool(context))
        self.register_tool(ListGroupsTool(context))
        logger.info(f"Communication tools plugin initialized with {len(self._tools)} tools")
