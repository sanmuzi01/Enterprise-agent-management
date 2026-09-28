from typing import Optional,List
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session
from utils.logger_handler import get_logger
from service.exceptions import Conflict
from models.init_db import Skill,Agent,agent_skill
logger = get_logger("skill_dao")

def create_skill(db:Session,user_id:int,name:str,description:str,config_file:str,
                 is_public:int = 0)->Optional[Skill]:
    #创建Skill记录
    try:
        skill = Skill(
            user_id=user_id, name=name, description=description,
            config_file=config_file, is_public=is_public
        )
        db.add(skill)
        db.flush()
        db.refresh(skill)
        logger.info(f"创建Skill成功: id={skill.id}, name={name}, user={user_id}")
        return skill
    except Exception as e:
        logger.error(f"创建Skill失败: {e}")
        db.rollback()
        return None

def get_skill_by_id(db:Session,skill_id:int)->Optional[Skill]:
    #按id查询
    return db.query(Skill).filter(Skill.id == skill_id).first()
def list_skills_by_user(db: Session, user_id: int) -> List[Skill]:
    #查询用户的所有skill
    return db.query(Skill).filter(Skill.user_id == user_id).all()
def list_public_skills(db: Session) -> List[Skill]:
    # 查询所有公开且已发布的 skill：给普通用户浏览/绑定用。草稿/待审核/已退役的
    # 公开 skill 不在这里出现——就算列出来，绑定时 can_bind_skill 也会拒绝非作者，
    # 不如干脆不让看到用不了的选项。管理员看全部（含草稿）走 list_all_skills，
    # 那个不受这个过滤影响。
    return db.query(Skill).filter(Skill.is_public == 1, Skill.lifecycle_status == "published").all()
def list_all_skills(db: Session) -> List[Skill]:
    #查询所有skill：管理员
    return db.query(Skill).all()
def update_skill(db: Session, skill_id: int, expected_row_version: Optional[int] = None, **kwargs) -> Optional[Skill]:
    """更新skill（仅允许更新 name/description/config_file/is_public/lifecycle_status）。

    `expected_row_version` 不传就是旧行为（不做版本比对，兼容 import/translate/reanalyze
    这类还不知道版本号概念的既有调用方）。传了就是乐观锁：一条 `UPDATE ... WHERE
    id=:id AND row_version=:expected` 原子完成"比对+写入"，不是先 SELECT 再在 Python
    里比对——那样两个并发请求可能都读到同一个旧版本，都以为自己没有冲突，都写了进去。
    受影响行数为 0 且记录本身还在，就是版本冲突，抛 `Conflict`（跟
    service/approval_service.py::try_consume_approved 是同一个模式）。
    """
    try:
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            logger.warning(f"更新Skill失败：不存在 id={skill_id}")
            return None
        allowed_fields = {"name", "description", "config_file", "is_public", "lifecycle_status"}
        values = {key: value for key, value in kwargs.items() if key in allowed_fields}
        values["row_version"] = Skill.row_version + 1
        stmt = sa_update(Skill).where(Skill.id == skill_id)
        if expected_row_version is not None:
            stmt = stmt.where(Skill.row_version == expected_row_version)
        result = db.execute(stmt.values(**values))
        if result.rowcount == 0:
            db.refresh(skill)
            raise Conflict(
                f"Skill 已被其他人修改（当前版本 {skill.row_version}，"
                f"你读到的是 {expected_row_version}），请刷新后重试"
            )
        db.flush()
        db.refresh(skill)
        logger.info(f"更新Skill成功: id={skill_id}")
        return skill
    except Conflict:
        raise
    except Exception as e:
        logger.error(f"更新Skill失败: id={skill_id}, error={e}")
        db.rollback()
        return None
def delete_skill(db: Session, skill_id: int) -> bool:
    #删除skill同时解除与agent的绑定
    try:
        skill=db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            logger.warning(f"删除Skill失败：不存在 id={skill_id}")
            return False
        db.delete(skill)
        db.flush()
        logger.info(f"删除Skill成功: id={skill_id}")
        return True
    except Exception as e:
        logger.error(f"删除Skill失败: id={skill_id}, error={e}")
        db.rollback()
        return False
def bind_skill_to_agent(db: Session, agent_id: int, skill_id: int) -> bool:
    #skill绑定agent，多对多
    try:
        #检查是否已经绑定
        exists = db.query(agent_skill).filter(
            agent_skill.c.agent_id == agent_id,
            agent_skill.c.skill_id == skill_id).first()
        if exists:
            logger.info(f"Skill已绑定到Agent: agent={agent_id}, skill={skill_id}")
            return True

        stmt = agent_skill.insert().values(agent_id=agent_id, skill_id=skill_id)
        db.execute(stmt)
        db.flush()
        logger.info(f"绑定Skill到Agent: agent={agent_id}, skill={skill_id}")
        return True
    except Exception as e:
        logger.error(f"绑定Skill失败: agent={agent_id}, skill={skill_id}, error={e}")
        db.rollback()
        return False
def unbind_skill_from_agent(db: Session, agent_id: int, skill_id: int) -> bool:
    """解除Skill和Agent的绑定"""
    try:
        stmt = agent_skill.delete().where(
            agent_skill.c.agent_id == agent_id,
            agent_skill.c.skill_id == skill_id,
        )
        result = db.execute(stmt)
        db.flush()
        logger.info(f"解绑Skill: agent={agent_id}, skill={skill_id}, 删除{result.rowcount}行")
        return True
    except Exception as e:
        logger.error(f"解绑Skill失败: agent={agent_id}, skill={skill_id}, error={e}")
        db.rollback()
        return False
def list_skills_by_agent(db: Session, agent_id: int) -> List[Skill]:
    #查询agent绑定的skill
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return []
    return agent.skills

def unbind_all_skills_from_agent(db: Session, agent_id: int) -> bool:
    """解除Agent的所有Skill绑定（批量更新时用）"""
    try:
        stmt = agent_skill.delete().where(agent_skill.c.agent_id == agent_id)
        db.execute(stmt)
        db.flush()
        logger.info(f"解绑Agent所有Skill: agent={agent_id}")
        return True
    except Exception as e:
        logger.error(f"解绑Agent所有Skill失败: agent={agent_id}, error={e}")
        db.rollback()
        return False