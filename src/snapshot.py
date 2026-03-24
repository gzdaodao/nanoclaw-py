# snapshot.py
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set

from .config import (
    CONTAINER_IMAGE, CONTAINER_MAX_OUTPUT_SIZE, CONTAINER_TIMEOUT,
    DATA_DIR, GROUPS_DIR, IDLE_TIMEOUT, TIMEZONE, 
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ORG_ID
)
from .group_folder import GroupFolderResolver


class SnapshotWriter:
    """Write snapshots to IPC directory"""
    
    def __init__(self, folder_resolver: GroupFolderResolver):
        self.folder_resolver = folder_resolver
    
    def write_tasks_snapshot(
        self,
        group_folder: str,
        is_main: bool,
        tasks: List[Dict[str, Any]]
    ) -> None:
        """Write tasks snapshot to IPC directory"""
        group_ipc_dir = self.folder_resolver.resolve_group_ipc_path(group_folder)
        group_ipc_dir.mkdir(parents=True, exist_ok=True)
        
        filtered = tasks if is_main else [t for t in tasks if t['groupFolder'] == group_folder]
        
        tasks_file = group_ipc_dir / 'current_tasks.json'
        tasks_file.write_text(json.dumps(filtered, indent=2))
    
    def write_groups_snapshot(
        self,
        group_folder: str,
        is_main: bool,
        groups: List[Dict[str, Any]],
        registered_jids: Set[str]
    ) -> None:
        """Write groups snapshot to IPC directory"""
        group_ipc_dir = self.folder_resolver.resolve_group_ipc_path(group_folder)
        group_ipc_dir.mkdir(parents=True, exist_ok=True)
        
        visible = groups if is_main else []
        
        groups_file = group_ipc_dir / 'available_groups.json'
        groups_file.write_text(json.dumps({
            'groups': visible,
            'lastSync': datetime.now().isoformat()
        }, indent=2))


write_tasks_snapshot = SnapshotWriter(GroupFolderResolver(GROUPS_DIR, DATA_DIR)).write_tasks_snapshot
write_groups_snapshot = SnapshotWriter(GroupFolderResolver(GROUPS_DIR, DATA_DIR)).write_groups_snapshot

