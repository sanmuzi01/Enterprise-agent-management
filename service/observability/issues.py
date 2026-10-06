"""问题中心：把技术故障变成可分配、可跟踪、可验证、可关闭的工作事项。

- 同一种故障只有一条（fingerprint = 服务 + 错误码 + 异常类型 + 规范化的调用栈 + 操作 + 依赖），发生多少次累计多少次；
  trace_id、用户、请求内容不参与 fingerprint，否则会把同一问题拆成大量记录；
- 状态：OPEN → ACKNOWLEDGED → INVESTIGATING → MITIGATED → RESOLVED，已解决的问题再次出现 → REGRESSED；
- 关闭有门槛：必须有负责人、根因、处理说明、修复版本；验收人必须是另一个管理员（制单与复核分离）；
- 记录是同步实现（用独立会话，失败只记日志，绝不影响业务请求），异步代码里用 asyncio.to_thread 调用。
"""
import hashlib
import json
import os
import re
import traceback
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import IssueEvent, IssueOccurrence, SessionLocal, SystemIssue
from service.exceptions import Conflict, InvalidInput, NotFound
from service.observability.error_codes import CATALOG, spec_for
from service.observability.redact import redact, redact_text
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("issues")

STATUSES = ["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATED", "RESOLVED", "REGRESSED"]
STATUS_LABELS = {"OPEN": "新发现", "ACKNOWLEDGED": "已确认", "INVESTIGATING": "排查中", "MITIGATED": "已缓解",
                 "RESOLVED": "已解决", "REGRESSED": "问题复发"}
SEVERITY_LABELS = {"low": "低", "medium": "中", "high": "高", "critical": "严重"}
CATEGORY_LABELS = {"code": "代码异常", "dependency": "依赖故障", "security": "安全", "data": "数据一致性", "task": "后台任务"}
# 允许的人工流转（RESOLVED → REGRESSED 由系统在再次发生时自动完成）
_FLOW = {"OPEN": {"ACKNOWLEDGED", "INVESTIGATING", "MITIGATED"}, "ACKNOWLEDGED": {"INVESTIGATING", "MITIGATED", "RESOLVED"},
         "INVESTIGATING": {"MITIGATED", "RESOLVED"}, "MITIGATED": {"INVESTIGATING", "RESOLVED"},
         "RESOLVED": set(), "REGRESSED": {"ACKNOWLEDGED", "INVESTIGATING", "MITIGATED", "RESOLVED"}}
MAX_OCCURRENCES_KEPT = 50
SERVICE_NAME = os.getenv("SERVICE_NAME", "agent-service")
_PROJECT_FRAME = re.compile(r"[\\/](FasdtApi|service|models|utils)[\\/]")
_FRAME_FILE = re.compile(r"File \"(.+?)\", line \d+, in (\S+)")


def release() -> str:
    return os.getenv("RELEASE") or os.getenv("GIT_COMMIT", "")[:12] or "dev"


def normalized_stack(exc: Optional[BaseException], limit: int = 3) -> str:
    """只取项目内最靠近出错点的几帧的“文件名:函数名”（不含行号，改代码换行号不会让同一问题变成新问题）。"""
    if exc is None:
        return ""
    frames = []
    for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
        for path, func_name in _FRAME_FILE.findall(line):
            if _PROJECT_FRAME.search(path):
                frames.append(f"{os.path.basename(path)}:{func_name}")
    return ">".join(frames[-limit:])


def make_fingerprint(*, service: str, error_code: str, operation: str = "", exc: Optional[BaseException] = None,
                     dependency: str = "") -> str:
    parts = [service, error_code, type(exc).__name__ if exc is not None else "", normalized_stack(exc), operation, dependency]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _title(error_code: str, operation: str, exc: Optional[BaseException], message: str) -> str:
    spec = spec_for(error_code)
    base = spec.message if error_code in CATALOG else message
    where = f"（{operation}）" if operation else ""
    return redact_text(f"{base}{where}")[:255]


def record_occurrence(*, error_code: str, http_status: int = 500, operation: str = "", exc: Optional[BaseException] = None,
                      message: str = "", trace_id: Optional[str] = None, department_id: Optional[int] = None,
                      dependency: str = "", service: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
                      resource_type: Optional[str] = None, resource_id: Optional[Any] = None,
                      sentry_event_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """登记一次故障发生，返回 {id, issue_no, status, occurrence_count, is_new}；任何失败都只记日志并返回 None。"""
    spec = spec_for(error_code, http_status)
    service = service or SERVICE_NAME
    fingerprint = make_fingerprint(service=service, error_code=error_code, operation=operation, exc=exc, dependency=dependency)
    detail = redact({"exception": type(exc).__name__ if exc is not None else None, "stack": normalized_stack(exc, 6),
                     **(extra or {})})
    now = utcnow()
    db = None
    try:
        db = SessionLocal()
        for attempt in range(2):
            issue = db.execute(select(SystemIssue).where(SystemIssue.fingerprint == fingerprint)).scalar_one_or_none()
            is_new = issue is None
            try:
                if is_new:
                    issue = SystemIssue(
                        fingerprint=fingerprint, title=_title(error_code, operation, exc, message), severity=spec.severity,
                        category=spec.category, status="OPEN", service=service, operation=(operation or None) and operation[:200],
                        error_code=error_code, department_id=department_id, last_trace_id=trace_id, retryable=int(spec.retryable),
                        occurrence_count=1, first_seen_at=now, last_seen_at=now, sentry_event_id=sentry_event_id,
                        affected_resource_type=resource_type, affected_resource_id=None if resource_id is None else str(resource_id)[:64])
                    db.add(issue)
                    db.flush()
                    issue.issue_no = f"ISSUE-{now:%Y%m%d}-{issue.id:04d}"
                else:
                    issue.occurrence_count = SystemIssue.occurrence_count + 1   # 原子累加，并发发生也不丢次数
                    issue.last_seen_at = now
                    issue.last_trace_id = trace_id or issue.last_trace_id
                    if sentry_event_id:
                        issue.sentry_event_id = sentry_event_id
                    if issue.status == "RESOLVED":   # 已解决的问题再次出现：复发，需要重新处理和验收
                        issue.status = "REGRESSED"
                        issue.regress_count = (issue.regress_count or 0) + 1
                        issue.verified_by = issue.verified_at = None
                        db.add(IssueEvent(issue_id=issue.id, actor_user_id=None, action="regressed",
                                          note=f"问题在修复版本 {issue.fix_version or '—'} 之后再次出现", created_at=now))
                db.add(IssueOccurrence(issue_id=issue.id, trace_id=trace_id, release=release(), http_status=http_status,
                                       message=redact_text(message)[:500], detail_json=json.dumps(detail, ensure_ascii=False)[:4000], occurred_at=now))
                db.commit()
                break
            except IntegrityError:   # 并发首次出现：另一个请求先建好了，重来一次走“累加”分支
                db.rollback()
                if attempt == 1:
                    raise
        db.refresh(issue)
        if not is_new and issue.occurrence_count % 20 == 0:
            _prune(db, issue.id)
        return {"id": issue.id, "issue_no": issue.issue_no, "status": issue.status, "occurrence_count": issue.occurrence_count,
                "is_new": is_new}
    except Exception:  # noqa: BLE001 —— 问题记录失败绝不能影响业务请求
        logger.warning("问题记录失败", exc_info=True)
        if db is not None:
            db.rollback()
        return None
    finally:
        if db is not None:
            db.close()


def _prune(db, issue_id: int) -> None:
    keep_from = db.execute(select(IssueOccurrence.id).where(IssueOccurrence.issue_id == issue_id)
                           .order_by(IssueOccurrence.id.desc()).offset(MAX_OCCURRENCES_KEPT).limit(1)).scalar()
    if keep_from:
        db.execute(IssueOccurrence.__table__.delete().where(IssueOccurrence.issue_id == issue_id, IssueOccurrence.id <= keep_from))
        db.commit()


def add_system_event(issue_id: int, action: str, note: str) -> None:
    db = SessionLocal()
    try:
        db.add(IssueEvent(issue_id=issue_id, actor_user_id=None, action=action, note=note[:1000], created_at=utcnow()))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("问题事件记录失败", exc_info=True)
    finally:
        db.close()


# ---------------------------------------------------------------- 查询与处理（管理端）

def _row(i: SystemIssue) -> Dict[str, Any]:
    return {"id": i.id, "issue_no": i.issue_no, "title": i.title, "severity": i.severity, "severity_label": SEVERITY_LABELS.get(i.severity, i.severity),
            "category": i.category, "category_label": CATEGORY_LABELS.get(i.category, i.category), "status": i.status,
            "status_label": STATUS_LABELS.get(i.status, i.status), "service": i.service, "operation": i.operation, "error_code": i.error_code,
            "department_id": i.department_id, "responsible_user_id": i.responsible_user_id, "last_trace_id": i.last_trace_id,
            "sentry_event_id": i.sentry_event_id, "occurrence_count": i.occurrence_count, "retryable": bool(i.retryable),
            "first_seen_at": i.first_seen_at.isoformat() + "Z", "last_seen_at": i.last_seen_at.isoformat() + "Z",
            "acknowledged_at": i.acknowledged_at and i.acknowledged_at.isoformat() + "Z",
            "resolved_at": i.resolved_at and i.resolved_at.isoformat() + "Z", "root_cause": i.root_cause, "resolution": i.resolution,
            "fix_version": i.fix_version, "resolved_by": i.resolved_by, "verified_by": i.verified_by,
            "verified_at": i.verified_at and i.verified_at.isoformat() + "Z", "regress_count": i.regress_count}


def list_issues(db, status: Optional[str] = None, severity: Optional[str] = None, department_id: Optional[int] = None,
                keyword: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    query = select(SystemIssue)
    if status == "active":
        query = query.where(SystemIssue.status != "RESOLVED")
    elif status:
        query = query.where(SystemIssue.status == status)
    if severity:
        query = query.where(SystemIssue.severity == severity)
    if department_id is not None:
        query = query.where(SystemIssue.department_id == department_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(SystemIssue.title.like(like) | SystemIssue.issue_no.like(like) | SystemIssue.error_code.like(like))
    rows = db.execute(query.order_by(SystemIssue.last_seen_at.desc()).limit(max(1, min(limit, 300)))).scalars().all()
    return [_row(r) for r in rows]


def get_issue(db, issue_id: int) -> Dict[str, Any]:
    issue = db.get(SystemIssue, issue_id)
    if issue is None:
        raise NotFound("问题不存在")
    occurrences = db.execute(select(IssueOccurrence).where(IssueOccurrence.issue_id == issue_id)
                             .order_by(IssueOccurrence.id.desc()).limit(MAX_OCCURRENCES_KEPT)).scalars().all()
    events = db.execute(select(IssueEvent).where(IssueEvent.issue_id == issue_id).order_by(IssueEvent.id)).scalars().all()
    from models.init_db import User
    ids = {e.actor_user_id for e in events if e.actor_user_id} | {issue.responsible_user_id, issue.resolved_by, issue.verified_by} - {None}
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()) if ids else {}
    data = _row(issue)
    data.update(responsible_name=names.get(issue.responsible_user_id), resolved_by_name=names.get(issue.resolved_by),
                verified_by_name=names.get(issue.verified_by))
    data["occurrences"] = [{"id": o.id, "trace_id": o.trace_id, "release": o.release, "http_status": o.http_status, "message": o.message,
                            "detail": json.loads(o.detail_json or "{}"), "occurred_at": o.occurred_at.isoformat() + "Z"} for o in occurrences]
    data["events"] = [{"id": e.id, "actor_user_id": e.actor_user_id, "actor_name": names.get(e.actor_user_id) if e.actor_user_id else "系统",
                       "action": e.action, "note": e.note, "created_at": e.created_at.isoformat() + "Z"} for e in events]
    data["links"] = links_for(issue)
    return data


def links_for(issue: SystemIssue) -> Dict[str, Optional[str]]:
    """跳转到外部观测平台的链接（配置了才有）。"""
    trace = issue.last_trace_id
    grafana, sentry, logs = (os.getenv("GRAFANA_URL", "").rstrip("/"), os.getenv("SENTRY_ISSUE_URL", "").rstrip("/"),
                             os.getenv("LOG_SEARCH_URL", "").rstrip("/"))
    return {"trace": f"{grafana}/explore?trace_id={trace}" if grafana and trace else None,
            "sentry": f"{sentry}/?query={issue.sentry_event_id}" if sentry and issue.sentry_event_id else None,
            "logs": f"{logs}?q={trace}" if logs and trace else None}


def summary(db) -> Dict[str, Any]:
    by_status = {s: 0 for s in STATUSES}
    for status, n in db.execute(select(SystemIssue.status, func.count()).group_by(SystemIssue.status)).all():
        by_status[status] = int(n)
    by_sev = {k: int(n) for k, n in db.execute(select(SystemIssue.severity, func.count()).where(SystemIssue.status != "RESOLVED")
                                              .group_by(SystemIssue.severity)).all()}
    acked = db.execute(select(SystemIssue.first_seen_at, SystemIssue.acknowledged_at).where(SystemIssue.acknowledged_at.is_not(None))).all()
    solved = db.execute(select(SystemIssue.first_seen_at, SystemIssue.resolved_at).where(SystemIssue.resolved_at.is_not(None))).all()

    def avg_minutes(pairs):
        return round(sum((b - a).total_seconds() for a, b in pairs) / len(pairs) / 60, 1) if pairs else None
    return {"by_status": by_status, "open_by_severity": by_sev, "active": sum(v for k, v in by_status.items() if k != "RESOLVED"),
            "mtta_minutes": avg_minutes(acked), "mttr_minutes": avg_minutes(solved),
            "regressed": by_status["REGRESSED"]}


def transition(db, issue_id: int, actor_id: int, action: str, *, note: str = "", responsible_user_id: Optional[int] = None,
               root_cause: str = "", resolution: str = "", fix_version: str = "", severity: str = "") -> Dict[str, Any]:
    """action：acknowledge / investigate / mitigate / resolve / verify / assign / note / severity。"""
    issue = db.execute(select(SystemIssue).where(SystemIssue.id == issue_id).with_for_update()).scalar_one_or_none()
    if issue is None:
        raise NotFound("问题不存在")
    now = utcnow()
    note = (note or "").strip()
    target = {"acknowledge": "ACKNOWLEDGED", "investigate": "INVESTIGATING", "mitigate": "MITIGATED", "resolve": "RESOLVED"}.get(action)
    if target:
        if target not in _FLOW.get(issue.status, set()):
            raise Conflict(f"当前状态「{STATUS_LABELS[issue.status]}」不能这样处理")
        if action == "acknowledge":
            issue.responsible_user_id = responsible_user_id or issue.responsible_user_id or actor_id
            issue.acknowledged_at = issue.acknowledged_at or now
        elif issue.responsible_user_id is None:
            issue.responsible_user_id = actor_id
            issue.acknowledged_at = issue.acknowledged_at or now
        if action == "resolve":
            missing = [label for label, value in (("根因", root_cause), ("处理说明", resolution), ("修复版本", fix_version)) if not (value or "").strip()]
            if missing:
                raise InvalidInput("关闭问题必须填写：" + "、".join(missing))
            issue.root_cause, issue.resolution, issue.fix_version = root_cause.strip()[:4000], resolution.strip()[:4000], fix_version.strip()[:80]
            issue.resolved_at, issue.resolved_by = now, actor_id
            issue.verified_by = issue.verified_at = None
        issue.status = target
    elif action == "verify":
        if issue.status != "RESOLVED":
            raise Conflict("只有已解决的问题才能验收")
        if issue.verified_by:
            raise Conflict("这个问题已经验收过了")
        if actor_id == issue.resolved_by:
            raise InvalidInput("验收人不能是处理人本人，请由另一位管理员验收")
        issue.verified_by, issue.verified_at = actor_id, now
    elif action == "assign":
        if not responsible_user_id:
            raise InvalidInput("请选择负责人")
        issue.responsible_user_id = responsible_user_id
    elif action == "severity":
        if severity not in SEVERITY_LABELS:
            raise InvalidInput("不支持的严重程度")
        issue.severity = severity
        note = note or f"严重程度改为{SEVERITY_LABELS[severity]}"
    elif action == "note":
        if not note:
            raise InvalidInput("请填写备注内容")
    else:
        raise InvalidInput(f"未知的操作: {action}")
    db.add(IssueEvent(issue_id=issue.id, actor_user_id=actor_id, action=action, note=note[:1000] or None, created_at=now))
    db.commit()
    return get_issue(db, issue_id)
