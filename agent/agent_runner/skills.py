# agents/skills/loader.py
"""极简分布式技能加载器 - 每个技能独立索引文件"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from .logger import logger


@dataclass
class Skill:
    """极简技能类"""
    uuid: str
    name: str
    description: str
    path: Path  # 技能目录路径
    readme: str = ""  # skill.md 内容，懒加载


class SkillLoader:
    """极简技能加载器 - 每个技能独立索引"""
    
    def __init__(self, skills_root: Path = Path("/workspace/group/skills")):
        self.skills_root = skills_root
        self.skills: Dict[str, Skill] = {}  # uuid -> Skill
        self.skills_by_name: Dict[str, Skill] = {}  # name -> Skill
        self._loaded = False
        self._load_time: Optional[datetime] = None
    
    def load_all_skills(self) -> int:
        """加载所有技能 - 扫描每个子目录的 index.json"""
        if not self.skills_root.exists():
            logger.warning(f"Skills directory not found: {self.skills_root}")
            return 0
        
        self.skills.clear()
        self.skills_by_name.clear()
        
        count = 0
        for skill_dir in self.skills_root.iterdir():
            if not skill_dir.is_dir():
                continue
            
            # 读取 index.json
            index_file = skill_dir / "index.json"
            if not index_file.exists():
                logger.debug(f"No index.json in {skill_dir}, skipping")
                continue
            
            try:
                data = json.loads(index_file.read_text(encoding='utf-8'))
                
                # 验证必要字段
                if not all(k in data for k in ["uuid", "name", "description"]):
                    logger.warning(f"Invalid index.json in {skill_dir}: missing required fields")
                    continue
                
                skill = Skill(
                    uuid=data["uuid"],
                    name=data["name"],
                    description=data["description"],
                    path=skill_dir
                )
                
                self.skills[skill.uuid] = skill
                self.skills_by_name[skill.name] = skill
                count += 1
                
                logger.debug(f"Loaded skill: {skill.name} ({skill.uuid})")
                
            except Exception as e:
                logger.error(f"Failed to load skill from {skill_dir}: {e}")
        
        self._loaded = True
        self._load_time = datetime.now()
        logger.info(f"Loaded {count} skills from {self.skills_root}")
        return count
    
    def get_skill(self, identifier: str) -> Optional[Skill]:
        """通过 UUID 或名称获取技能"""
        # 先按 UUID 查找
        if identifier in self.skills:
            return self.skills[identifier]
        
        # 再按名称查找
        if identifier in self.skills_by_name:
            return self.skills_by_name[identifier]
        
        return None
    
    def get_skill_readme(self, skill: Skill) -> str:
        """获取技能详细描述（懒加载）"""
        if skill.readme:
            return skill.readme
        
        readme_path = skill.path / "skill.md"
        if readme_path.exists():
            try:
                skill.readme = readme_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"Failed to read skill.md for {skill.name}: {e}")
                skill.readme = ""
        else:
            skill.readme = ""
        
        return skill.readme
    
    def search_skills(self, query: str) -> List[Dict[str, Any]]:
        """搜索技能 - 简单文本匹配"""
        query = query.lower()
        results = []
        
        for skill in self.skills.values():
            score = 0
            
            # 名称匹配
            if query in skill.name.lower():
                score += 10
            
            # 描述匹配
            if query in skill.description.lower():
                score += 5
            
            if score > 0:
                results.append({
                    "uuid": skill.uuid,
                    "name": skill.name,
                    "description": skill.description,
                    "relevance": score
                })
        
        # 按相关度排序
        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results
    
    def get_all_skills(self) -> List[Dict[str, str]]:
        """获取所有技能列表"""
        return [
            {
                "uuid": s.uuid,
                "name": s.name,
                "description": s.description
            }
            for s in self.skills.values()
        ]
    
    def get_skills_summary(self) -> str:
        """生成技能摘要文本（用于系统提示词）"""
        if not self.skills:
            return "No skills available."
        
        lines = ["## Available Skills:"]
        for skill in self.skills.values():
            lines.append(f"- **{skill.name}**: {skill.description}")
        
        return "\n".join(lines)
    
    def get_skill_detail(self, identifier: str) -> Optional[Dict[str, Any]]:
        """获取技能详细信息（包含 README）"""
        skill = self.get_skill(identifier)
        if not skill:
            return None
        
        readme = self.get_skill_readme(skill)
        
        return {
            "uuid": skill.uuid,
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.path),
            "readme": readme,
            "readme_preview": readme[:200] + "..." if len(readme) > 200 else readme
        }


# 全局单例
_skill_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """获取全局技能加载器单例"""
    global _skill_loader
    if _skill_loader is None:
        _skill_loader = SkillLoader()
    return _skill_loader


def load_all_skills() -> int:
    """加载所有技能"""
    return get_skill_loader().load_all_skills()


def get_skill(identifier: str) -> Optional[Skill]:
    """获取技能"""
    return get_skill_loader().get_skill(identifier)


def get_skill_detail(identifier: str) -> Optional[Dict]:
    """获取技能详细信息"""
    return get_skill_loader().get_skill_detail(identifier)


def search_skills(query: str) -> List[Dict]:
    """搜索技能"""
    return get_skill_loader().search_skills(query)


def get_skills_summary() -> str:
    """获取技能摘要"""
    return get_skill_loader().get_skills_summary()


def get_all_skills() -> List[Dict]:
    """获取所有技能列表"""
    return get_skill_loader().get_all_skills()
