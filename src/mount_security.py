# mount_security.py - 面向对象版本
import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

from .config import MOUNT_ALLOWLIST_PATH
from .logger import logger
from .dtypes import AdditionalMount, AllowedRoot, MountAllowlist


class MountAllowlistLoader:
    """Load mount allowlist from config file"""
    
    DEFAULT_BLOCKED_PATTERNS: Set[str] = {
        '.ssh', '.gnupg', '.gpg', '.aws', '.azure', '.gcloud', '.kube', '.docker',
        'credentials', '.env', '.netrc', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519',
        'private_key', '.secret'
    }
    
    def __init__(self, allowlist_path: Path = MOUNT_ALLOWLIST_PATH):
        self.allowlist_path = Path(allowlist_path)
        self._cached_allowlist: Optional[MountAllowlist] = None
        self._load_error: Optional[str] = None
    
    def load(self) -> Optional[MountAllowlist]:
        """Load the mount allowlist"""
        if self._cached_allowlist is not None:
            return self._cached_allowlist
        
        if self._load_error is not None:
            return None
        
        try:
            if not self.allowlist_path.exists():
                self._load_error = f'Mount allowlist not found at {self.allowlist_path}'
                logger.warn(
                    f'Mount allowlist not found at {self.allowlist_path} - '
                    'additional mounts will be BLOCKED. Create the file to enable additional mounts.'
                )
                return None
            
            content = self.allowlist_path.read_text()
            data = json.loads(content)
            
            self._cached_allowlist = self._parse_allowlist(data)
            
            logger.info(
                f'Mount allowlist loaded successfully from {self.allowlist_path}: '
                f'{len(self._cached_allowlist.allowedRoots)} allowed roots, '
                f'{len(self._cached_allowlist.blockedPatterns)} blocked patterns'
            )
            
            return self._cached_allowlist
            
        except Exception as e:
            self._load_error = str(e)
            logger.error(f'Failed to load mount allowlist from {self.allowlist_path}: {e}')
            return None
    
    def _parse_allowlist(self, data: dict) -> MountAllowlist:
        """Parse and validate allowlist data"""
        # Validate structure
        if not isinstance(data.get('allowedRoots'), list):
            raise ValueError('allowedRoots must be an array')
        if not isinstance(data.get('blockedPatterns'), list):
            raise ValueError('blockedPatterns must be an array')
        if not isinstance(data.get('nonMainReadOnly'), bool):
            raise ValueError('nonMainReadOnly must be a boolean')
        
        # Merge with default blocked patterns
        blocked_patterns = list(set(self.DEFAULT_BLOCKED_PATTERNS) | set(data['blockedPatterns']))
        
        allowed_roots = []
        for root in data['allowedRoots']:
            allowed_roots.append(AllowedRoot(
                path=root['path'],
                allowReadWrite=root.get('allowReadWrite', False),
                description=root.get('description')
            ))
        
        return MountAllowlist(
            allowedRoots=allowed_roots,
            blockedPatterns=blocked_patterns,
            nonMainReadOnly=data['nonMainReadOnly']
        )


class PathValidator:
    """Validate paths for mounts"""
    
    @staticmethod
    def expand_path(p: str) -> Path:
        """Expand ~ to home directory and resolve to absolute path"""
        return Path(p).expanduser().resolve()
    
    @staticmethod
    def get_real_path(p: str) -> Optional[Path]:
        """Get the real path, resolving symlinks"""
        try:
            return Path(p).resolve(strict=True)
        except:
            return None
    
    @staticmethod
    def matches_blocked_pattern(real_path: Path, blocked_patterns: List[str]) -> Optional[str]:
        """Check if a path matches any blocked pattern"""
        path_str = str(real_path)
        path_parts = real_path.parts
        
        for pattern in blocked_patterns:
            # Check path components
            for part in path_parts:
                if pattern in part:
                    return pattern
            
            # Check full path
            if pattern in path_str:
                return pattern
        
        return None
    
    @staticmethod
    def find_allowed_root(
        real_path: Path,
        allowed_roots: List[AllowedRoot]
    ) -> Optional[AllowedRoot]:
        """Check if a real path is under an allowed root"""
        for root in allowed_roots:
            expanded_root = PathValidator.expand_path(root.path)
            real_root = PathValidator.get_real_path(str(expanded_root))
            
            if real_root is None:
                continue
            
            try:
                real_path.relative_to(real_root)
                return root
            except ValueError:
                continue
        
        return None
    
    @staticmethod
    def is_valid_container_path(container_path: str) -> bool:
        """Validate container path to prevent escaping"""
        if '..' in container_path:
            return False
        if container_path.startswith('/'):
            return False
        if not container_path or not container_path.strip():
            return False
        return True


class MountValidator:
    """Validate additional mounts against allowlist"""
    
    def __init__(self, allowlist_loader: Optional[MountAllowlistLoader] = None):
        self.allowlist_loader = allowlist_loader or MountAllowlistLoader()
        self.path_validator = PathValidator()
    
    def validate_mount(self, mount: AdditionalMount, is_main: bool) -> Dict[str, Any]:
        """Validate a single additional mount against the allowlist"""
        allowlist = self.allowlist_loader.load()
        
        if allowlist is None:
            return {
                'allowed': False,
                'reason': f'No mount allowlist configured at {MOUNT_ALLOWLIST_PATH}'
            }
        
        container_path = mount.containerPath or Path(mount.hostPath).name
        
        if not self.path_validator.is_valid_container_path(container_path):
            return {
                'allowed': False,
                'reason': f'Invalid container path: "{container_path}" - must be relative, non-empty, and not contain ".."'
            }
        
        expanded_path = self.path_validator.expand_path(mount.hostPath)
        real_path = self.path_validator.get_real_path(str(expanded_path))
        
        if real_path is None:
            return {
                'allowed': False,
                'reason': f'Host path does not exist: "{mount.hostPath}" (expanded: "{expanded_path}")'
            }
        
        blocked_match = self.path_validator.matches_blocked_pattern(real_path, allowlist.blockedPatterns)
        if blocked_match:
            return {
                'allowed': False,
                'reason': f'Path matches blocked pattern "{blocked_match}": "{real_path}"'
            }
        
        allowed_root = self.path_validator.find_allowed_root(real_path, allowlist.allowedRoots)
        if allowed_root is None:
            roots_str = ', '.join([str(self.path_validator.expand_path(r.path)) for r in allowlist.allowedRoots])
            return {
                'allowed': False,
                'reason': f'Path "{real_path}" is not under any allowed root. Allowed roots: {roots_str}'
            }
        
        requested_read_write = mount.readonly is False
        effective_readonly = True
        
        if requested_read_write:
            if not is_main and allowlist.nonMainReadOnly:
                effective_readonly = True
                logger.info(f'Mount "{mount.hostPath}" forced to read-only for non-main group')
            elif not allowed_root.allowReadWrite:
                effective_readonly = True
                logger.info(f'Mount "{mount.hostPath}" forced to read-only - root does not allow read-write')
            else:
                effective_readonly = False
        
        return {
            'allowed': True,
            'reason': f'Allowed under root "{allowed_root.path}"' +
                      (f' ({allowed_root.description})' if allowed_root.description else ''),
            'realHostPath': str(real_path),
            'resolvedContainerPath': container_path,
            'effectiveReadonly': effective_readonly
        }
    
    def validate_additional_mounts(
        self,
        mounts: List[AdditionalMount],
        group_name: str,
        is_main: bool
    ) -> List[Dict[str, Any]]:
        """Validate all additional mounts for a group"""
        validated = []
        
        for mount in mounts:
            result = self.validate_mount(mount, is_main)
            
            if result['allowed']:
                validated.append({
                    'hostPath': result['realHostPath'],
                    'containerPath': f'/workspace/extra/{result["resolvedContainerPath"]}',
                    'readonly': result['effectiveReadonly']
                })
                logger.debug(
                    f'Mount validated successfully for group {group_name}: '
                    f'{result["realHostPath"]} -> {result["resolvedContainerPath"]} '
                    f'(readonly: {result["effectiveReadonly"]}) - {result["reason"]}'
                )
            else:
                logger.warn(
                    f'Additional mount REJECTED for group {group_name}: '
                    f'{mount.hostPath} - {result["reason"]}'
                )
        
        return validated


class AllowlistTemplateGenerator:
    """Generate allowlist template"""
    
    @staticmethod
    def generate() -> str:
        """Generate a template allowlist file"""
        template = {
            'allowedRoots': [
                {
                    'path': '~/projects',
                    'allowReadWrite': True,
                    'description': 'Development projects'
                },
                {
                    'path': '~/repos',
                    'allowReadWrite': True,
                    'description': 'Git repositories'
                },
                {
                    'path': '~/Documents/work',
                    'allowReadWrite': False,
                    'description': 'Work documents (read-only)'
                }
            ],
            'blockedPatterns': [
                'password',
                'secret',
                'token'
            ],
            'nonMainReadOnly': True
        }
        return json.dumps(template, indent=2)


# Maintain backward compatibility
load_mount_allowlist = MountAllowlistLoader().load
validate_additional_mounts = MountValidator().validate_additional_mounts
generate_allowlist_template = AllowlistTemplateGenerator.generate
