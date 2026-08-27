# container_runtime.py - 面向对象版本
import subprocess
import time
from typing import List, Optional

from .logger import logger


class ContainerRuntime:
    """Container runtime manager"""
    
    def __init__(self, runtime_bin: str = 'docker', ex_args: List[str] = None):
        self.runtime_bin = runtime_bin
        self.ex_args = ex_args
    
    def readonly_mount_args(self, host_path: str, container_path: str) -> List[str]:
        """Return CLI args for a readonly bind mount"""
        return ['-v', f'{host_path}:{container_path}:ro']
    
    def stop_container(self, name: str) -> List[str]:
        """Return command to stop a container by name"""
        return [self.runtime_bin, 'stop', name]
    
    def ensure_running(self) -> None:
        """Ensure the container runtime is running"""
        try:
            subprocess.run(
                [self.runtime_bin, 'info'],
                capture_output=True,
                timeout=10,
                check=True
            )
            logger.debug('Container runtime already running')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f'Failed to reach container runtime: {e}')
            self._show_error_message()
            raise RuntimeError('Container runtime is required but failed to start')
    
    def cleanup_orphans(self) -> None:
        """Kill orphaned NanoClaw containers from previous runs"""
        try:
            result = subprocess.run(
                [self.runtime_bin, 'ps', '--filter', 'name=nanoclaw-', '--format', '{{.Names}}'],
                capture_output=True,
                text=True,
                check=False
            )
            orphans = [name.strip() for name in result.stdout.split('\n') if name.strip()]
            
            for name in orphans:
                try:
                    subprocess.run(self.stop_container(name), capture_output=True, check=False)
                except:
                    pass
            
            if orphans:
                logger.info(f'Stopped orphaned containers: {orphans}')
        except Exception as e:
            logger.warn(f'Failed to clean up orphaned containers: {e}')
    
    def _show_error_message(self) -> None:
        """Show error message to user"""
        print('\n' + '╔' + '═' * 70 + '╗')
        print('║  FATAL: Container runtime failed to start                      ║')
        print('║                                                                ║')
        print('║  Agents cannot run without a container runtime. To fix:        ║')
        print('║  1. Ensure Docker is installed and running                     ║')
        print('║  2. Run: docker info                                           ║')
        print('║  3. Restart NanoClaw                                           ║')
        print('╚' + '═' * 70 + '╝\n')


# Maintain backward compatibility

from .config import CONTAINER_RUNTIME_BIN, CONTAINER_EX_ARGS

readonly_mount_args = ContainerRuntime(runtime_bin=CONTAINER_RUNTIME_BIN, ex_args=CONTAINER_EX_ARGS).readonly_mount_args
stop_container = ContainerRuntime(runtime_bin=CONTAINER_RUNTIME_BIN, ex_args=CONTAINER_EX_ARGS).stop_container
ensure_container_runtime_running = ContainerRuntime(runtime_bin=CONTAINER_RUNTIME_BIN, ex_args=CONTAINER_EX_ARGS).ensure_running
cleanup_orphans = ContainerRuntime(runtime_bin=CONTAINER_RUNTIME_BIN, ex_args=CONTAINER_EX_ARGS).cleanup_orphans
