# agents/skills/loader.py
"""极简分布式技能加载器 - 每个技能独立索引文件"""

import json
import shutil
import uuid as uuid_lib
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
    readme: str = ""  # SKILL.md 内容，懒加载


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
    
    def create_skill(self, name: str, description: str, content: str) -> Optional[Dict[str, Any]]:
        """
        创建新技能
        
        Args:
            name: 技能名称
            description: 技能简短描述
            content: 技能详细内容 (SKILL.md)
            
        Returns:
            创建成功的技能信息字典，失败返回 None
        """
        # 检查技能名称是否已存在
        if name in self.skills_by_name:
            logger.warning(f"Skill with name '{name}' already exists")
            return None
        
        # 生成 UUID
        skill_uuid = str(uuid_lib.uuid4())
        
        # 创建技能目录（使用 UUID 作为目录名）
        skill_dir = self.skills_root / skill_uuid
        try:
            skill_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            logger.error(f"Skill directory already exists: {skill_dir}")
            return None
        except Exception as e:
            logger.error(f"Failed to create skill directory: {e}")
            return None
        
        # 写入 index.json
        index_data = {
            "uuid": skill_uuid,
            "name": name,
            "description": description,
            "created_at": datetime.now().isoformat()
        }
        
        index_file = skill_dir / "index.json"
        try:
            index_file.write_text(
                json.dumps(index_data, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error(f"Failed to write index.json: {e}")
            # 清理已创建的目录
            try:
                skill_dir.rmdir()
            except Exception:
                pass
            return None
        
        # 写入 SKILL.md
        readme_file = skill_dir / "SKILL.md"
        try:
            readme_file.write_text(content, encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to write SKILL.md: {e}")
            # 清理已创建的文件和目录
            try:
                index_file.unlink()
                skill_dir.rmdir()
            except Exception:
                pass
            return None
        
        # 创建 Skill 对象并加入缓存
        skill = Skill(
            uuid=skill_uuid,
            name=name,
            description=description,
            path=skill_dir,
            readme=content  # 预填充 readme 缓存
        )
        
        self.skills[skill_uuid] = skill
        self.skills_by_name[name] = skill
        
        logger.info(f"Created skill: {name} ({skill_uuid})")
        
        return {
            "uuid": skill_uuid,
            "name": name,
            "description": description,
            "path": str(skill_dir),
            "readme": content
        }
    
    def delete_skill(self, identifier: str, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        删除技能
        
        Args:
            identifier: 技能标识符（UUID 或名称）
            force: 是否强制删除（如果为 False，仅返回确认信息）
            
        Returns:
            删除成功的技能信息字典，失败返回 None
        """
        # 获取技能
        skill = self.get_skill(identifier)
        if not skill:
            logger.warning(f"Skill not found: {identifier}")
            return None
        
        # 如果不是强制删除，返回确认信息
        if not force:
            logger.info(f"Delete requested for skill '{skill.name}' ({skill.uuid}) but force=False")
            return {
                "uuid": skill.uuid,
                "name": skill.name,
                "description": skill.description,
                "path": str(skill.path),
                "requires_confirmation": True,
                "message": f"Skill '{skill.name}' found. Set force=True to delete permanently."
            }
        
        try:
            # 保存技能信息以便返回
            skill_info = {
                "uuid": skill.uuid,
                "name": skill.name,
                "description": skill.description,
                "path": str(skill.path),
                "deleted": False
            }
            
            # 删除技能目录及其内容
            if skill.path.exists():
                shutil.rmtree(skill.path)
                logger.info(f"Deleted skill directory: {skill.path}")
                skill_info["deleted"] = True
            else:
                logger.warning(f"Skill directory does not exist: {skill.path}")
                skill_info["deleted"] = False
                skill_info["warning"] = "Directory did not exist"
            
            # 从缓存中移除
            self.skills.pop(skill.uuid, None)
            self.skills_by_name.pop(skill.name, None)
            
            logger.info(f"Skill deleted successfully: {skill.name} ({skill.uuid})")
            return skill_info
            
        except Exception as e:
            logger.error(f"Failed to delete skill '{skill.name}': {e}")
            return None
    
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
        
        readme_path = skill.path / "SKILL.md"
        if readme_path.exists():
            try:
                skill.readme = readme_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"Failed to read SKILL.md for {skill.name}: {e}")
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


def create_skill(name: str, description: str, content: str) -> Optional[Dict]:
    """
    创建新技能（便捷函数）
    
    Args:
        name: 技能名称
        description: 技能简短描述
        content: 技能详细内容 (SKILL.md)
        
    Returns:
        创建成功的技能信息字典，失败返回 None
    """
    return get_skill_loader().create_skill(name, description, content)


def delete_skill(identifier: str, force: bool = False) -> Optional[Dict]:
    """
    删除技能（便捷函数）
    
    Args:
        identifier: 技能标识符（UUID 或名称）
        force: 是否强制删除
        
    Returns:
        删除成功的技能信息字典，失败返回 None
    """
    return get_skill_loader().delete_skill(identifier, force)
