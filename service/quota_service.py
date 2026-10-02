"""计费/配额服务：套餐用量查询 + 每月 Token 配额检查。

范围有意收窄：MVP 只做「每自然月 Token 用量」这一种配额资源，不做真实计费/支付，
也不做按 Agent 数 / 知识库空间数限额（留作后续按需扩展）——先把最容易失控的开销
（token 消耗）管住，做成一个可以直接上线用的最小闭环。
"""

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import func, select

from models.init_db import Agent, AgentRun, AutomationWork
from models.plan_async_dao import get_effective_plan_async
from service.exceptions import QuotaExceeded
from utils.timeutil import utcnow


def _month_start(now: Optional[datetime] = None) -> datetime:
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_monthly_token_usage_async(db, user_id: int, since: Optional[datetime] = None) -> int:
    since = since or _month_start()
    result = await db.execute(
        select(func.coalesce(func.sum(AgentRun.total_tokens), 0))
        .where(AgentRun.user_id == user_id, AgentRun.started_at >= since)
    )
    agent_tokens = int(result.scalar() or 0)
    automation_result = await db.execute(
        select(func.coalesce(func.sum(AutomationWork.total_tokens), 0))
        .where(AutomationWork.user_id == user_id, AutomationWork.created_at >= since)
    )
    return agent_tokens + int(automation_result.scalar() or 0)


async def get_quota_status_async(db, user_id: int) -> Dict:
    """当前用户的套餐 + 本月用量，供用户设置页展示、也供 enforce_quota_async 复用。"""
    plan = await get_effective_plan_async(db, user_id)
    month_start = _month_start()
    used = await get_monthly_token_usage_async(db, user_id, since=month_start)
    limit = int(plan.monthly_token_limit) if plan else 0
    unlimited = limit <= 0
    return {
        "plan_id": plan.id if plan else None,
        "plan_name": plan.name if plan else None,
        "plan_display_name": plan.display_name if plan else "未分配套餐（不限量）",
        "monthly_token_limit": limit,
        "used_tokens": used,
        "remaining_tokens": None if unlimited else max(0, limit - used),
        "unlimited": unlimited,
        "period_start": month_start.strftime("%Y-%m-%d"),
    }


async def list_monthly_runs_async(db, user_id: int, since: Optional[datetime] = None) -> list:
    """本月（或指定起点以来）的运行明细，供用量报表导出用。"""
    since = since or _month_start()
    result = await db.execute(
        select(AgentRun, Agent.name.label("agent_name"))
        .join(Agent, AgentRun.agent_id == Agent.id)
        .where(AgentRun.user_id == user_id, AgentRun.started_at >= since)
        .order_by(AgentRun.started_at.desc())
    )
    return [
        {
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S") if run.started_at else None,
            "agent_name": agent_name,
            "status": run.status,
            "total_steps": run.total_steps or 0,
            "total_tokens": run.total_tokens or 0,
        }
        for run, agent_name in result.all()
    ]


async def enforce_quota_async(db, user_id: int) -> Dict:
    """本月 Token 配额检查：超出则抛 QuotaExceeded（429）。成功时把这次检查时的
    用量快照原样返回——调用方（chat 路由）留着它，run 结束后传给
    check_and_notify_threshold_async 算"这次跑有没有跨过提醒阈值"，不用再多查一次库。

    调用方须在真正发起一次昂贵的模型调用之前调用这个函数——发起之后再检查，
    钱已经花出去了，拦不住任何东西。
    """
    status_ = await get_quota_status_async(db, user_id)
    if status_["unlimited"]:
        return status_
    if status_["used_tokens"] >= status_["monthly_token_limit"]:
        raise QuotaExceeded(
            f"本月 Token 用量已达套餐上限（{status_['plan_display_name']}："
            f"{status_['monthly_token_limit']}），请等待下月重置或联系管理员升级套餐。",
            context={
                "plan_name": status_["plan_name"],
                "monthly_token_limit": status_["monthly_token_limit"],
                "used_tokens": status_["used_tokens"],
            },
        )
    return status_


# 用量跨过这些比例时提醒一次——80%/95% 提前预警，100% 是"已经用完了"确认通知。
# 只在"上一次检查时还没到、这次跑完到了"的那一刻推送一次，不会每条消息都刷屏。
_ALERT_THRESHOLDS = (0.8, 0.95, 1.0)


async def check_and_notify_threshold_async(db, user_id: int, used_before: int) -> None:
    """一次运行结束后调用：如果本月用量因为这次运行跨过了提醒阈值，推一条通知。

    best-effort——通知失败不能影响聊天请求本身已经成功返回这件事。
    """
    try:
        status_ = await get_quota_status_async(db, user_id)
        if status_["unlimited"]:
            return
        limit = status_["monthly_token_limit"]
        used_after = status_["used_tokens"]
        for pct in _ALERT_THRESHOLDS:
            boundary = limit * pct
            if used_before < boundary <= used_after:
                from service.notification_service import dispatch_alert_async

                icon = "🔴" if pct >= 1.0 else "🟠"
                pct_label = "已用完" if pct >= 1.0 else f"已用 {int(pct * 100)}%"
                await dispatch_alert_async(
                    db, user_id,
                    title=f"{icon} Token 配额{pct_label}",
                    message=f"{status_['plan_display_name']} 本月 Token 配额{pct_label}"
                            f"（{used_after}/{limit}）。",
                )
    except Exception as exc:  # noqa: BLE001 - 配额提醒失败不能影响聊天请求本身
        from utils.logger_handler import get_logger
        get_logger("quota_service").warning(f"配额阈值提醒失败: user_id={user_id}, error={exc}")
