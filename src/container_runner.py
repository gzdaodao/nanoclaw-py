# container_runner.py - 面向对象版本
import asyncio
import json
import os
import signal
import shutil
import pwd
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Awaitable
import subprocess

from .config import (
    CONTAINER_IMAGE, CONTAINER_MAX_OUTPUT_SIZE, CONTAINER_TIMEOUT,
    DATA_DIR, GROUPS_DIR, IDLE_TIMEOUT, TIMEZONE, HOST_DIR,
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ORG_ID, OPENAI_MODEL
)
from .group_folder import GroupFolderResolver
from .logger import logger
from .container_runtime import ContainerRuntime
from .mount_security import MountValidator
from .dtypes import RegisteredGroup, AdditionalMount

OUTPUT_START_MARKER = '---NANOCLAW_OUTPUT_START---'
OUTPUT_END_MARKER = '---NANOCLAW_OUTPUT_END---'

@dataclass
class ContainerInput:
    prompt: str
    groupFolder: str
    chatJid: str
    isMain: bool
    sessionId: Optional[str] = None
    isScheduledTask: bool = False
    assistantName: Optional[str] = None
    secrets: Optional[Dict[str, str]] = None
    configs: Optional[Dict[str, str]] = None
    channelInfo: Optional[Dict[str, Any]] = None 
    availableChannels: Optional[List[Dict[str, Any]]] = None

@dataclass
class ContainerOutput:
    status: str  # 'success' or 'error'
    result: Optional[str] = None
    newSessionId: Optional[str] = None
    error: Optional[str] = None

@dataclass
class VolumeMount:
    hostPath: str
    containerPath: str
    readonly: bool


class VolumeMountBuilder:
    """Build volume mounts for containers"""
    
    def __init__(self, project_root: Path, data_dir: Path, groups_dir: Path):
        self.project_root = project_root
        self.data_dir = data_dir
        self.groups_dir = groups_dir
        self.folder_resolver = GroupFolderResolver(groups_dir, data_dir)
        self.mount_validator = MountValidator()
    
    def build_mounts(self, group: RegisteredGroup, is_main: bool) -> List[VolumeMount]:
        """Build volume mounts for container"""
        mounts = []
        group_dir = self.folder_resolver.resolve_group_folder(group.folder)
        host_dir = HOST_DIR
        
        if is_main:
            # Main gets group folder as working directory
            mounts.append(VolumeMount(
                hostPath='{}/{}'.format(HOST_DIR,str(group_dir)),
                containerPath='/workspace/group',
                readonly=False
            ))
        else:
            # Other groups only get their own folder
            mounts.append(VolumeMount(
                hostPath='{}/{}'.format(HOST_DIR,str(group_dir)),
                containerPath='/workspace/group',
                readonly=False
            ))
            
            # Global memory directory
            global_dir = self.groups_dir / 'global'
            if global_dir.exists():
                mounts.append(VolumeMount(
                    hostPath='{}/{}'.format(HOST_DIR,str(global_dir)),
                    containerPath='/workspace/global',
                    readonly=True
                ))
        
            # share memory directory
            global_dir = self.groups_dir / 'share'
            if global_dir.exists():
                mounts.append(VolumeMount(
                    hostPath='{}/{}'.format(HOST_DIR,str(global_dir)),
                    containerPath='/workspace/share',
                    readonly=True
                ))
        
        # Sync skills
        self._sync_skills(group_dir)
        
               
        # Per-group IPC namespace
        group_ipc_dir = self.folder_resolver.resolve_group_ipc_path(group.folder)
        for subdir in ['messages', 'tasks', 'input']:
            (group_ipc_dir / subdir).mkdir(parents=True, exist_ok=True)
        
        mounts.append(VolumeMount(
            hostPath='{}/{}'.format(HOST_DIR,str(group_ipc_dir)),
            containerPath='/workspace/ipc',
            readonly=False
        ))
        
       
        
        # Additional mounts
        if group.containerConfig and group.containerConfig.additionalMounts:
            validated = self.mount_validator.validate_additional_mounts(
                group.containerConfig.additionalMounts,
                group.name,
                is_main
            )
            for m in validated:
                mounts.append(VolumeMount(
                    hostPath=m['hostPath'],
                    containerPath=m['containerPath'],
                    readonly=m['readonly']
                ))
        
        return mounts
    
    def _sync_skills(self, group_dir: Path) -> None:
        """Sync skills to group sessions directory"""
        skills_dst = group_dir / 'skills'
        skills_dst.mkdir(parents=True, exist_ok=True)
 
        skills_src = self.groups_dir / 'global' / 'skills'
        if not skills_src.exists():
            return
       
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            dst_dir = skills_dst / skill_dir.name
            if not dst_dir.exists():
                shutil.copytree(skill_dir, dst_dir)
  
class ContainerArgumentBuilder:
    """Build container runtime arguments"""
    
    def __init__(self, runtime: ContainerRuntime):
        self.runtime = runtime
    
    def build_args(self, mounts: List[VolumeMount], container_name: str) -> List[str]:
        """Build container arguments"""
        args = ['run', '-i', '--rm', '--name', container_name]
        
        # Pass host timezone
        args.extend(['-e', f'TZ={TIMEZONE}'])
        
        # Run as host user
        host_uid = os.getuid()
        host_gid = os.getgid()
        if host_uid != 0 and host_uid != 1000:
            args.extend(['--user', f'{host_uid}:{host_gid}'])
            args.extend(['-e', 'HOME=/home/node'])
        
        for mount in mounts:
            if mount.readonly:
                args.extend(self.runtime.readonly_mount_args(mount.hostPath, mount.containerPath))
            else:
                args.extend(['-v', f'{mount.hostPath}:{mount.containerPath}'])
        
        args.append(CONTAINER_IMAGE)
        
        return args


class ContainerOutputParser:
    """Parse container output with markers"""
    
    def __init__(self):
        self.parse_buffer = ''
        self.new_session_id: Optional[str] = None
        self.had_streaming_output = False
    
    def parse_chunk(self, chunk: str) -> List[ContainerOutput]:
        """Parse a chunk of output and return parsed outputs"""
        outputs = []
        self.parse_buffer += chunk
        
        while True:
            start_idx = self.parse_buffer.find(OUTPUT_START_MARKER)
            if start_idx == -1:
                break
            
            end_idx = self.parse_buffer.find(OUTPUT_END_MARKER, start_idx)
            if end_idx == -1:
                break
            
            json_str = self.parse_buffer[
                start_idx + len(OUTPUT_START_MARKER):end_idx
            ].strip()
            self.parse_buffer = self.parse_buffer[end_idx + len(OUTPUT_END_MARKER):]
            
            try:
                parsed_data = json.loads(json_str)
                parsed = ContainerOutput(**parsed_data)
                if parsed.newSessionId:
                    self.new_session_id = parsed.newSessionId
                self.had_streaming_output = True
                outputs.append(parsed)
            except Exception as e:
                logger.warn(f'Failed to parse streamed output: {e}')
        
        return outputs


class ContainerLogger:
    """Handle container logging"""
    
    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
    
    def write_log(self, 
                  group_name: str,
                  container_name: str,
                  input_data: ContainerInput,
                  container_args: List[str],
                  mounts: List[VolumeMount],
                  stdout_data: bytes,
                  stderr_data: bytes,
                  returncode: int,
                  duration_ms: float,
                  stdout_truncated: bool,
                  stderr_truncated: bool,
                  is_error: bool = False,
                  is_timeout: bool = False) -> Path:
        """Write container log file"""
        timestamp = datetime.now().isoformat().replace(':', '-').replace('.', '-')
        log_type = 'TIMEOUT' if is_timeout else 'ERROR' if is_error else 'RUN'
        log_file = self.logs_dir / f'container-{timestamp}.log'
        
        is_verbose = os.getenv('LOG_LEVEL', '').lower() in ('debug', 'trace')
        
        log_lines = [
            f'=== Container {log_type} Log ===',
            f'Timestamp: {datetime.now().isoformat()}',
            f'Group: {group_name}',
            f'Container: {container_name}',
            f'Duration: {duration_ms}ms',
            f'Exit Code: {returncode}',
            f'Stdout Truncated: {stdout_truncated}',
            f'Stderr Truncated: {stderr_truncated}',
            ''
        ]
        
        if is_verbose or is_error or is_timeout:
            log_lines.extend([
                '=== Input ===',
                json.dumps(input_data.__dict__, indent=2),
                '',
                '=== Container Args ===',
                ' '.join(container_args),
                '',
                '=== Mounts ===',
                '\n'.join([f'{m.hostPath} -> {m.containerPath}{" (ro)" if m.readonly else ""}' for m in mounts]),
                '',
                f'=== Stderr{" (TRUNCATED)" if stderr_truncated else ""} ===',
                stderr_data.decode(errors='replace'),
                '',
                f'=== Stdout{" (TRUNCATED)" if stdout_truncated else ""} ===',
                stdout_data.decode(errors='replace')
            ])
        else:
            log_lines.extend([
                '=== Input Summary ===',
                f'Prompt length: {len(input_data.prompt)} chars',
                f'Session ID: {input_data.sessionId or "new"}',
                '',
                '=== Mounts ===',
                '\n'.join([f'{m.containerPath}{" (ro)" if m.readonly else ""}' for m in mounts]),
                ''
            ])
        
        log_file.write_text('\n'.join(log_lines))
        logger.debug(f'Container log written: {log_file}')
        
        return log_file


class ContainerRunner:
    """Main container runner class"""
    
    def __init__(self):
        self.runtime = ContainerRuntime()
        self.mount_builder = VolumeMountBuilder(
            project_root=Path.cwd(),
            data_dir=DATA_DIR,
            groups_dir=GROUPS_DIR
        )
        self.arg_builder = ContainerArgumentBuilder(self.runtime)
        self.output_parser = ContainerOutputParser()
    
 
    def _prepare_secrets(self) -> Dict[str, str]:
        """Read allowed secrets from environment"""
        return {
            'OPENAI_API_KEY': OPENAI_API_KEY,
        }

    def _prepare_agent_configs(self) -> Dict[str, str]:
        return {
            'OPENAI_BASE_URL': OPENAI_BASE_URL,
            'OPENAI_MODEL': OPENAI_MODEL,
            'OPENAI_ORG_ID': OPENAI_ORG_ID,
        }


    async def run_agent(
        self,
        group: RegisteredGroup,
        input_data: ContainerInput,
        on_process: Callable[[subprocess.Popen, str], None],
        on_output: Optional[Callable[[ContainerOutput], Awaitable[None]]] = None
    ) -> ContainerOutput:
        """Run agent in container"""
        start_time = datetime.now()
        
        group_dir = self.mount_builder.folder_resolver.resolve_group_folder(group.folder)
        group_dir.mkdir(parents=True, exist_ok=True)
        
        mounts = self.mount_builder.build_mounts(group, input_data.isMain)
        safe_name = self._sanitize_container_name(group.folder)
        container_name = f'nanoclaw-{safe_name}-{int(start_time.timestamp() * 1000)}'
        container_args = self.arg_builder.build_args(mounts, container_name)
        
        self._log_mounts(group.name, mounts)
        logger.info(
            f'Spawning container agent for group {group.name}: '
            f'{container_name} ({len(mounts)} mounts, isMain={input_data.isMain})'
        )
        
        # Read secrets
        input_data.secrets = self._prepare_secrets()
        input_data.configs = self._prepare_agent_configs()
        
        # Prepare process
        proc = await asyncio.create_subprocess_exec(
            self.runtime.runtime_bin,
            *container_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        on_process(proc, container_name)
        
        # Write input
        proc.stdin.write(json.dumps(input_data.__dict__).encode() + b'\n')
        await proc.stdin.drain()
        proc.stdin.close()
        
        # Remove secrets from input for logging
        input_data.secrets = None
        
        # Setup output handling
        stdout_data = bytearray()
        stderr_data = bytearray()
        stdout_truncated = False
        stderr_truncated = False
        max_size = CONTAINER_MAX_OUTPUT_SIZE
        
        # Setup timeout
        timeout_handler = ContainerTimeoutHandler(
            group=group,
            container_name=container_name,
            process=proc
        )
        timeout_task = asyncio.create_task(timeout_handler.run())
        
        # Run readers
        stdout_task = asyncio.create_task(
            self._read_stdout(proc, stdout_data, stdout_truncated, max_size, on_output)
        )
        stderr_task = asyncio.create_task(
            self._read_stderr(proc, stderr_data, stderr_truncated, max_size, group.folder)
        )
        
        # Wait for process to complete
        returncode = await proc.wait()
        
        # Cancel tasks
        stdout_task.cancel()
        stderr_task.cancel()
        timeout_task.cancel()
        
        try:
            await stdout_task
        except asyncio.CancelledError:
            pass
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass
        
        duration = (datetime.now() - start_time).total_seconds() * 1000
        
        # Handle timeout
        if timeout_handler.timed_out:
            return await self._handle_timeout(
                group, container_name, input_data, duration, returncode,
                self.output_parser.had_streaming_output, self.output_parser.new_session_id
            )
        
        # Process completion
        logger_instance = ContainerLogger(group_dir / 'logs')
        is_error = returncode != 0
        
        logger_instance.write_log(
            group_name=group.name,
            container_name=container_name,
            input_data=input_data,
            container_args=container_args,
            mounts=mounts,
            stdout_data=stdout_data,
            stderr_data=stderr_data,
            returncode=returncode,
            duration_ms=duration,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            is_error=is_error
        )
        
        if returncode != 0:
            stderr_str = stderr_data.decode(errors='replace')
            logger.error(f'Container exited with error for {group.name}: code={returncode}, stderr={stderr_str[-200:]}')
            return ContainerOutput(
                status='error',
                result=None,
                error=f'Container exited with code {returncode}: {stderr_str[-200:]}'
            )
        
        # Streaming mode
        if on_output:
            logger.info(f'Container completed (streaming mode) for {group.name}')
            return ContainerOutput(
                status='success',
                result=None,
                newSessionId=self.output_parser.new_session_id
            )
        
        # Legacy mode - parse from stdout
        return self._parse_legacy_output(stdout_data, group.name)
    
    def _sanitize_container_name(self, folder: str) -> str:
        """Sanitize folder name for container name"""
        import re
        return re.sub(r'[^a-zA-Z0-9-]', '-', folder)
    
    def _log_mounts(self, group_name: str, mounts: List[VolumeMount]) -> None:
        """Log mount configuration"""
        logger.debug(f'Container mount configuration for {group_name}:')
        for m in mounts:
            logger.debug(f'  {m.hostPath} -> {m.containerPath}{" (ro)" if m.readonly else ""}')
    
    async def _read_stdout(self, proc, stdout_data, stdout_truncated, max_size, on_output):
        """Read and process stdout"""
        output_chain = asyncio.Event()
        output_chain.set()
        
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            
            chunk = line.decode()
            
            # Accumulate for logging
            if not stdout_truncated:
                remaining = max_size - len(stdout_data)
                chunk_bytes = chunk.encode()
                if len(chunk_bytes) > remaining:
                    stdout_data.extend(chunk_bytes[:remaining])
                    stdout_truncated = True
                    logger.warn('Container stdout truncated')
                else:
                    stdout_data.extend(chunk_bytes)
            
            # Parse markers
            if on_output:
                outputs = self.output_parser.parse_chunk(chunk)
                for parsed in outputs:
                    # Wait for previous output to complete
                    await output_chain.wait()
                    output_chain.clear()
                    try:
                        await on_output(parsed)
                    finally:
                        output_chain.set()
    
    async def _read_stderr(self, proc, stderr_data, stderr_truncated, max_size, group_folder):
        """Read and log stderr"""
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            
            chunk = line.decode()
            lines = chunk.strip().split('\n')
            for l in lines:
                if l:
                    logger.debug(f'[{group_folder}] {l}')
            
            if stderr_truncated:
                continue
            
            remaining = max_size - len(stderr_data)
            chunk_bytes = chunk.encode()
            if len(chunk_bytes) > remaining:
                stderr_data.extend(chunk_bytes[:remaining])
                stderr_truncated = True
                logger.warn('Container stderr truncated')
            else:
                stderr_data.extend(chunk_bytes)
    
    async def _handle_timeout(self, group, container_name, input_data, duration, 
                             returncode, had_streaming_output, new_session_id):
        """Handle container timeout"""
        group_dir = self.mount_builder.folder_resolver.resolve_group_folder(group.folder)
        logger_instance = ContainerLogger(group_dir / 'logs')
        
        logger_instance.write_log(
            group_name=group.name,
            container_name=container_name,
            input_data=input_data,
            container_args=[],
            mounts=[],
            stdout_data=b'',
            stderr_data=b'',
            returncode=returncode,
            duration_ms=duration,
            stdout_truncated=False,
            stderr_truncated=False,
            is_timeout=True
        )
        
        if had_streaming_output:
            logger.info(f'Container timed out after output (idle cleanup) for {group.name}')
            return ContainerOutput(
                status='success',
                result=None,
                newSessionId=new_session_id
            )
        
        logger.error(f'Container timed out with no output for {group.name}')
        return ContainerOutput(
            status='error',
            result=None,
            error='Container timed out'
        )
    
    def _parse_legacy_output(self, stdout_data: bytearray, group_name: str) -> ContainerOutput:
        """Parse output in legacy mode"""
        stdout_str = stdout_data.decode(errors='replace')
        try:
            start_idx = stdout_str.find(OUTPUT_START_MARKER)
            end_idx = stdout_str.find(OUTPUT_END_MARKER)
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_line = stdout_str[
                    start_idx + len(OUTPUT_START_MARKER):end_idx
                ].strip()
            else:
                lines = stdout_str.strip().split('\n')
                json_line = lines[-1] if lines else ''
            
            output_data = json.loads(json_line)
            output = ContainerOutput(**output_data)
            
            logger.info(f'Container completed for {group_name}: status={output.status}')
            return output
        except Exception as e:
            logger.error(f'Failed to parse container output for {group_name}: {e}')
            return ContainerOutput(
                status='error',
                result=None,
                error=f'Failed to parse container output: {e}'
            )


class ContainerTimeoutHandler:
    """Handle container timeouts"""
    
    def __init__(self, group: RegisteredGroup, container_name: str, process):
        self.group = group
        self.container_name = container_name
        self.process = process
        self.timed_out = False
        self.runtime = ContainerRuntime()
        
        config_timeout = group.containerConfig.timeout if group.containerConfig else CONTAINER_TIMEOUT
        self.timeout_ms = max(config_timeout, IDLE_TIMEOUT + 30000)
    
    async def run(self):
        """Run timeout handler"""
        await asyncio.sleep(self.timeout_ms / 1000)
        self.timed_out = True
        logger.error(f'Container timeout for {self.group.name}, stopping gracefully')
        
        stop_cmd = await asyncio.create_subprocess_exec(
            *self.runtime.stop_container(self.container_name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            await asyncio.wait_for(stop_cmd.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warn(f'Graceful stop failed for {self.group.name}, force killing')
            self.process.kill()



# Maintain backward compatibility
run_container_agent = ContainerRunner().run_agent

