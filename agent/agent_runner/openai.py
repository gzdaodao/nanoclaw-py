# agents/openai.py
"""OpenAI API based agent implementation with simplified skill system."""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Union, Awaitable
from datetime import datetime
import traceback
import inspect

import openai
from openai import AsyncOpenAI

from .base import Agent, AgentContext, AgentMessage, AgentResponse, AgentFactory
from .logger import logger


# Tools
from .tools.base import ToolPlugin, ToolContext, ToolResult
from .tools.loader import PluginLoader

# Simplified skills
from .skills import (
    load_all_skills, get_skills_summary, search_skills,
    get_skill_detail, get_all_skills, get_skill
)

from .tools.builtin.file_tools import FileToolsPlugin
from .tools.builtin.communication_tools import CommunicationToolsPlugin
from .tools.builtin.system_tools import SystemToolsPlugin
from .tools.builtin.scheduler_tools import SchedulerToolsPlugin
from .tools.builtin.memory_tools import MemoryToolsPlugin

# IPC
from .ipc_client import IpcClient

from traceback import format_exc



@AgentFactory.register("openai")
class OpenAIAgent(Agent):
    """Agent using OpenAI API with simplified skill system"""
    
    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str,
        context: Optional[AgentContext] = None,
        temperature: float = 0.5,
        max_tokens: int = 2000000000,
        system_prompt: Optional[str] = None,
        tool_categories: Optional[List[str]] = None,
        plugin_dirs: Optional[List[Path]] = None,
        max_history: int = 100,
        request_timeout: int = 600,
        max_retries: int = 3,
        org_id: str = None,
        **kwargs
    ):
        super().__init__(name, context, max_history)
        
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.custom_system_prompt = system_prompt
        self.tool_categories = tool_categories or [
            "general", "filesystem", "communication", "system", "skills"
        ]
        self.request_timeout = request_timeout
        self.max_retries = max_retries
 
        # IPC
        self.ipc_client = IpcClient()

        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=org_id,
            timeout=request_timeout,
            max_retries=max_retries
        )
        
        # Plugin system
        self.plugin_loader = PluginLoader(plugin_dirs or self._default_plugin_dirs())
        self.tool_context = self._create_tool_context()
        self.plugins: Dict[str, ToolPlugin] = {}
        self.tool_handlers: Dict[str, Callable] = {}
               
        # State
        self._session_id = context.session_id if context else ""
        self._metadata["model"] = model
        self._metadata["temperature"] = temperature
        
        # Stats
        self._stats = {
            "api_calls": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_calls": 0,
            "errors": 0,
            "skill_queries": 0
        }
    
    def _default_plugin_dirs(self) -> List[Path]:
        """Get default plugin directories"""
        dirs = []
        
        # Built-in plugins
        builtin_dir = Path(__file__).parent / 'tools' / 'builtin'
        if builtin_dir.exists():
            dirs.append(builtin_dir)
        
        # User plugins
        user_dir = Path('/workspace/group/plugins')
        if user_dir.exists():
            dirs.append(user_dir)
        
        # global plugins
        global_dir = Path('/workspace/global/plugins')
        if global_dir.exists():
            dirs.append(global_dir)
        
        return dirs
    
    def _create_tool_context(self) -> ToolContext:
        """Create tool execution context"""
        return ToolContext(
            agent_name=self.name,
            group_folder=self.context.group_folder,
            chat_id=self.context.chat_id,
            is_main=self.context.is_main,
            workspace_dir=self.context.workspace_dir,
            ipc_client=self.ipc_client,
            permissions=["*"] if self.context.is_main else [
                "filesystem:read", "filesystem:write",
                "communication:send", "communication:read",
                "memory:read", "memory:write", "system:execute",
            ]
        )
    
    async def initialize(self) -> None:
        """Initialize agent, load plugins and skills"""
        logger.info(f"Initializing OpenAI agent: {self.name}")
        
        # Connect IPC
        await self.ipc_client.connect()
        
        # Load all skills
        try:
            skill_count = load_all_skills()
            logger.info(f"Loaded {skill_count} skills from /workspace/group/skills")
            self._stats["skills_loaded"] = skill_count
        except Exception as e:
            logger.error(f"Failed to load skills: {e}")
        
        # Load built-in tool plugins
        await self._load_tool_plugins()
               
        # Build system prompt
        self.system_prompt = self._build_system_prompt()
        
        # Add system message to history
        self.add_to_history(AgentMessage(
            role="system",
            content=self.system_prompt
        ))
        
        await super().initialize()
        
        logger.info(f"Agent {self.name} initialized with {len(self.tool_handlers)} tools and {skill_count} skills")
       
    async def _load_tool_plugins(self):
        """Load tool plugins"""
        discovered = self.plugin_loader.discover_plugins()
        for module_name in discovered:
            try:
                plugin = await self.plugin_loader.load_plugin(module_name, self.tool_context)
                if plugin:
                    self.plugins[plugin.name] = plugin
                    for tool in plugin.list_tools():
                        tool_instance = plugin.get_tool(tool.name)
                        if tool_instance:
                            self.tool_handlers[tool.name] = tool_instance
                    logger.info(f"Loaded external plugin: {plugin.name}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module_name}: {e}")
    
    def _build_system_prompt(self) -> str:
        """Build system prompt with skill information"""
        if self.custom_system_prompt:
            return self.custom_system_prompt
        
        # Get skills summary
        skills_summary = get_skills_summary()
        
       
        # Build prompt
        prompt = f"""You are {self.context.assistant_name}, an AI assistant with access to various skills and tools.
You are in group: {self.context.group_folder}
Main group: {self.context.is_main}

{skills_summary}

"""
        
        # Add skill tools
        prompt += """
## GUIDELINES
1. When users ask about capabilities, check the skills list first
2. For specific tasks, search for relevant skills using search_skills()
3. Provide skill details when users want to learn more
4. Use other tools when appropriate
5. Keep responses clear and helpful

## WORKFLOW
When given a task:
1. Check if there's a relevant skill in the skills list
2. If found, explain how the skill can help
3. If user wants details, use get_skill_details()
4. If no relevant skill, use available tools
5. Report results clearly
"""
        
        return prompt
    
    def _get_enabled_tools(self) -> List[Dict[str, Any]]:
        """Get OpenAI function specifications for enabled tools"""
        tools = []
        for name, tool in self.tool_handlers.items():
            if tool.metadata.category in self.tool_categories:
                tools.append(tool.get_openai_function_spec())
        return tools
    
    def _build_messages(self, new_messages: List[str]) -> List[Dict[str, Any]]:
        """Build messages for OpenAI API"""
        messages = []
        
        # Add conversation history
        for msg in self.get_recent_history(self.max_history):
            messages.append(msg.to_dict())
        
        # Add new messages
        for content in new_messages:
            messages.append({"role": "user", "content": content})
            self.add_to_history(AgentMessage(role="user", content=content))
        
        return messages
    
    async def process_messages(self, messages: List[str], **kwargs) -> AgentResponse:
        """Process multiple messages"""
        if not self._running:
            await self.initialize()
        
        self._processing = True
        self._last_activity = datetime.now()
        
        try:
            # Build messages
            api_messages = self._build_messages(messages)
            
            # Get enabled tools
            tools = self._get_enabled_tools()
            
            # Prepare completion arguments
            completion_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": api_messages,
                "temperature": kwargs.get("temperature", self.temperature),
                #"max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            
            if tools:
                completion_kwargs["tools"] = tools
                completion_kwargs["tool_choice"] = "auto"
            
            # Remove None values
            completion_kwargs = {k: v for k, v in completion_kwargs.items() if v is not None}
            
            # Call OpenAI
            self._stats["api_calls"] += 1
            start_time = time.time()
            
            logger.info(f'completion_kwargs:{completion_kwargs}')
            response = await self.client.chat.completions.create(**completion_kwargs)
            
            elapsed = (time.time() - start_time) * 1000
            
            # Update token stats
            if response.usage:
                self._stats["total_tokens"] += response.usage.total_tokens
                self._stats["prompt_tokens"] += response.usage.prompt_tokens
                self._stats["completion_tokens"] += response.usage.completion_tokens
            
            # Process response and handle tool calls
            content, tool_results = await self._process_response(response)
            
            # Update tool call stats
            self._stats["tool_calls"] += len(tool_results)
            
            # Add assistant response to history
            self.add_to_history(AgentMessage(
                role="assistant",
                content=content,
                tool_calls=response.choices[0].message.tool_calls
            ))
            
            return AgentResponse(
                content=content,
                session_id=response.id,
                metadata={
                    "model": response.model,
                    "elapsed_ms": round(elapsed, 2),
                    "tool_calls": len(tool_results),
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    } if response.usage else None
                },
                tool_results=tool_results
            )
            
        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            logger.info(format_exc())

            self._stats["errors"] += 1
            return AgentResponse(error=f"OpenAI API error: {e}")
        except Exception as e:
            logger.error(f"Error processing messages: {e}\n{traceback.format_exc()}")
            self._stats["errors"] += 1
            return AgentResponse(error=str(e))
        finally:
            self._processing = False
    
    async def stream_response(
        self,
        message: str,
        callback: Callable[[str], Awaitable[None]],
        **kwargs
    ) -> AgentResponse:
        """Stream response token by token"""
        if not self._running:
            await self.initialize()
        
        self._processing = True
        self._last_activity = datetime.now()
        
        try:
            # Build messages
            api_messages = self._build_messages([message])
            
            # Get enabled tools
            tools = self._get_enabled_tools()
            
            # Prepare completion arguments
            completion_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": api_messages,
                "temperature": kwargs.get("temperature", self.temperature),
                #"max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "stream": True
            }
            
            if tools:
                completion_kwargs["tools"] = tools
                completion_kwargs["tool_choice"] = "auto"
            
            # Stream response
            self._stats["api_calls"] += 1
            full_content = ""
            
            stream = await self.client.chat.completions.create(**completion_kwargs)
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    await callback(content)
            
            # Add to history
            self.add_to_history(AgentMessage(role="assistant", content=full_content))
            
            return AgentResponse(content=full_content)
            
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            self._stats["errors"] += 1
            return AgentResponse(error=str(e))
        finally:
            self._processing = False
    
    async def _process_response(self, response):
        """Process OpenAI response and handle tool calls"""
        logger.info(f'_process_response:{response}')
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        tool_results = []
        
        # Handle tool calls
        if message.tool_calls:
            for tool_call in message.tool_calls:
                result = await self._handle_tool_call(tool_call)
                tool_results.append(result)
                
                # Track skill queries
                if tool_call.function.name in ["search_skills", "get_skill_details"]:
                    self._stats["skill_queries"] += 1
                
                # Add tool result to history
                self.add_to_history(AgentMessage(
                    role="tool",
                    content=json.dumps(result),
                    tool_call_id=tool_call.id,
                    name=tool_call.function.name
                ))
            
            # If there were tool calls, get final response
            final_messages = self._build_messages([])
            final_response = await self.client.chat.completions.create(
                model=self.model,
                messages=final_messages,
                temperature=self.temperature,
                #max_tokens=self.max_tokens
            )
            content = final_response.choices[0].message.content or ""
            
            # Update token stats
            if final_response.usage:
                self._stats["total_tokens"] += final_response.usage.total_tokens
                self._stats["prompt_tokens"] += final_response.usage.prompt_tokens
                self._stats["completion_tokens"] += final_response.usage.completion_tokens
        
        logger.debug(f'content:{content}\ntool_results: {tool_results}')

        return content, tool_results
    
    async def _handle_tool_call(self, tool_call) -> Dict[str, Any]:
        """Handle a single tool call"""
        try:
            function_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            
            logger.info(f"Executing tool: {function_name} with args: {arguments}")
            
            if function_name in self.tool_handlers:
                tool = self.tool_handlers[function_name]
                result = await tool(**arguments)
                
                return {
                    "tool": function_name,
                    "success": result.success,
                    "result": result.data if result.success else None,
                    "error": result.error if not result.success else None
                }
            else:
                logger.warning(f"Unknown tool: {function_name}")
                return {
                    "tool": function_name,
                    "success": False,
                    "error": f"Unknown tool: {function_name}"
                }
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            logger.debug(traceback.format_exc())
            return {
                "tool": tool_call.function.name,
                "success": False,
                "error": str(e)
            }
    
    async def _handle_skill_query(self, message: str) -> Optional[str]:
        """Handle direct skill queries (for non-tool mode)"""
        # Check if message is asking about skills
        skill_keywords = ["skill", "can you", "ability", "capable", "what can"]
        if not any(kw in message.lower() for kw in skill_keywords):
            return None
        
        # Search for relevant skills
        results = search_skills(message)
        if results:
            response = "I found these relevant skills:\n\n"
            for skill in results[:3]:
                response += f"• **{skill['name']}**: {skill['description']}\n"
            response += "\nUse `get_skill_details('skill_name')` to learn more about any skill."
            return response
        
        return None
    
    async def stop(self) -> None:
        """Stop the agent"""
        logger.info(f"Stopping agent: {self.name}")
        
        # Cleanup plugins
        for plugin in self.plugins.values():
            try:
                await plugin.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up plugin {plugin.name}: {e}")
        
        # Disconnect IPC
        await self.ipc_client.disconnect()
        
        await super().stop()
        logger.info(f"Agent {self.name} stopped. Stats: {self._stats}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get agent status"""
        status = super().get_status()
        
        # Get skill info
        all_skills = get_all_skills()
        
        status.update({
            "model": self.model,
            "temperature": self.temperature,
            "tools": len(self.tool_handlers),
            "skills": len(all_skills),
            "skills_list": all_skills[:5],  # First 5 skills as preview
            "plugins": list(self.plugins.keys()),
            "stats": self._stats,
            "tool_categories": self.tool_categories
        })
        return status
    
    def get_stats(self) -> Dict[str, Any]:
        """Get detailed statistics"""
        all_skills = get_all_skills()
        return {
            **self._stats,
            "history_length": len(self.history),
            "tools_loaded": len(self.tool_handlers),
            "skills_loaded": len(all_skills),
            "plugins_loaded": len(self.plugins),
            "uptime_seconds": (datetime.now() - self._last_activity).total_seconds() if self._last_activity else 0
        }
