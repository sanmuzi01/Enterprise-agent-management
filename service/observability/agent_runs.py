"""Agent 运行失败的归类与追踪：把一次失败的运行变成可追溯的问题。

- 用户自己的配置问题（没配模型密钥）、业务拒绝（额度、权限）不是系统故障：只记录错误码，不进问题中心；
- 依赖不可用（Java 服务、数据库）和代码缺陷：每次都进问题中心（同一种故障聚合成一条）；
- 模型超时/暂时不可用是常态波动：同一个 Agent 10 分钟内累计 3 次才进问题中心，避免偶发超时刷屏；
- 每次失败写 agent.run-event.v1 事件（与运行记录同一个事务），运行记录上带 trace_id、错误码、问题编号，
  用户在运行轨迹里看到的问题编号可以直接在问题中心里找到。
"""
import os
from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy import func, select

from models.init_db import Agent, AgentRun
from service.exceptions import AppError
from service.observability.dependency_errors import classify
from service.observability.issues import record_occurrence
from utils.timeutil import utcnow

TRANSIENT_CODES = {"MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "upstream_error"}
THRESHOLD = int(os.getenv("AGENT_TRANSIENT_ISSUE_THRESHOLD", "3"))
WINDOW_MINUTES = int(os.getenv("AGENT_TRANSIENT_ISSUE_WINDOW_MINUTES", "10"))


def classify_failure(exc: Optional[BaseException]) -> Tuple[str, bool]:
    """返回 (错误码, 是否系统故障)。"""
    if exc is None:
        return "INTERNAL_ERROR", True
    if isinstance(exc, AppError):
        return exc.code, exc.http_status >= 500
    text = str(exc)
    if isinstance(exc, ValueError) and ("API Key" in text or "模型配置" in text or "模型连接" in text):
        return "MODEL_NOT_CONFIGURED", False
    code, _status = classify(exc)
    return code, True


async def report_failure(db, run_id: int, exc: Optional[BaseException], message: str) -> None:
    """失败收尾时调用：补全运行记录上的追踪字段，必要时登记问题并写事件。调用方负责 commit。"""
    from service.events import outbox
    from service.observability import context as trace_context
    run = (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalars().first()
    if run is None:
        return
    code, system_fault = classify_failure(exc)
    run.error_code = code
    run.trace_id = run.trace_id or trace_context.current_trace_id()
    team_id = (await db.execute(select(Agent.team_id).where(Agent.id == run.agent_id))).scalar()
    report = system_fault
    if system_fault and code in TRANSIENT_CODES:
        since = utcnow() - timedelta(minutes=WINDOW_MINUTES)
        await db.flush()
        recent = (await db.execute(select(func.count()).where(AgentRun.agent_id == run.agent_id, AgentRun.error_code == code,
                                                              AgentRun.status == "failed", AgentRun.started_at >= since))).scalar() or 0
        report = recent >= THRESHOLD     # flush 之后，本次这条已经算在里面
    if report:
        import asyncio
        result = await asyncio.to_thread(lambda: record_occurrence(
            error_code=code, http_status=500, operation=f"Agent 运行 #{run.agent_id}", exc=exc, message=message,
            trace_id=run.trace_id, department_id=team_id, resource_type="agent", resource_id=run.agent_id,
            extra={"agent_id": run.agent_id, "run_id": run.id}))
        if result:
            run.issue_no = result["issue_no"]
    outbox.emit(db, topic="agent.run-event.v1", event_type="agent.run.failed", aggregate_type="agent_run", aggregate_id=run.id,
                key=str(run.agent_id), department_id=team_id, trace_id=run.trace_id,
                payload={"run_id": run.id, "agent_id": run.agent_id, "status": "failed", "error_code": code, "system_fault": system_fault,
                         "issue_no": run.issue_no, "steps": run.total_steps, "tokens": run.total_tokens})


def agent_health(db, days: int = 7, limit: int = 50):
    """各 Agent 近 N 天的运行可靠性：运行数、失败率、错误码分布、平均步数与 Token、是否有未关闭的问题。只统计，不看内容。"""
    from sqlalchemy import case
    since = utcnow() - timedelta(days=max(1, min(days, 90)))
    rows = db.execute(select(
        AgentRun.agent_id, func.count(), func.sum(case((AgentRun.status == "failed", 1), else_=0)),
        func.sum(case((AgentRun.status == "cancelled", 1), else_=0)), func.avg(AgentRun.total_steps), func.sum(AgentRun.total_tokens),
    ).where(AgentRun.started_at >= since).group_by(AgentRun.agent_id).order_by(func.count().desc()).limit(limit)).all()
    codes = {}
    for agent_id, code, n in db.execute(select(AgentRun.agent_id, AgentRun.error_code, func.count()).where(
            AgentRun.started_at >= since, AgentRun.status == "failed").group_by(AgentRun.agent_id, AgentRun.error_code)).all():
        codes.setdefault(agent_id, {})[code or "UNKNOWN"] = int(n)
    names = dict(db.execute(select(Agent.id, Agent.name).where(Agent.id.in_([r[0] for r in rows]))).all()) if rows else {}
    from models.init_db import SystemIssue
    open_issues = {int(r[0]): int(r[1]) for r in db.execute(select(SystemIssue.affected_resource_id, func.count()).where(
        SystemIssue.affected_resource_type == "agent", SystemIssue.status != "RESOLVED").group_by(SystemIssue.affected_resource_id)).all()
        if str(r[0]).isdigit()}
    result = []
    for agent_id, total, failed, cancelled, steps, tokens in rows:
        failed, total = int(failed or 0), int(total)
        result.append({"agent_id": agent_id, "agent_name": names.get(agent_id), "runs": total, "failed": failed, "cancelled": int(cancelled or 0),
                       "open_issues": open_issues.get(agent_id, 0),
                       "failure_rate": round(failed * 100.0 / total, 1), "avg_steps": round(float(steps or 0), 1), "tokens": int(tokens or 0),
                       "error_codes": codes.get(agent_id, {})})
    result.sort(key=lambda r: (-r["failure_rate"], -r["runs"]))
    return {"days": days, "agents": result}
