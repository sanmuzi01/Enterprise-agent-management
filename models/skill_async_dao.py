"""Skill 异步 DAO。"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.init_db import Agent, Skill


async def get_skill_by_id_async(db: AsyncSession, skill_id: int) -> Optional[Skill]:
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    return result.scalars().first()


async def list_skills_by_user_async(db: AsyncSession, user_id: int) -> List[Skill]:
    result = await db.execute(select(Skill).where(Skill.user_id == user_id).order_by(Skill.id.desc()))
    return list(result.unique().scalars().all())


async def list_all_skills_async(db: AsyncSession, limit: int = 1000) -> List[Skill]:
    result = await db.execute(select(Skill).order_by(Skill.id.desc()).limit(limit))
    return list(result.unique().scalars().all())


async def list_public_skills_async(db: AsyncSession) -> List[Skill]:
    # 只给已发布的——跟同步版 models/skill_dao.py::list_public_skills 保持一致的过滤。
    result = await db.execute(
        select(Skill).where(Skill.is_public == 1, Skill.lifecycle_status == "published")
        .order_by(Skill.id.desc())
    )
    return list(result.unique().scalars().all())


async def list_skills_by_agent_async(db: AsyncSession, agent_id: int) -> List[Skill]:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .options(selectinload(Agent.skills))
    )
    agent = result.unique().scalars().first()
    return list(agent.skills) if agent else []
