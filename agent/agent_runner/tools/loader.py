# agents/tools/loader.py
"""Dynamic plugin loader for tools."""

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional, Type, Set
import sys
import traceback

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolContext
from agent_runner.logger import logger


current_package = __package__


class PluginLoader:
    """Dynamic plugin loader for tools"""
    
    def __init__(self, plugin_dirs: List[Path]):
        self.plugin_dirs = plugin_dirs
        self.plugins: Dict[str, ToolPlugin] = {}
        self.tools: Dict[str, BaseTool] = {}
        self._loaded_modules: Set[str] = set()
    
    def discover_plugins(self) -> List[str]:
        """Discover available plugins in plugin directories"""
        discovered = []
        
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists():
                logger.debug(f"Plugin directory not found: {plugin_dir}")
                continue
            
            # Add to Python path
            if str(plugin_dir) not in sys.path:
                sys.path.insert(0, str(plugin_dir))
            
            # Discover plugins
            for finder, name, ispkg in pkgutil.iter_modules([str(plugin_dir)]):
                if name not in self._loaded_modules:
                    discovered.append(name)
                    logger.debug(f"Discovered plugin module: {name}")
        
        return discovered
    
    async def load_plugin(self, module_name: str, context: ToolContext) -> Optional[ToolPlugin]:
        """Load a specific plugin by module name"""
        try:
            # Import module
            module = importlib.import_module(module_name)
            self._loaded_modules.add(module_name)
            
            # Find plugin class
            plugin_class = None
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, ToolPlugin) and 
                    obj != ToolPlugin):
                    plugin_class = obj
                    break
            
            if not plugin_class:
                logger.warning(f"No ToolPlugin subclass found in {module_name}")
                return None
            
            # Instantiate and initialize plugin
            plugin = plugin_class()
            await plugin.initialize(context)
            
            # Register all tools from plugin
            self.plugins[plugin.name] = plugin
            for tool_metadata in plugin.list_tools():
                tool = plugin.get_tool(tool_metadata.name)
                if tool:
                    self.tools[tool_metadata.name] = tool
                    logger.info(f"Loaded tool: {tool_metadata.name} from plugin {plugin.name}")
            
            logger.info(f"Loaded plugin: {plugin.name} v{plugin.version}")
            return plugin
            
        except Exception as e:
            logger.error(f"Failed to load plugin {module_name}: {e}")
            logger.debug(traceback.format_exc())
            return None
    
    async def load_all_plugins(self, context: ToolContext) -> Dict[str, ToolPlugin]:
        """Discover and load all plugins"""
        discovered = self.discover_plugins()
        
        for module_name in discovered:
            await self.load_plugin(module_name, context)
        
        return self.plugins
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get tool by name"""
        return self.tools.get(name)
    
    def get_plugin(self, name: str) -> Optional[ToolPlugin]:
        """Get plugin by name"""
        return self.plugins.get(name)
    
    def list_tools(self, category: Optional[str] = None) -> List[Dict]:
        """List all loaded tools, optionally filtered by category"""
        tools = []
        for tool in self.tools.values():
            if category and tool.metadata.category != category:
                continue
            tools.append({
                "name": tool.metadata.name,
                "description": tool.metadata.description,
                "category": tool.metadata.category,
                "plugin": self._find_plugin_for_tool(tool.metadata.name),
                "version": tool.metadata.version
            })
        return tools
    
    def _find_plugin_for_tool(self, tool_name: str) -> Optional[str]:
        """Find plugin name that contains a tool"""
        for plugin_name, plugin in self.plugins.items():
            if plugin.get_tool(tool_name):
                return plugin_name
        return None
    
    def get_openai_functions(self, categories: Optional[List[str]] = None) -> List[Dict]:
        """Get OpenAI function specifications for all tools in specified categories"""
        functions = []
        for tool in self.tools.values():
            if categories and tool.metadata.category not in categories:
                continue
            functions.append(tool.get_openai_function_spec())
        return functions
    
    async def cleanup(self):
        """Cleanup all plugins"""
        for plugin in self.plugins.values():
            try:
                await plugin.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up plugin {plugin.name}: {e}")
        self.plugins.clear()
        self.tools.clear()
