"""组件运行引擎 —— 唯一运行入口。

手动运行、未来 Worker 定时运行、事件触发，都调 run_widget(...)。
流程固定：取连接器 -> fetch -> 取处理器 -> process -> 存数据点 -> 更新调度状态。
runner 只认注册表，不认识任何具体数据源 / 处理器 / 视图。
"""

import inspect
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from utils.logger_handler import get_logger
from utils.timeutil import utcnow
from service.widgets import retention, schema
from service.widgets.connectors import CONNECTORS
from service.widgets.context import WidgetRunContext
from service.widgets.processors import PROCESSORS

logger = get_logger("widget_runner")

DEFAULT_KEEP_POINTS = retention.DEFAULT_KEEP_POINTS


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _retention_kwargs() -> Dict[str, int]:
    return {
        "keep_points": _env_int("WIDGET_KEEP_POINTS", retention.DEFAULT_KEEP_POINTS),
        "keep_days": _env_int("WIDGET_KEEP_DAYS", retention.DEFAULT_KEEP_DAYS),
        "keep_error_points": _env_int("WIDGET_KEEP_ERROR_POINTS", retention.DEFAULT_KEEP_ERROR_POINTS),
    }


def max_consecutive_fails() -> int:
    """连续失败达到该次数后，组件暂停自动调度（改成手动运行才恢复）。0 表示不暂停。"""
    return _env_int("WIDGET_MAX_CONSECUTIVE_FAILS", 6)


def compute_backoff_next_run(fail_count: int, now: datetime) -> datetime:
    """失败后的下次运行时间：指数退避，带上限。fail_count 从 1 起。"""
    base = _env_int("WIDGET_RETRY_BACKOFF_BASE_MINUTES", 5)
    cap = _env_int("WIDGET_RETRY_BACKOFF_CAP_MINUTES", 360)
    exp = max(0, int(fail_count) - 1)
    delay = min(base * (2 ** exp), cap) if base > 0 else cap
    return now + timedelta(minutes=delay)


@dataclass
class WidgetRunResult:
    ok: bool
    widget_id: int
    payload: Dict[str, Any]
    label: Optional[str] = None
    value: Optional[float] = None
    error: Optional[str] = None
    duration_ms: int = 0


def _loads(raw: Optional[str], fallback):
    try:
        value = json.loads(raw) if raw else None
        return value if value is not None else fallback
    except (TypeError, ValueError, RecursionError):
        return fallback


def spec_from_widget(widget) -> Dict[str, Any]:
    """把 UserWidget 行还原成一份完整 spec dict。"""
    return {
        "spec_version": getattr(widget, "spec_version", schema.SPEC_VERSION),
        "name": widget.name,
        "type": widget.type,
        "description": widget.description or "",
        "capabilities": _loads(widget.capabilities_json, []),
        "data_source": _loads(widget.data_source_json, {"kind": "sample", "config": {}}),
        "processor": _loads(widget.processor_json, {"kind": "passthrough", "config": {}}),
        "view": _loads(widget.view_json, {"kind": schema.TYPE_DEFAULT_VIEW.get(widget.type, "table"), "config": {}}),
        "trigger": _loads(widget.trigger_json, {"kind": "manual", "config": {}}),
        "actions": _loads(widget.actions_json, list(schema.DEFAULT_ACTIONS)),
    }


def compute_next_run_at(trigger: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """按触发规则算下一次运行时间（naive UTC，与库中 DateTime 列一致）。manual -> None。"""
    kind = (trigger or {}).get("kind", "manual")
    config = (trigger or {}).get("config") or {}
    if kind == "hourly":
        minute = config.get("minute", 0)
        nxt = now.replace(minute=minute if isinstance(minute, int) and 0 <= minute < 60 else 0,
                          second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
        return nxt
    if kind == "daily":
        run_at = str(config.get("run_at") or "09:00")
        try:
            hh, mm = (int(x) for x in run_at.split(":", 1))
        except (ValueError, TypeError):
            hh, mm = 9, 0
        # 触发规则里的时间是用户本地时区（默认东八区），这里换算回 UTC。
        tz_offset_hours = 8 if str(config.get("timezone") or "Asia/Shanghai") == "Asia/Shanghai" else 0
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(hours=tz_offset_hours)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt
    return None


def _summarize(view_kind: str, processed: Any):
    """从处理结果里提炼通用快速字段 (label, value)。"""
    label: Optional[str] = None
    value: Optional[float] = None
    try:
        if isinstance(processed, dict):
            if "value" in processed and isinstance(processed["value"], (int, float)):
                value = float(processed["value"])
                unit = processed.get("unit") or ""
                label = f"{value:g} {unit}".strip()
            elif isinstance(processed.get("points"), list) and processed["points"]:
                last = processed["points"][-1]
                if isinstance(last.get("y"), (int, float)):
                    value = float(last["y"])
                    unit = processed.get("unit") or ""
                    label = f"{value:g} {unit}".strip()
            elif isinstance(processed.get("summary"), dict):
                summary = processed["summary"]
                if isinstance(summary.get("runs"), (int, float)):
                    value = float(summary["runs"])
                    label = f"运行 {summary['runs']} 次"
            elif isinstance(processed.get("counts"), dict):
                total = sum(v for v in processed["counts"].values() if isinstance(v, (int, float)))
                value = float(total)
                label = f"合计 {total:g}"
        elif isinstance(processed, list):
            label = f"{len(processed)} 条"
            value = float(len(processed))
    except Exception:  # noqa: BLE001 - 概要字段是尽力而为，不能影响主流程
        pass
    return label, value


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


# 这些展示方式本身不是「图」，如果数据其实是时间序列，就顺带给一个折线图的 auto_view
_NON_VISUAL_VIEWS = {"markdown", "table", "web_monitor", "task_list"}


def build_success_payload(spec: Dict[str, Any], source: Dict[str, Any], processed: Any,
                          generated_at: str) -> Dict[str, Any]:
    """成功运行的统一 payload；含「更适合的展示方式」提示 auto_view。"""
    from service.widgets.shape import detect_categorical, detect_series

    view = spec.get("view") or {}
    payload: Dict[str, Any] = {
        "ok": True,
        "view": view,
        "result": processed,
        "generated_at": generated_at,
        "source": {
            "kind": source.get("kind"),
            "provider": (source.get("config") or {}).get("provider"),
            "url": (source.get("config") or {}).get("url"),
        },
    }
    if view.get("kind") in _NON_VISUAL_VIEWS:
        series = detect_series(processed)
        categorical = None if series else detect_categorical(processed)
        hit = series or categorical
        if hit:
            payload["auto_view"] = {
                "kind": hit["suggested_view"]["kind"],
                "config": hit["suggested_view"]["config"],
                "data": {"points": hit["points"], "unit": hit["unit"]},
            }
            payload["auto_view_reason"] = (
                "检测到按时间排列的数值，可切换成走势图查看" if series
                else "检测到几个类目的数值，可切换成柱状图查看"
            )
    return payload


_LEVEL_RANK = {"ok": 0, "warn": 1, "alert": 2}


async def _maybe_dispatch_alert(db, user_id: int, widget, payload: Dict[str, Any]) -> None:
    """threshold_alert 处理器给出 level 时，只在等级发生变化那一刻往外推——

    等级持续保持 alert 期间每次调度都推会刷屏；等级变化（升级/降级/恢复正常）才是
    真正值得群里看一眼的信息。level 变化记在 widget.last_alert_level 上做去重判断。
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return
    level = result.get("level")
    if level not in _LEVEL_RANK:
        return

    previous = widget.last_alert_level
    widget.last_alert_level = level
    if level == previous:
        return  # 等级没变化，不重复推送
    if level == "ok" and previous is None:
        return  # 从来没告警过，恢复正常也没必要推

    from service.notification_service import dispatch_alert_async

    if level == "ok":
        title = f"✅ 「{widget.name}」已恢复正常"
    else:
        icon = "🔴" if level == "alert" else "🟠"
        title = f"{icon} 「{widget.name}」触发{'警报' if level == 'alert' else '提醒'}"
    message = str(result.get("text") or result.get("summary") or "")
    try:
        await dispatch_alert_async(db, user_id, title=title, message=message)
    except Exception as exc:  # noqa: BLE001 - 告警推送失败不能影响组件运行本身
        logger.warning(f"告警推送失败: widget_id={widget.id}, user_id={user_id}, error={exc}")


async def run_widget(db, user_id: int, widget_id: int, *, request_id: str = None,
                     trigger: str = "manual") -> WidgetRunResult:
    """运行一个组件并落库一条数据点。db 为 AsyncSession；调用方负责 commit。"""
    from models import user_widget_async_dao as dao

    widget = await dao.get_owned_widget_async(db, user_id, widget_id)
    if not widget:
        from service.exceptions import NotFound
        raise NotFound("组件不存在或无权限")

    spec = spec_from_widget(widget)
    ctx = WidgetRunContext(
        user_id=user_id,
        now=utcnow(),
        widget_id=widget_id,
        widget_type=widget.type,
        db=db,
        request_id=request_id,
        trigger=trigger,
    )

    started = time.time()
    try:
        source = spec.get("data_source") or {}
        connector = CONNECTORS.get(source.get("kind"))
        raw = await connector.fetch(ctx, source.get("config") or {})

        proc = spec.get("processor") or {}
        processor = PROCESSORS.get(proc.get("kind", "passthrough"))
        processed = await _maybe_await(processor(ctx, raw, proc.get("config") or {}))

        view = spec.get("view") or {}
        duration_ms = int((time.time() - started) * 1000)
        label, value = _summarize(view.get("kind", "table"), processed)
        payload = build_success_payload(
            spec, source, processed, ctx.now.strftime("%Y-%m-%d %H:%M:%S")
        )

        await dao.add_data_point_async(
            db, widget_id, ok=1, label=label, value=value,
            payload_json=json.dumps(payload, ensure_ascii=False), duration_ms=duration_ms,
        )
        await dao.apply_retention_async(db, widget_id, now=ctx.now, **_retention_kwargs())

        await _maybe_dispatch_alert(db, user_id, widget, payload)

        widget.last_run_at = ctx.now
        widget.last_status = "ok"
        widget.fail_count = 0
        widget.next_run_at = compute_next_run_at(spec.get("trigger") or {}, ctx.now)
        await db.flush()
        return WidgetRunResult(True, widget_id, payload, label, value, None, duration_ms)
    except Exception as exc:  # noqa: BLE001 - 运行失败要落库成失败数据点，并把错误交给上层
        duration_ms = int((time.time() - started) * 1000)
        message = str(exc)[:500]
        logger.warning(f"组件运行失败: widget_id={widget_id}, user_id={user_id}, error={message}")
        payload = {"ok": False, "view": spec.get("view") or {}, "error": message,
                   "generated_at": ctx.now.strftime("%Y-%m-%d %H:%M:%S")}
        await dao.add_data_point_async(
            db, widget_id, ok=0, label=None, value=None,
            payload_json=json.dumps(payload, ensure_ascii=False), error=message, duration_ms=duration_ms,
        )
        await dao.apply_retention_async(db, widget_id, now=ctx.now, **_retention_kwargs())

        fail_count = int(widget.fail_count or 0) + 1
        widget.last_run_at = ctx.now
        widget.fail_count = fail_count
        trigger = spec.get("trigger") or {}
        limit = max_consecutive_fails()
        is_scheduled = trigger.get("kind", "manual") != "manual"
        if is_scheduled and limit > 0 and fail_count >= limit:
            # 连续失败太多次：停掉自动调度，等用户手动运行成功再恢复
            widget.last_status = "paused"
            widget.next_run_at = None
            logger.warning(
                f"组件连续失败 {fail_count} 次，暂停自动调度: widget_id={widget_id}, user_id={user_id}"
            )
        elif is_scheduled:
            widget.last_status = "error"
            widget.next_run_at = compute_backoff_next_run(fail_count, ctx.now)
        else:
            widget.last_status = "error"
            widget.next_run_at = compute_next_run_at(trigger, ctx.now)
        await db.flush()
        return WidgetRunResult(False, widget_id, payload, None, None, message, duration_ms)


async def run_spec_preview(db, user_id: int, spec: Dict[str, Any], *,
                           request_id: str = None) -> WidgetRunResult:
    """按一份 spec 跑一次完整取数 / 处理流程，但**不落库、不改任何调度状态**。

    给「创建前先看看真实效果」用。连接器 / 处理器 / auto_view 全部走和正式运行一样的代码。
    """
    ctx = WidgetRunContext(
        user_id=user_id,
        now=utcnow(),
        widget_id=None,
        widget_type=spec.get("type"),
        db=db,
        request_id=request_id,
        trigger="preview",
    )
    started = time.time()
    try:
        source = spec.get("data_source") or {}
        connector = CONNECTORS.get(source.get("kind"))
        raw = await connector.fetch(ctx, source.get("config") or {})

        proc = spec.get("processor") or {}
        processor = PROCESSORS.get(proc.get("kind", "passthrough"))
        processed = await _maybe_await(processor(ctx, raw, proc.get("config") or {}))

        duration_ms = int((time.time() - started) * 1000)
        payload = build_success_payload(
            spec, source, processed, ctx.now.strftime("%Y-%m-%d %H:%M:%S")
        )
        label, value = _summarize((spec.get("view") or {}).get("kind", "table"), processed)
        return WidgetRunResult(True, 0, payload, label, value, None, duration_ms)
    except Exception as exc:  # noqa: BLE001 - 预览失败只回错误，不抛 500
        duration_ms = int((time.time() - started) * 1000)
        message = str(exc)[:500]
        payload = {"ok": False, "view": spec.get("view") or {}, "error": message,
                   "generated_at": ctx.now.strftime("%Y-%m-%d %H:%M:%S")}
        return WidgetRunResult(False, 0, payload, None, None, message, duration_ms)
