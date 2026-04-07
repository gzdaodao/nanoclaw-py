# agents/tools/builtin/skill_tools.py
"""Skill management tools plugin."""

from pathlib import Path
from typing import Optional, List, Dict, Any
import uuid as uuid_lib

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult
from agent_runner.logger import logger
from agent_runner.skills import get_skill_loader, Skill, delete_skill, create_skill


class GetSkillDetailTool(BaseTool):
    """Tool to get skill details"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_skill_detail",
            description="Get detailed information about a skill including its README content",
            parameters={
                "identifier": {
                    "type": "string",
                    "description": "Skill identifier (UUID or name)",
                    "required": True
                }
            },
            category="skills",
            required_permissions=["skills:read"],
            version="1.0.0",
            tags=["skill", "read", "query"]
        )
    
    async def execute(self, identifier: str) -> ToolResult:
        """Execute get skill detail"""
        try:
            loader = get_skill_loader()
            
            # 确保技能已加载
            if not loader._loaded:
                loader.load_all_skills()
            
            skill_detail = loader.get_skill_detail(identifier)
            
            if not skill_detail:
                return ToolResult.fail(f"Skill not found: {identifier}")
            
            return ToolResult.ok(skill_detail)
            
        except Exception as e:
            logger.error(f"Failed to get skill detail: {e}")
            return ToolResult.fail(f"Failed to get skill detail: {e}")


class SearchSkillsTool(BaseTool):
    """Tool to search skills"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="search_skills",
            description="Search for skills by name or description",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Search query string",
                    "required": True
                }
            },
            category="skills",
            required_permissions=["skills:read"],
            version="1.0.0",
            tags=["skill", "search", "query"]
        )
    
    async def execute(self, query: str) -> ToolResult:
        """Execute search skills"""
        try:
            loader = get_skill_loader()
            
            # 确保技能已加载
            if not loader._loaded:
                loader.load_all_skills()
            
            results = loader.search_skills(query)
            
            return ToolResult.ok({
                "results": results,
                "count": len(results),
                "query": query
            })
            
        except Exception as e:
            logger.error(f"Failed to search skills: {e}")
            return ToolResult.fail(f"Failed to search skills: {e}")


class ListSkillsTool(BaseTool):
    """Tool to list all skills"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="list_skills",
            description="List all available skills with their names and descriptions",
            parameters={
                "include_summary": {
                    "type": "boolean",
                    "description": "Include formatted summary text",
                    "default": False
                }
            },
            category="skills",
            required_permissions=["skills:read"],
            version="1.0.0",
            tags=["skill", "list", "query"]
        )
    
    async def execute(self, include_summary: bool = False) -> ToolResult:
        """Execute list skills"""
        try:
            loader = get_skill_loader()
            
            # 确保技能已加载
            if not loader._loaded:
                loader.load_all_skills()
            
            skills = loader.get_all_skills()
            
            result = {
                "skills": skills,
                "count": len(skills)
            }
            
            if include_summary:
                result["summary"] = loader.get_skills_summary()
            
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to list skills: {e}")
            return ToolResult.fail(f"Failed to list skills: {e}")


class CreateSkillTool(BaseTool):
    """Tool to create a new skill"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="create_skill",
            description="Create a new skill with the given name, description, and content",
            parameters={
                "name": {
                    "type": "string",
                    "description": "Skill name (must be unique)",
                    "required": True
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the skill",
                    "required": True
                },
                "content": {
                    "type": "string",
                    "description": "Detailed skill content in Markdown format (skill.md)",
                    "required": True
                }
            },
            category="skills",
            required_permissions=["skills:write"],
            version="1.0.0",
            tags=["skill", "create", "write"]
        )
    
    async def execute(self, name: str, description: str, content: str) -> ToolResult:
        """Execute create skill"""
        try:
            # 验证参数
            if not name or not name.strip():
                return ToolResult.fail("Skill name cannot be empty")
            
            if not description or not description.strip():
                return ToolResult.fail("Skill description cannot be empty")
            
            if not content or not content.strip():
                return ToolResult.fail("Skill content cannot be empty")
            
            loader = get_skill_loader()
            
            # 确保技能已加载
            if not loader._loaded:
                loader.load_all_skills()
            
            # 检查名称是否已存在
            existing = loader.get_skill(name)
            if existing:
                return ToolResult.fail(f"Skill with name '{name}' already exists")
            
            # 创建技能
            result = create_skill(name.strip(), description.strip(), content.strip())
            
            if not result:
                return ToolResult.fail(f"Failed to create skill: {name}")
            
            logger.info(f"Skill created successfully: {name} ({result['uuid']})")
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to create skill: {e}")
            return ToolResult.fail(f"Failed to create skill: {e}")


class DeleteSkillTool(BaseTool):
    """Tool to delete a skill"""
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="delete_skill",
            description="Delete a skill by UUID or name",
            parameters={
                "identifier": {
                    "type": "string",
                    "description": "Skill identifier (UUID or name)",
                    "required": True
                },
                "force": {
                    "type": "boolean",
                    "description": "Force delete without confirmation",
                    "default": False
                }
            },
            category="skills",
            required_permissions=["skills:write"],
            version="1.0.0",
            tags=["skill", "delete", "remove"]
        )
    
    async def execute(self, identifier: str, force: bool = False) -> ToolResult:
        """Execute delete skill"""
        try:
            if not identifier or not identifier.strip():
                return ToolResult.fail("Skill identifier cannot be empty")
            
            loader = get_skill_loader()
            
            # 确保技能已加载
            if not loader._loaded:
                loader.load_all_skills()
            
            # 调用封装好的删除方法
            result = delete_skill(identifier.strip(), force)
            
            if not result:
                return ToolResult.fail(f"Skill not found: {identifier}")
            
            # 如果需要确认
            if result.get("requires_confirmation"):
                return ToolResult.fail(result.get("message", "Confirmation required"))
            
            # 删除成功
            logger.info(f"Skill deleted: {result['name']} ({result['uuid']})")
            return ToolResult.ok(result)
            
        except Exception as e:
            logger.error(f"Failed to delete skill: {e}")
            return ToolResult.fail(f"Failed to delete skill: {e}")


class SkillsPlugin(ToolPlugin):
    """Plugin providing skill management tools"""
    
    def __init__(self):
        super().__init__()
        self.skills_root: Optional[Path] = None
    
    @property
    def name(self) -> str:
        return "skills"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    async def initialize(self, context: ToolContext) -> None:
        """Initialize skills plugin"""
        # 设置技能根目录
        if context.workspace_dir:
            self.skills_root = context.workspace_dir / "skills"
        else:
            self.skills_root = Path("/workspace/group/skills")
        
        # 更新技能加载器的根目录
        from agent_runner.skills import _skill_loader
        if _skill_loader:
            _skill_loader.skills_root = self.skills_root
        
        # 注册所有技能管理工具
        self.register_tool(GetSkillDetailTool(context))
        self.register_tool(SearchSkillsTool(context))
        self.register_tool(ListSkillsTool(context))
        self.register_tool(CreateSkillTool(context))
        self.register_tool(DeleteSkillTool(context))
        
        # 预加载技能
        try:
            from agent_runner.skills import load_all_skills
            count = load_all_skills()
            logger.info(f"Skills plugin initialized with {count} skills loaded")
        except Exception as e:
            logger.warning(f"Failed to pre-load skills: {e}")
        
        logger.info(f"Skills plugin initialized with {len(self._tools)} tools")
    
    async def cleanup(self) -> None:
        """Cleanup plugin resources"""
        logger.info("Skills plugin cleaned up")
