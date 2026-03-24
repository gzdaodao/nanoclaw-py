# group_folder.py - 面向对象版本
import re
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, GROUPS_DIR
from .logger import logger


class GroupFolderValidator:
    """Validate group folder names"""
    
    GROUP_FOLDER_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    RESERVED_FOLDERS = {'global'}
    
    @classmethod
    def is_valid(cls, folder: str) -> bool:
        """Validate group folder name"""
        if not folder:
            return False
        if folder != folder.strip():
            return False
        if not cls.GROUP_FOLDER_PATTERN.match(folder):
            return False
        if '/' in folder or '\\' in folder:
            return False
        if '..' in folder:
            return False
        if folder.lower() in cls.RESERVED_FOLDERS:
            return False
        return True
    
    @classmethod
    def assert_valid(cls, folder: str) -> None:
        """Assert folder is valid"""
        if not cls.is_valid(folder):
            raise ValueError(f'Invalid group folder "{folder}"')


class PathSecurity:
    """Security utilities for paths"""
    
    @staticmethod
    def ensure_within_base(base_dir: Path, resolved_path: Path) -> None:
        """Ensure path is within base directory"""
        try:
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f'Path escapes base directory: {resolved_path}')


class GroupFolderResolver:
    """Resolve group folder paths"""
    
    def __init__(self, groups_dir: Path = GROUPS_DIR, data_dir: Path = DATA_DIR):
        self.groups_dir = groups_dir
        self.data_dir = data_dir
        self.validator = GroupFolderValidator()
        self.path_security = PathSecurity()
    
    def resolve_group_folder(self, folder: str) -> Path:
        """Resolve group folder path"""
        self.validator.assert_valid(folder)
        group_path = (self.groups_dir / folder).resolve()
        self.path_security.ensure_within_base(self.groups_dir, group_path)
        return group_path
    
    def resolve_group_ipc_path(self, folder: str) -> Path:
        """Resolve group IPC path"""
        self.validator.assert_valid(folder)
        ipc_base_dir = (self.data_dir / 'ipc').resolve()
        ipc_path = (ipc_base_dir / folder).resolve()
        self.path_security.ensure_within_base(ipc_base_dir, ipc_path)
        return ipc_path


# Maintain backward compatibility
is_valid_group_folder = GroupFolderValidator.is_valid
assert_valid_group_folder = GroupFolderValidator.assert_valid
ensure_within_base = PathSecurity.ensure_within_base
resolve_group_folder_path = GroupFolderResolver().resolve_group_folder
resolve_group_ipc_path = GroupFolderResolver().resolve_group_ipc_path
