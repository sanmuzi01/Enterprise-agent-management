"""数据保留策略：哪些数据保留多久、到期怎么清理。

以前只有几类“传输用”的数据会自动清理（发件箱事件 7 天、附件 7 天、组件数据点 90 天），而每次对话都会写的
运行轨迹（agent_run / agent_step）、每个请求都会写的操作日志、通知、后台任务记录只增不减。
上线一年后这几张表会是库里最大的几张，备份变慢、后台列表变慢，也没人说得清这些数据要留多久。

原则：
  - 只清理“过程记录”，不碰业务数据和用户资产：对话内容、知识库、审批单、业务单据都不在这里；
  - 审计记录（audit_event、kb_audit_log）、问题中心（system_issue、issue_event）永远保留——事后追责和回归判断都靠它们；
  - 每一类单独配置天数（环境变量），0 表示这一类不清理；配得太小（< MIN_DAYS）按 MIN_DAYS 算，防止手误把近期数据删光；
  - 还在进行中的不删（运行中的轨迹、排队/执行中的任务、未读通知、待处理死信）；
  - 分批删除，每批单独提交，不会长时间锁表；先 --dry-run 看数量，确认后 --apply，清理结果写审计。

自动执行默认关闭（DATA_RETENTION_AUTO=0）：删数据要先和客户确认保留期限，确认后打开，事件运行器每天跑一次；
也可以不开自动、改用定时任务调 scripts/data_retention.py --apply。
"""
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from sqlalchemy import bindparam, text

from utils.timeutil import utcnow

logger = logging.getLogger(__name__)

MIN_DAYS = 7
BATCH_SIZE = 1000

# 永远不清理的表，写在这里是为了让“为什么不清理”有据可查（文档和测试都引用它）
NEVER_PURGED = {
    "audit_event": "审计记录：谁在什么时候做了什么，事后追责要用",
    "kb_audit_log": "知识库审计记录",
    "system_issue": "问题中心：同一故障只有一条，删了就判断不出“回归”",
    "issue_event": "问题处理记录（确认、指派、解决、验收）",
    "conversation / message": "对话内容是用户自己的资产，由用户删除对话或删除助手时一并删除",
    "approval_request": "审批单是业务凭证",
}


@dataclass(frozen=True)
class Policy:
    name: str
    label: str
    env: str
    default_days: int
    # 选出一批到期记录的 id（参数 :cutoff、:limit）
    select_sql: str
    count_sql: str
    # 给定一批 id，按顺序执行的删除语句（参数 :ids，先子表后主表）
    delete_sqls: tuple

    def days(self) -> int:
        try:
            value = int(os.getenv(self.env, str(self.default_days)))
        except ValueError:
            logger.warning("%s 不是整数，按默认 %s 天", self.env, self.default_days)
            value = self.default_days
        if value <= 0:
            return 0
        return max(value, MIN_DAYS)


POLICIES: List[Policy] = [
    Policy(
        "agent_runs", "智能体运行轨迹（每次对话的步骤、工具调用）", "AGENT_RUN_RETENTION_DAYS", 180,
        "SELECT id FROM agent_run WHERE started_at < :cutoff AND status <> 'running' ORDER BY id LIMIT :limit",
        "SELECT COUNT(*) FROM agent_run WHERE started_at < :cutoff AND status <> 'running'",
        ("DELETE FROM agent_step WHERE run_id IN :ids", "DELETE FROM agent_run WHERE id IN :ids"),
    ),
    Policy(
        "operation_logs", "接口操作日志", "OPERATION_LOG_RETENTION_DAYS", 90,
        "SELECT id FROM operation_log WHERE created_at < :cutoff ORDER BY id LIMIT :limit",
        "SELECT COUNT(*) FROM operation_log WHERE created_at < :cutoff",
        ("DELETE FROM operation_log WHERE id IN :ids",),
    ),
    Policy(
        "read_notifications", "已读的站内通知（未读的不删）", "NOTIFICATION_RETENTION_DAYS", 180,
        "SELECT id FROM notification WHERE read_at IS NOT NULL AND created_at < :cutoff ORDER BY id LIMIT :limit",
        "SELECT COUNT(*) FROM notification WHERE read_at IS NOT NULL AND created_at < :cutoff",
        ("DELETE FROM notification WHERE id IN :ids",),
    ),
    Policy(
        "finished_tasks", "已结束的后台任务记录（排队中、执行中的不删）", "BACKGROUND_TASK_RETENTION_DAYS", 90,
        "SELECT id FROM background_task WHERE status IN ('finished', 'failed', 'cancelled') "
        "AND COALESCE(finished_at, created_at) < :cutoff ORDER BY id LIMIT :limit",
        "SELECT COUNT(*) FROM background_task WHERE status IN ('finished', 'failed', 'cancelled') "
        "AND COALESCE(finished_at, created_at) < :cutoff",
        # 重试出来的新任务指向原任务（parent_task_id 外键）：原任务删了，新任务只是断开指向，不跟着删
        ("UPDATE background_task SET parent_task_id = NULL WHERE parent_task_id IN :ids",
         "DELETE FROM background_task WHERE id IN :ids"),
    ),
    Policy(
        "handled_dead_letters", "已处理的死信（重新投递或已丢弃的；待处理的不删）", "DEAD_LETTER_RETENTION_DAYS", 180,
        "SELECT id FROM dead_letter WHERE status <> 'pending' AND COALESCE(handled_at, created_at) < :cutoff ORDER BY id LIMIT :limit",
        "SELECT COUNT(*) FROM dead_letter WHERE status <> 'pending' AND COALESCE(handled_at, created_at) < :cutoff",
        ("DELETE FROM dead_letter WHERE id IN :ids",),
    ),
]


def plan(db, now=None) -> List[Dict]:
    """每一类：保留天数、截止时间、到期条数。只读。"""
    now = now or utcnow()
    rows = []
    for p in POLICIES:
        days = p.days()
        if days == 0:
            rows.append({"name": p.name, "label": p.label, "env": p.env, "days": 0, "cutoff": None, "due": 0})
            continue
        cutoff = now - timedelta(days=days)
        due = int(db.execute(text(p.count_sql), {"cutoff": cutoff}).scalar() or 0)
        rows.append({"name": p.name, "label": p.label, "env": p.env, "days": days, "cutoff": cutoff, "due": due})
    return rows


def _purge(db, p: Policy, cutoff, batch_size: int, max_batches: Optional[int]) -> int:
    total, batches = 0, 0
    while max_batches is None or batches < max_batches:
        ids = [r[0] for r in db.execute(text(p.select_sql), {"cutoff": cutoff, "limit": batch_size}).all()]
        if not ids:
            break
        for sql in p.delete_sqls:
            db.execute(text(sql).bindparams(bindparam("ids", expanding=True)), {"ids": ids})
        db.commit()   # 每批单独提交：不长时间锁表，中途被打断也只是少删了几批，下次接着删
        total += len(ids)
        batches += 1
        if len(ids) < batch_size:
            break
    return total


def apply(db, now=None, operator_id: int = 0, batch_size: int = BATCH_SIZE,
          max_batches: Optional[int] = None, only: Optional[List[str]] = None) -> Dict[str, int]:
    """按策略清理到期数据，返回每一类删了多少条；有删除就写一条审计。"""
    from service import audit_service
    now = now or utcnow()
    result = {}
    for p in POLICIES:
        if only and p.name not in only:
            continue
        days = p.days()
        if days == 0:
            continue
        try:
            deleted = _purge(db, p, now - timedelta(days=days), batch_size, max_batches)
        except Exception:  # noqa: BLE001 —— 一类失败不影响其他类；已提交的批次保持
            db.rollback()
            logger.exception("数据保留清理失败：%s", p.name)
            result[p.name] = -1
            continue
        if deleted:
            result[p.name] = deleted
    if result:
        audit_service.record(operator_id, "data_retention.applied", resource_type="database",
                             detail={"deleted": result, "days": {p.name: p.days() for p in POLICIES}})
    return result


def auto_enabled() -> bool:
    return os.getenv("DATA_RETENTION_AUTO", "0").strip().lower() in ("1", "true", "yes", "on")


def run_scheduled(session_factory: Callable = None) -> Dict[str, int]:
    """事件运行器每天调用一次（DATA_RETENTION_AUTO=1 时）。"""
    if not auto_enabled():
        return {}
    if session_factory is None:
        from models.init_db import SessionLocal as session_factory
    with session_factory() as db:
        result = apply(db)
    if result:
        logger.info("数据保留清理完成：%s", result)
    return result
