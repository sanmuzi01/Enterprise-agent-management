"""用户画像异步 DAO。"""

from utils.timeutil import utcnow
from typing import Optional


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.init_db import UserProfile


async def get_user_profile_async(db: AsyncSession, user_id: int) -> Optional[UserProfile]:
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return result.scalars().first()


async def upsert_user_profile_async(db: AsyncSession, user_id: int, payload: dict) -> UserProfile:
    profile = await get_user_profile_async(db, user_id)
    now = utcnow()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
    for key, value in payload.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    profile.updated_at = now
    await db.flush()
    return profile
