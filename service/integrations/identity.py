"""外部平台的人 / 部门 ↔ 平台账号 / 部门。

能在飞书、钉钉里用助手的前提：有一条 active 的绑定，指向一个没被停用、仍是本企业有效成员的平台账号。
用户的部门、权限一律按“此刻”的平台数据算（和网页一样），外部平台里的部门只用来同步时对照，不直接授权——
所以在平台里调岗、停用，飞书 / 钉钉里的权限立刻跟着变。

同步组织架构（sync_organization_sync）：
  - 人：按手机号精确对上平台账号（对不上的记成 unmatched，由管理员手动绑定，不按姓名猜）；
    外部已离职 / 已不在通讯录里的，绑定自动停用（status=disabled）并写审计；
  - 部门：按名称精确对上平台部门；外部部门和平台部门的成员不一致只列出来，不自动调岗（调岗是人的决定，见“一人一部门”）。
"""
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, text

from models.init_db import ExternalDepartmentBinding, ExternalUserBinding
from service.exceptions import InvalidInput
from utils.timeutil import utcnow

REASONS = {
    "unbound": "你的{platform}账号还没有绑定平台账号，请联系管理员在后台“外部协作平台”里绑定后再使用。",
    "disabled": "你的{platform}账号绑定已停用（可能已离职或被管理员停用），如有疑问请联系管理员。",
    "not_member": "你的平台账号已停用或已不在本企业，不能使用助手。",
}


def resolve_sync(db, provider: str, tenant_id: str, external_user_id: str) -> Tuple[Optional[int], Optional[str]]:
    """返回 (本地 user_id, None) 或 (None, 原因代码)。"""
    binding = db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant_id,
        ExternalUserBinding.external_user_id == external_user_id)).scalar_one_or_none()
    if binding is None or binding.local_user_id is None:
        return None, "unbound"
    if binding.status != "active":
        return None, "disabled"
    member = db.execute(text(
        "SELECT 1 FROM `user` u JOIN organization_members om ON om.user_id = u.id AND om.status = 'active' "
        "WHERE u.id = :u AND COALESCE(u.is_disabled, 0) = 0 AND om.organization_id = :o"),
        {"u": binding.local_user_id, "o": binding.organization_id}).scalar()
    if not member:
        return None, "not_member"
    return int(binding.local_user_id), None


def bind_sync(db, operator_id: int, organization_id: int, provider: str, tenant_id: str, external_user_id: str,
              local_user_id: Optional[int], external_name: Optional[str] = None) -> Dict[str, Any]:
    """管理员手动绑定 / 改绑 / 解绑（local_user_id=None）。改了对应关系就写审计（从谁改成谁）。"""
    from service import audit_service
    if local_user_id is not None:
        ok = db.execute(text("SELECT 1 FROM organization_members WHERE organization_id = :o AND user_id = :u AND status = 'active'"),
                        {"o": organization_id, "u": local_user_id}).scalar()
        if not ok:
            raise InvalidInput("只能绑定到本企业的有效成员")
        taken = db.execute(select(ExternalUserBinding).where(
            ExternalUserBinding.provider == provider, ExternalUserBinding.local_user_id == local_user_id,
            ExternalUserBinding.status == "active", ExternalUserBinding.external_user_id != external_user_id)).first()
        if taken:
            raise InvalidInput("这个平台账号已经绑定了另一个外部账号，请先解绑")
    row = db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant_id,
        ExternalUserBinding.external_user_id == external_user_id)).scalar_one_or_none()
    before = row.local_user_id if row else None
    if row is None:
        row = ExternalUserBinding(organization_id=organization_id, provider=provider, external_tenant_id=tenant_id,
                                  external_user_id=external_user_id)
        db.add(row)
    row.local_user_id = local_user_id
    row.status = "active" if local_user_id is not None else "unmatched"
    if external_name:
        row.external_name = external_name[:120]
    db.commit()
    if before != local_user_id:
        audit_service.record(operator_id, "integration.user_rebound", resource_type="external_user_binding", resource_id=row.id,
                             detail={"provider": provider, "external_user_id": external_user_id, "from": before, "to": local_user_id})
    return {"id": row.id, "external_user_id": external_user_id, "local_user_id": local_user_id, "status": row.status}


def list_bindings_sync(db, provider: str, organization_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(select(ExternalUserBinding).where(ExternalUserBinding.provider == provider,
                                                        ExternalUserBinding.organization_id == organization_id)
                      .order_by(ExternalUserBinding.status, ExternalUserBinding.id)).scalars().all()
    names = {}
    ids = [r.local_user_id for r in rows if r.local_user_id]
    if ids:
        from models.init_db import User
        names = {int(i): n for i, n in db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()}
    return [{"id": r.id, "external_user_id": r.external_user_id, "external_name": r.external_name, "status": r.status,
             "local_user_id": r.local_user_id, "local_user_name": names.get(r.local_user_id),
             "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None} for r in rows]


def _digits(phone: Optional[str]) -> str:
    value = "".join(c for c in (phone or "") if c.isdigit())
    return value[-11:] if len(value) >= 11 else value


def sync_organization_sync(db, operator_id: int, organization_id: int, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """把拉到的外部组织架构写进映射表。不改平台里的部门和成员，只建立 / 更新对应关系。"""
    from service import audit_service
    tenant = str(data.get("tenant_id") or "")
    if not tenant:
        raise InvalidInput("外部平台没有返回企业标识")
    now = utcnow()

    teams = {name: tid for tid, name in db.execute(text(
        "SELECT id, name FROM teams WHERE organization_id = :o AND status = 'active'"), {"o": organization_id}).all()}
    existing_depts = {r.external_department_id: r for r in db.execute(select(ExternalDepartmentBinding).where(
        ExternalDepartmentBinding.provider == provider, ExternalDepartmentBinding.external_tenant_id == tenant)).scalars().all()}
    dept_unmatched = []
    for dept in data.get("departments", []):
        did = str(dept["id"])
        row = existing_depts.get(did)
        if row is None:
            row = ExternalDepartmentBinding(organization_id=organization_id, provider=provider, external_tenant_id=tenant,
                                            external_department_id=did)
            db.add(row)
        row.external_name = (dept.get("name") or "")[:200]
        row.external_parent_id = str(dept.get("parent_id") or "") or None
        row.last_synced_at = now
        if row.local_team_id is None:
            row.local_team_id = teams.get((dept.get("name") or "").strip())
        if row.local_team_id is None:
            dept_unmatched.append(row.external_name)

    phones = {_digits(p): uid for uid, p in db.execute(text(
        "SELECT u.id, u.phone FROM `user` u JOIN organization_members om ON om.user_id = u.id "
        "WHERE om.organization_id = :o AND om.status = 'active' AND u.phone IS NOT NULL"), {"o": organization_id}).all() if _digits(p)}
    existing_users = {r.external_user_id: r for r in db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant)).scalars().all()}
    seen, matched, unmatched, disabled = set(), 0, [], []
    for person in data.get("users", []):
        uid = str(person["user_id"])
        seen.add(uid)
        row = existing_users.get(uid)
        if row is None:
            row = ExternalUserBinding(organization_id=organization_id, provider=provider, external_tenant_id=tenant,
                                      external_user_id=uid, status="unmatched")
            db.add(row)
        row.external_union_id = person.get("union_id") or row.external_union_id
        row.external_name = (person.get("name") or "")[:120] or row.external_name
        row.last_synced_at = now
        if not person.get("active", True):
            if row.status != "disabled":
                disabled.append(row.external_name or uid)
            row.status = "disabled"
            continue
        if row.local_user_id is None:
            row.local_user_id = phones.get(_digits(person.get("mobile")))
        if row.local_user_id is not None:
            row.status = "active"
            matched += 1
        else:
            row.status = "unmatched"
            unmatched.append(row.external_name or uid)
    for uid, row in existing_users.items():          # 通讯录里已经没有的人：停用
        if uid not in seen and row.status != "disabled":
            row.status = "disabled"
            disabled.append(row.external_name or uid)
    db.commit()
    summary = {"tenant_id": tenant, "departments": len(data.get("departments", [])), "departments_unmatched": dept_unmatched[:50],
               "users": len(data.get("users", [])), "users_matched": matched, "users_unmatched": unmatched[:100],
               "users_unmatched_total": len(unmatched), "users_disabled": disabled[:100], "users_disabled_total": len(disabled)}
    audit_service.record(operator_id, "integration.organization_synced", resource_type="collaboration_app", resource_id=0,
                         detail={k: v for k, v in summary.items() if not isinstance(v, list)} | {"provider": provider})
    return summary
