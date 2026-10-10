"""技能批量上架 / 下架（管理员）。

“上架”= 出现在用户的技能中心：公开（is_public=1）且已发布（lifecycle_status=published），两个条件缺一不可。
以前只能一个个打开编辑弹窗改“发布状态”，批量导入的技能（导入只会设成公开、状态是草稿）就一直停在“公开的草稿”，
用户的技能中心是空的。这里一次处理一批：
  - 上架：设为公开 + 已发布；
  - 下架：退回草稿（公开标记保留，下次上架不用重新勾）；
  - 部门助手的专属技能（skills/enterprise/agent_<id>.yml）是某个助手私有的，不能上架，跳过并说明原因；
  - 已经是目标状态的不重复写；整批一个事务，写一条审计（谁、上架了哪些、跳过了哪些）。
已发布的技能被编辑运行配置时仍会自动退回草稿（crud.update_skill_with_config），不受这里影响。
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from models.init_db import Skill
from models.skill_dao import update_skill as dao_update

ACTIONS = ("publish", "unpublish")
MAX_BATCH = 500
PRIVATE_PREFIX = "enterprise/agent_"


def is_agent_private(skill: Skill) -> bool:
    return (skill.config_file or "").replace("\\", "/").startswith(PRIVATE_PREFIX)


def batch_set_shelf(db: Session, skill_ids: List[int], operator_id: int, action: str) -> Dict:
    from service import audit_service
    from service.exceptions import InvalidInput

    if action not in ACTIONS:
        raise InvalidInput("操作只能是上架或下架")
    ids = list(dict.fromkeys(int(i) for i in skill_ids))   # 去重、保持顺序
    if not ids:
        raise InvalidInput("请至少选择一个技能")
    if len(ids) > MAX_BATCH:
        raise InvalidInput(f"一次最多处理 {MAX_BATCH} 个技能")

    found = {s.id: s for s in db.query(Skill).filter(Skill.id.in_(ids)).all()}
    changed, unchanged, skipped = [], [], []
    for skill_id in ids:
        skill = found.get(skill_id)
        if skill is None:
            skipped.append({"id": skill_id, "name": "", "reason": "技能不存在"})
            continue
        if action == "publish" and is_agent_private(skill):
            skipped.append({"id": skill.id, "name": skill.name, "reason": "部门助手的专属技能，只给那个助手用，不能上架"})
            continue
        target = {"is_public": 1, "lifecycle_status": "published"} if action == "publish" else {"lifecycle_status": "draft"}
        if all(getattr(skill, k) == v for k, v in target.items()):
            unchanged.append(skill.id)
            continue
        dao_update(db, skill.id, **target)
        changed.append({"id": skill.id, "name": skill.name})
    db.commit()
    if changed:
        audit_service.record(operator_id, "skill.batch_published" if action == "publish" else "skill.batch_unpublished",
                             resource_type="skill",
                             detail={"skill_ids": [c["id"] for c in changed], "skipped": skipped})
    return {"action": action, "changed": changed, "unchanged": unchanged, "skipped": skipped}
