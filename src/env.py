# env.py - 面向对象版本
from pathlib import Path
from typing import Dict, List, Optional

from .logger import logger


class EnvFileReader:
    """Read environment variables from .env file"""
    
    def __init__(self, env_path: Optional[Path] = None):
        self.env_path = env_path or (Path.cwd() / '.env')
    
    def read_values(self, keys: List[str]) -> Dict[str, str]:
        """Read values for requested keys from .env file"""
        try:
            content = self.env_path.read_text()
        except Exception as e:
            logger.debug(f'.env file not found: {e}')
            return {}
        
        result = {}
        wanted = set(keys)
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if '=' not in line:
                continue
            
            key, value = line.split('=', 1)
            key = key.strip()
            if key not in wanted:
                continue
            
            value = value.strip()
            # Remove quotes
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            
            if value:
                result[key] = value
        
        return result


# Maintain backward compatibility
read_env_file = EnvFileReader().read_values
