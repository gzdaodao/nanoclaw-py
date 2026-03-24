# logger.py - 面向对象版本
import logging
import sys
import os
from typing import Any, Dict, Optional


class Logger:
    """Simple logger wrapper"""
    
    def __init__(self, name: str = 'nanoclaw', level: Optional[str] = None):
        self.logger = logging.getLogger(name)
        
        if level is None:
            level = os.getenv('LOG_LEVEL', 'INFO').upper()
        
        self.logger.setLevel(level)
        
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def debug(self, msg: str, **kwargs):
        self.logger.debug(self._format(msg, kwargs))
    
    def info(self, msg: str, **kwargs):
        self.logger.info(self._format(msg, kwargs))
    
    def warn(self, msg: str, **kwargs):
        self.logger.warning(self._format(msg, kwargs))
     
    def warning(self, msg: str, **kwargs):
        self.logger.warning(self._format(msg, kwargs))
    

    def error(self, msg: str, **kwargs):
        self.logger.error(self._format(msg, kwargs))
    
    def fatal(self, msg: str, **kwargs):
        self.logger.critical(self._format(msg, kwargs))
    
    def _format(self, msg: str, kwargs: Dict[str, Any]) -> str:
        if kwargs:
            return f"{msg} - {kwargs}"
        return msg


# Global logger instance
logger = Logger()
