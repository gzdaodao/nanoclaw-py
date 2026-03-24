# logger.py - 面向对象版本
import logging
import sys
import os
from typing import Any, Dict, Optional
from logging.handlers import RotatingFileHandler


class Logger:
    """Simple logger wrapper"""
    
    def __init__(self, name: str = 'nanoclaw', level: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self._logfile = '/workspace/group/logs/agent-runner.log'
        
        if level is None:
            level = os.getenv('LOG_LEVEL', 'DEBUG').upper()
        
        self.logger.setLevel(level)
        
        if not self.logger.handlers:
            #handler = logging.StreamHandler(sys.stdout)
            
            log_dir = os.path.dirname(self._logfile)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            handler = RotatingFileHandler(
                    self._logfile, 
                    maxBytes=10 * 1024 * 1024, 
                    backupCount=5,
                    encoding='utf-8')

            
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
