# __init__.py
"""NanoClaw - Multi-group WhatsApp bot with containerized agents"""

__version__ = '1.0.0'

# Export main classes for easier importing
from .main import NanoClawApplication, main
from .db import Database, db_session, get_db, release_db
from .dtypes import (
    RegisteredGroup, NewMessage, ScheduledTask, TaskRunLog,
    ScheduleType, TaskStatus, Channel, ContainerConfig, AdditionalMount
)
from .logger import logger
from .group_queue import GroupQueue
from .group_folder import GroupFolderResolver, GroupFolderValidator
from .container_runner import ContainerRunner, ContainerOutput
from .container_runtime import ContainerRuntime
from .router import MessageFormatter, ChannelRouter, OutboundFormatter
from .ipc import IpcWatcher, IpcDeps
from .task_scheduler import SchedulerLoop, SchedulerDeps
from .snapshot import SnapshotWriter
