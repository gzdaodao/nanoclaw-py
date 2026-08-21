# agents/runner.py
"""Agent runner for containerized execution."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import traceback

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .base import AgentContext, AgentFactory
from .openai import OpenAIAgent
from .ipc_client import IpcClient
from .logger import logger

            
# Add to Python path
agent_dir = Path(__file__).parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

app_dir = Path(__file__).parent
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))
 
class AgentRunner:
    """Agent runner for containerized execution"""
    
    def __init__(self):
        self.agent: Optional[OpenAIAgent] = None
        self.ipc_client = IpcClient()
        self._running = False
        self._input_data = None
        self._shutdown_event = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._stats = {
            "messages_processed": 0,
            "tokens_used": 0,
            "start_time": None,
            "errors": 0
        }

        self._read_input_data()

 
    def _read_input_data(self):
        """同步读取 stdin 数据（在异步启动前调用）"""
        try:
            # 直接从 sys.stdin 读取一行
            line = sys.stdin.readline()
            if line:
                data = json.loads(line)
                self._input_data = data
                logger.info(f"Read initial data from stdin: {list(data.keys())}")
            else:
                logger.warning("No data received from stdin")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse stdin JSON: {e}")
        except Exception as e:
            logger.error(f"Failed to read from stdin: {e}")
 
   
    async def initialize(self):
        """Initialize agent from environment variables"""
        logger.info("Initializing agent from environment variables")
        
        # Load context from environment
        context = AgentContext(
            session_id=self._input_data['sessionId'] or self._input_data['chatJid'],
            group_folder=self._input_data['groupFolder'],
            chat_id=self._input_data['chatJid'],
            is_main=self._input_data['isMain'],
            assistant_name=self._input_data['assistantName'],
            channel_info=self._input_data.get('channelInfo', None),
            available_channels=self._input_data.get('availableChannels', []),
            workspace_dir=Path(self._input_data.get('workspaceDir', '/workspace/group')),
            data_dir=Path(self._input_data.get('DataDir', '/workspace/group/.data'))
        )
        
        secrets = self._input_data.get('secrets', {})
        configs = self._input_data.get('configs', {})
        
        # Agent configuration
        agent_type = configs.get('AGENT_TYPE', 'openai')
        agent_name = configs.get('AGENT_NAME', 'default')
        
        # OpenAI specific config
        openai_config = {
            "model": configs.get('OPENAI_MODEL', ''),
            "base_url": configs.get('OPENAI_BASE_URL', ''),
            "org_id": configs.get('OPENAI_ORG_ID', ''),
            "temperature": float(configs.get('OPENAI_TEMPERATURE', '0.5')),
            "max_tokens": int(configs.get('OPENAI_MAX_TOKENS', '2000000000')),
            "max_compress_tokens": int(configs.get('OPENAI_MAX_COMPRESS_TOKENS', '1000000')),
            "enable_skill_learning": configs.get('ENABLE_SKILL_LEARNING', True),
            "tool_categories": configs.get('TOOL_CATEGORIES', 'general,filesystem,communication,data,system,skills,memory,web,scheduler').split(',')
        }
        
        # Create agent
        if agent_type == 'openai':
            self.agent = OpenAIAgent(
                name=agent_name,
                api_key=secrets.get('OPENAI_API_KEY'),
                context=context,
                **openai_config
            )
        else:
            # Use factory for other agent types
            self.agent = AgentFactory.create(
                agent_type=agent_type,
                name=agent_name,
                context=context,
            )
        
        # Initialize agent
        await self.agent.initialize()
        
        logger.info(f"Agent initialized: {agent_name} (type: {agent_type})")
        logger.info(f"Context: group={context.group_folder}, is_main={context.is_main}")
    
    async def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        loop = asyncio.get_running_loop()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self.shutdown(s.name))
            )
        
        logger.info("Signal handlers setup")
    
    async def run_ipc_loop(self):
        """Run IPC message processing loop"""
        input_dir = Path('/workspace/ipc/input')
        input_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Starting IPC loop, watching {input_dir}")
        
        processed_files = set()
        
        while self._running and not self._shutdown_event.is_set():
            try:
                # Check for input files
                files = sorted(input_dir.glob('*.json'))
                
                #logger.info(f"Starting process files:{files}")
                for file in files:
                    if file.name in processed_files:
                        continue
                    
                    if file.name == '_close':
                        logger.info("Received close signal")
                        self._shutdown_event.set()
                        break
                    
                    await self._process_ipc_file(file)
                    processed_files.add(file.name)
                
                # Cleanup old processed files set (keep last 1000)
                if len(processed_files) > 1000:
                    processed_files = set(list(processed_files)[-500:])
                
                await asyncio.sleep(0.1)  # 100ms polling
                
            except Exception as e:
                logger.error(f"Error in IPC loop: {e}")
                logger.debug(traceback.format_exc())
                await asyncio.sleep(1)
    
    async def _process_ipc_file(self, file: Path):
        """Process an IPC file"""
        try:
            data = json.loads(file.read_text())
            logger.debug(f"Processing IPC file: {file.name}, type: {data.get('type')}")
            
            if data.get('type') == 'message':
                await self._handle_message(data)
                self._stats["messages_processed"] += 1
            
            elif data.get('type') == 'command':
                await self._handle_command(data)
            
            elif data.get('type') == 'task':
                await self._handle_task(data)
            
            elif data.get('type') == 'ping':
                await self._handle_ping(data)
            
            else:
                logger.warning(f"Unknown message type: {data.get('type')}")
            
            # Delete processed file
            file.unlink()
            logger.debug(f"Processed and deleted: {file.name}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {file}: {e}")
            await self._move_to_error(file, "invalid_json")
        except Exception as e:
            logger.error(f"Error processing {file}: {e}")
            logger.debug(traceback.format_exc())
            await self._move_to_error(file, "processing_error")
            self._stats["errors"] += 1
    
    async def _move_to_error(self, file: Path, reason: str):
        """Move file to error directory"""
        error_dir = file.parent / 'errors'
        error_dir.mkdir(exist_ok=True)
        new_name = error_dir / f"{reason}_{file.name}"
        file.rename(new_name)
        logger.debug(f"Moved to errors: {new_name}")
    
    async def _handle_message(self, data: Dict):
        """Handle message"""
        text = data.get('text', '')
        jid = self._input_data.get('chatJid')
        message_id = data.get('id', '')
        stream = data.get('stream', False)
        logger.info(f"_handle_message: {data}")
        
        logger.info(f"Processing message: {message_id} (stream={stream})")
        
        if stream:
            # Stream response
            async def on_token(token: str):
                await self.ipc_client.send_output(token, jid, message_id)
            
            response = await self.agent.stream_response(text, on_token)
        else:
            # Regular response
            response = await self.agent.process_message(text)
        
        # Send response
        if response.error:
            logger.error(f"Agent error: {response.error}")
            await self.ipc_client.send_error(response.error, jid, message_id)
        elif response.content:
            await self.ipc_client.send_output(response.content, jid, message_id)
            
            # Update token stats
            if response.metadata and 'usage' in response.metadata:
                usage = response.metadata['usage']
                self._stats["tokens_used"] += usage.get('total_tokens', 0)
    
    async def _handle_command(self, data: Dict):
        """Handle command"""
        command = data.get('command', '')
        args = data.get('args', {})
        command_id = data.get('id', '')
        
        logger.info(f"Processing command: {command}")
        jid = self._input_data.get('chatJid')
        
        if command == 'get_status':
            status = self.agent.get_status() if self.agent else {}
            status.update({
                "runner_stats": self._stats,
                "running": self._running
            })
            await self.ipc_client.send_output(json.dumps(status), jid, command_id)
        
        elif command == 'get_skills':
            if hasattr(self.agent, 'skill_indexer') and self.agent.skill_indexer:
                skills = self.agent.skill_indexer.list_skills()
                await self.ipc_client.send_output(json.dumps({"skills": skills}), jid, command_id)
            else:
                await self.ipc_client.send_output(json.dumps({"skills": []}), jid, command_id)
        
        elif command == 'clear_history':
            if self.agent:
                self.agent.clear_history(keep_system=True)
                await self.ipc_client.send_output("History cleared", jid, command_id)
        
        elif command == 'get_stats':
            await self.ipc_client.send_output(json.dumps(self._stats), jid, command_id)
        
        elif command == 'shutdown':
            logger.info("Shutdown command received")
            await self.ipc_client.send_output("Shutting down...", jid, command_id)
            self._shutdown_event.set()
        
        else:
            logger.warning(f"Unknown command: {command}")
            await self.ipc_client.send_error(f"Unknown command: {command}", jid, command_id)
    
    async def _handle_task(self, data: Dict):
        """Handle scheduled task"""
        task_id = data.get('task_id')
        prompt = data.get('prompt', '')
        jid = self._input_data.get('chatJid')
        
        logger.info(f"Processing task: {task_id}")
        
        response = await self.agent.process_message(prompt)
        
        await self.ipc_client.send_task_result(task_id, jid, response.to_dict())
    
    async def _handle_ping(self, data: Dict):
        """Handle ping"""
        jid = self._input_data.get('chatJid')
        await self.ipc_client.send_output("pong", jid, data.get('id'))
    
    async def shutdown(self, signal_name: str = "unknown"):
        """Graceful shutdown"""
        logger.info(f"Shutting down (signal: {signal_name})")
        self._shutdown_event.set()
        self._running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        # Stop agent
        if self.agent:
            await self.agent.stop()
        
        # Disconnect IPC
        await self.ipc_client.disconnect()
        
        # Log final stats
        self._stats["end_time"] = datetime.now().isoformat()
        if self._stats["start_time"]:
            start = datetime.fromisoformat(self._stats["start_time"])
            duration = (datetime.now() - start).total_seconds()
            self._stats["duration_seconds"] = duration
        
        logger.info(f"Shutdown complete. Final stats: {self._stats}")
    
    async def run(self):
        """Main run loop"""
        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()
        
        try:
            # Setup signal handlers
            await self.setup_signal_handlers()
            
            # Initialize from environment
            await self.initialize()
            
            # Connect IPC
            await self.ipc_client.connect()
            
            logger.info(f"Agent runner started. Stats: {self._stats}")
            
            # Run IPC loop
            await self.run_ipc_loop()
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        except Exception as e:
            logger.error(f"Runner error: {e}")
            logger.info(traceback.format_exc())
            self._stats["errors"] += 1
        finally:
            await self.shutdown("runner_complete")


def main():
    """Entry point"""
    logger.info('main start')
    runner = AgentRunner()
    
    try:
        asyncio.run(runner.run())
        logger.info("runner stopped")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    #import time
    #time.sleep(1000)
    main()
