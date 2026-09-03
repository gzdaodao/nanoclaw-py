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
import aiohttp
from aiohttp import ClientTimeout, ClientSession

from .base import Agent, AgentContext, AgentMessage, AgentResponse, AgentFactory
from .base import CtxExceededError, BalanceError, APIError
from .logger import logger


# Tools
from .tools.base import ToolPlugin, ToolContext, ToolResult
from .tools.loader import PluginLoader

# Simplified skills
from .skills import (
    load_all_skills, get_skills_summary, search_skills,
    get_skill_detail, get_all_skills, get_skill
)

# Simplified mcp_servers
from .mcp_servers import (
    load_all_mcp_servers, get_mcp_servers_summary, search_mcp_servers,
    get_mcp_server_detail, get_all_mcp_servers, get_mcp_server
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
        max_compress_tokens: int = 1000000,
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
        self.max_compress_tokens = max_compress_tokens
        self.custom_system_prompt = system_prompt
        self.tool_categories = tool_categories or [
            "general", "filesystem", "communication", "system", "skills", "mcp_servers",
        ]
        self.request_timeout = request_timeout
        self.max_retries = max_retries
 
        # IPC
        self.ipc_client = IpcClient()
        
        # OpenAI API configuration
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.org_id = org_id
        
        # HTTP session
        self._session: Optional[ClientSession] = None
        
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
            agent=self,
            agent_name=self.name,
            group_folder=self.context.group_folder,
            chat_id=self.context.chat_id,
            is_main=self.context.is_main,
            workspace_dir=self.context.workspace_dir,
            ipc_client=self.ipc_client,
            permissions=["*"])
    
    async def _get_session(self) -> ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            timeout = ClientTimeout(total=self.request_timeout)
            self._session = ClientSession(timeout=timeout)
        return self._session
    
    async def initialize(self) -> None:
        """Initialize agent, load plugins and skills"""
        logger.info(f"Initializing OpenAI agent: {self.name}")
        
        # Connect IPC
        await self.ipc_client.connect()
        
        # Initialize HTTP session
        await self._get_session()
        
        # Load all skills
        try:
            skill_count = load_all_skills()
            logger.info(f"Loaded {skill_count} skills from /workspace/group/skills")
            self._stats["skills_loaded"] = skill_count
        except Exception as e:
            logger.error(f"Failed to load skills: {e}")

 
        # Load all mcp_servers
        try:
            mcp_server_count = await load_all_mcp_servers()
            logger.info(f"Loaded {mcp_server_count} mcp_servers from /workspace/group/mcp_servers")
            self._stats["mcp_servers_loaded"] = mcp_server_count
        except Exception as e:
            logger.error(f"Failed to load mcp_servers: {e}")

        
        # Load built-in tool plugins
        await self._load_tool_plugins()
               
        # Build system prompt
        self.system_prompt = self._build_system_prompt()
        
        await super().initialize()
        
        logger.info(f"Agent {self.name} initialized with {len(self.tool_handlers)} tools and {skill_count} skills")
        logger.info(f"Agent {self.name} initialized with {len(self.tool_handlers)} tools and {mcp_server_count} mcp_servers")
       
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
    
    def _get_ai_soul(self) -> str:
        soul = ''

        group_soul = (self.context.workspace_dir or Path('/workspace/group')) / 'SOUL.md'
        global_soul = Path('/workspace/global') / 'SOUL.md'
        if group_soul.exists():
            soul = group_soul.read_text()
        elif global_soul.exists():
            soul = global_soul.read_text()
        

        return soul


    def _build_system_prompt(self) -> str:
        """Build system prompt with skill information"""
        prompt = f"""You are {self.context.assistant_name}, an AI assistant with access to various skills and tools.
"""
        if self.custom_system_prompt:
            prompt = self.custom_system_prompt

        # Add SOUL
        soul = self._get_ai_soul()
        prompt += soul
        
        # Get skills summary
        skills_summary = get_skills_summary()
         
        # Get mcp_servers summary
        mcp_servers_summary = get_mcp_servers_summary()
        
        # Build prompt
        prompt += f"""You are in group: {self.context.group_folder}
Main group: {self.context.is_main}

{skills_summary}
{mcp_servers_summary}

"""
        
        # Add skill tools
        prompt += """
## GUIDELINES
1. When users ask about capabilities, check the skills list first
2. For specific tasks, search for relevant skills using search_skills()
3. Provide skill details when users want to learn more
4. Use other tools when appropriate
5. Keep responses clear and helpful
6. Tool call must strictly adhere to the OpenAI standards
7. It the mcp server tools you want to use are not in the tools list, user tool: load_mcp_tools to add mcp server tools

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
        messages = [{"role": "system", "content": self.system_prompt}]
 
        # Add new messages
        for content in new_messages:
            self.add_to_history(AgentMessage(role="user", content=content))
        
        # Add conversation history
        for msg in self.get_recent_history(self.max_history):
            messages.append(msg.to_dict())
               
        return messages
    
    async def _make_api_request(
        self, 
        messages: List[Dict[str, Any]], 
        tools: List[Dict[str, Any]] = None, 
        stream: bool = False
    ) -> Dict[str, Any]:
        """Make async HTTP request to OpenAI API"""
        url = f"{self.base_url}/chat/completions"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        if self.org_id:
            headers["OpenAI-Organization"] = self.org_id

        if not tools:
            tools = self._get_enabled_tools()
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
            'context_management': [{"type": "compaction", "compact_threshold": self.max_compress_tokens}],
        }
        
        if self.max_tokens and self.max_tokens != 2000000000:
            payload["max_tokens"] = self.max_tokens
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        session = await self._get_session()
        
        # Make request with retries
        last_exception = None
        logger.debug(f'_make_api_request payload:{payload}')
        for attempt in range(self.max_retries):
            try:
                async with session.post(url, headers=headers, json=payload) as response:
                    ct = await response.text()
                    logger.debug(f'_make_api_request response:[{response.status}] {ct}')
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.ClientResponseError as e:
                error_message = str(e)
                try:
                    error_data = await response.json()
                    error = error_data.get('error', {})
                    error_code = error.get('code')
                    error_message = error.get('message', 'Unknown error')
                except Exception as e:
                    raise e

                status = response.status
                if status == 400 and error_code == 'context_length_exceeded':
                    raise CtxExceededError(error_message)
                elif status == 429:
                    if error_code in ['insufficient_quota', 'credit_balance_exhausted', 'quota_exceeded']:
                        raise BalanceError(error_message)

                logger.error(f"API request failed (attempt {attempt + 1}/{self.max_retries}): {e} {ct}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    raise APIError(error_message)
 
            except aiohttp.ClientError as e:
                last_exception = e
                logger.error(f"API request failed (attempt {attempt + 1}/{self.max_retries}): {e} {ct}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise last_exception
    
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
            
            # Call OpenAI API
            self._stats["api_calls"] += 1
            start_time = time.time()
            
            response_data = await self._make_api_request(api_messages, tools)
            
            elapsed = (time.time() - start_time) * 1000
            
            # Update token stats
            if "usage" in response_data:
                usage = response_data["usage"]
                self._stats["total_tokens"] += usage.get("total_tokens", 0)
                self._stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
                self._stats["completion_tokens"] += usage.get("completion_tokens", 0)
            
            # Process response and handle tool calls
            contents, tool_results = await self._process_response(response_data)
            
            # Update tool call stats
            self._stats["tool_calls"] += len(tool_results)
            
            return AgentResponse(
                content='\n'.join(contents),
                session_id=response_data.get("id", ""),
                metadata={
                    "model": response_data.get("model", self.model),
                    "elapsed_ms": round(elapsed, 2),
                    "tool_calls": len(tool_results),
                    "usage": response_data.get("usage", {})
                },
                tool_results=tool_results
            )

        except BalanceError as e:
            return AgentResponse(
                content='AI account balance insufficient',
            )
        except CtxExceededError as e:
            self.clear_history()
            return AgentResponse(
                content='AI session context is too long, automatically reset session.',
            )
        except APIError as e:
            return AgentResponse(
                content=f'AI API Error: {str(e)}',
            )

            
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
            
            # Call streaming API
            self._stats["api_calls"] += 1
            full_content = ""
            
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            if self.org_id:
                headers["OpenAI-Organization"] = self.org_id
            
            payload = {
                "model": kwargs.get("model", self.model),
                "messages": api_messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "stream": True
            }
            
            if self.max_tokens and self.max_tokens != 2000000000:
                payload["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
            
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            
            session = await self._get_session()
            
            async with session.post(url, headers=headers, json=payload) as response:
                response.raise_for_status()
                
                # Process SSE stream
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = line[6:]
                        if data == '[DONE]':
                            break
                        
                        try:
                            chunk = json.loads(data)
                            if chunk.get('choices') and chunk['choices'][0].get('delta', {}).get('content'):
                                content = chunk['choices'][0]['delta']['content']
                                full_content += content
                                await callback(content)
                        except json.JSONDecodeError:
                            continue
            
            # Add to history
            self.add_to_history(AgentMessage(role="assistant", content=full_content))
            
            return AgentResponse(content=full_content)
            
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            self._stats["errors"] += 1
            return AgentResponse(error=str(e))
        finally:
            self._processing = False
    
    async def _process_response(self, response_data: Dict[str, Any]):
        """Process OpenAI response and handle tool calls"""
        logger.info(f'_process_response: {response_data}')
        
        if not response_data.get('choices'):
            return "", []
        
        choice = response_data['choices'][0]
        message = choice.get('message', {})
        content = message.get('content', "") or ""
        contents = [content]
        tool_results = []
        
        # Convert tool_calls to serializable format if present
        tool_calls = message.get('tool_calls')
        
        # Add assistant response to history (with serializable tool_calls)
        self.add_to_history(AgentMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls
        ))
        
        # Handle tool calls
        for tool_call in message.get('tool_calls', []):
            result = await self._handle_tool_call(tool_call)
            logger.debug(f'tool_result: {result}')
            tool_results.append(result)
            
            # Track skill queries
            if tool_call.get('function', {}).get('name') in ["search_skills", "get_skill_details"]:
                self._stats["skill_queries"] += 1
            
            # Add tool result to history
            self.add_to_history(AgentMessage(
                role="tool",
                content=json.dumps(result) if isinstance(result, dict) else str(result),
                tool_call_id=tool_call.get('id'),
                name=tool_call.get('function', {}).get('name')
            ))
        
        # If there were tool calls, get final response
        if message.get('tool_calls', []):
            api_messages = self._build_messages([])
            response_data = await self._make_api_request(api_messages)
        
        
            #if response_data.get('choices'):
            #    choice = response_data['choices'][0]
            #    message = choice.get('message', {})
            #    tcontent = message.get('content', "") or ""
            #    logger.debug(f'tcontent: {tcontent}')
            #    content += tcontent
            #    
            #    # Update token stats
            #    if "usage" in response_data:
            #        usage = response_data["usage"]
            #        self._stats["total_tokens"] += usage.get("total_tokens", 0)
            #        self._stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
            #        self._stats["completion_tokens"] += usage.get("completion_tokens", 0)
    
            tcontents, ttool_results = await self._process_response(response_data)
            contents.extend(tcontents)
            tool_results.extend(ttool_results)

        return contents, tool_results
    
    async def _handle_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a single tool call"""
        try:
            function_name = tool_call.get('function', {}).get('name', '')
            arguments = json.loads(tool_call.get('function', {}).get('arguments', '{}'))
            
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
                "tool": tool_call.get('function', {}).get('name', 'unknown'),
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
        
        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
        
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



  
