"""通用审计事件的写入入口（Phase 3D 阶段6，docs/enterprise-rbac-plan.md 9.5）。

跟 `service/knowledge_space/space_async_service.py::_audit` 是同一种"尽力而为"
写法：审计失败不能拖垮主流程，broad except + 独立 rollback。目前调用方是
`service/approval_service.py`、`service/organization_admin_service.py`、
`service/agent_admin_service.py`；以后有别的写操作要留痕时也调这里，不新建一张表。

不接受调用方传入的 `db` 会话——写审计必须走 `models/audit_db.py` 那个独立的最小权限
账号连接，不能复用调用方会话（那个会话的账号权限跟主业务一致，等于审计账号也能
改/删自己写过的记录），见 docs/enterprise-rbac-plan.md 第21节。
"""
from typing import Any

from utils.logger_handler import get_logger

logger = get_logger("audit_service")


def record(user_id: int, action: str, *, resource_type: str = None,
           resource_id: int = None, detail: Any = None) -> None:
    from models import audit_dao
    from models.audit_db import AuditSessionLocal

    db = AuditSessionLocal()
    try:
        audit_dao.record(db, user_id, action, resource_type=resource_type,
                          resource_id=resource_id, detail=detail, commit=True)
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        logger.warning(f"审计写入失败：user={user_id} action={action}", exc_info=True)
        db.rollback()
    finally:
        db.close()


async def record_async(user_id: int, action: str, *, resource_type: str = None,
                        resource_id: int = None, detail: Any = None) -> None:
    from models import audit_dao
    from models.audit_db import AuditAsyncSessionLocal

    async with AuditAsyncSessionLocal() as db:
        try:
            await audit_dao.record_async(db, user_id, action, resource_type=resource_type,
                                          resource_id=resource_id, detail=detail)
        except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
            logger.warning(f"审计写入失败：user={user_id} action={action}", exc_info=True)
            await db.rollback()
