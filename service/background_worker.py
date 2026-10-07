import asyncio
import atexit
import os
import time

from models.init_db import SessionLocal, bootstrap_database
from service import background_task_service
from utils.logger_handler import get_logger

logger = get_logger("background_worker")

# 组件调度是异步的，但任务 Worker 主体是同步循环。用一个进程内常驻的事件循环来跑
# 异步部分，避免每 60s asyncio.run() 建/拆一个 loop（连带重建 asyncmy 连接池）。
_widget_loop: "asyncio.AbstractEventLoop | None" = None


def _get_widget_loop() -> "asyncio.AbstractEventLoop":
    global _widget_loop
    if _widget_loop is None or _widget_loop.is_closed():
        _widget_loop = asyncio.new_event_loop()
    return _widget_loop


def close_widget_loop() -> None:
    """进程退出前关闭常驻事件循环。

    不关的话，Windows 的 ProactorEventLoop 在解释器退出时会报
    `ResourceWarning: unclosed event loop` 和两个 `unclosed socket`（循环内部的自通知 socketpair），
    此前误以为是 asyncmy 连接泄漏。循环正在别的线程里运行时关不掉，直接放弃（进程马上就退出了）。
    """
    global _widget_loop
    loop, _widget_loop = _widget_loop, None
    if loop is None or loop.is_closed() or loop.is_running():
        return
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:  # noqa: BLE001 —— 退出阶段的清理失败不能影响进程退出
        pass
    finally:
        loop.close()


atexit.register(close_widget_loop)


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，主要用于 Worker 轮询间隔。"""

    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def run_once() -> bool:
    """执行一轮 Worker 调度。

    返回 True 表示本轮领取并执行了任务；返回 False 表示本轮没有可执行任务。
    拆出 run_once 是为了后续可以单独测试 Worker 领取逻辑，而不用启动无限循环。
    """
    db = SessionLocal()
    try:
        # 先恢复超时任务，再领取新任务，避免异常退出留下永久 running。
        requeued = background_task_service.requeue_stale_running_tasks(db)
        if requeued:
            logger.warning(f"已重新排队超时任务: {requeued} 个")

        task = background_task_service.claim_next_task(db)
        if not task:
            db.commit()
            return False

        # 领取任务后先提交事务，释放数据库行锁，再进入真正耗时的任务执行。
        task_id = task.id
        task_type = task.task_type
        db.commit()
        logger.info(f"Worker 领取任务: task={task_id}, type={task_type}")
    except Exception as e:
        db.rollback()
        logger.error(f"Worker 领取任务失败: {e}")
        return False
    finally:
        db.close()

    try:
        return background_task_service.run_task_by_id(task_id)
    except Exception as e:
        # 理论上具体任务函数会自己记录失败；这里是最后一道兜底，避免 Worker 崩掉。
        db = SessionLocal()
        try:
            background_task_service.mark_task_failed(db, task_id, str(e))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        logger.error(f"Worker 执行任务失败: task={task_id}, error={e}")
        return False


def _run_widget_scheduler_tick() -> None:
    """到点的自定义组件调度入口（默认开启，WIDGET_SCHEDULER_ENABLED=0 关闭）。

    真正批量运行的逻辑在 service.widgets.scheduler.run_due_widgets：逐个抢占 + 提交，
    最终都走 runner.run_widget，与手动运行完全一致。
    """
    from service.widgets.scheduler import run_due_widgets, scheduler_enabled

    if not scheduler_enabled():
        return
    try:
        from models.async_db import AsyncSessionLocal

        async def _tick():
            async with AsyncSessionLocal() as db:
                return await run_due_widgets(db)

        summary = _get_widget_loop().run_until_complete(_tick())
        if summary.get("due"):
            logger.info(f"组件调度: {summary}")
    except Exception as exc:  # noqa: BLE001 - 调度失败不能拖垮任务 Worker
        logger.warning(f"组件调度执行失败: {exc}")


def _run_reminder_tick() -> None:
    """业务提醒规则（审批等待、客户未跟进、缺发票、待办到期、每周摘要）。默认开启，
    REMINDERS_ENABLED=0 关闭。每条规则有自己的间隔和租约，多个 Worker 同时运行也只执行一次。"""
    if os.getenv("REMINDERS_ENABLED", "1") == "0":
        return
    try:
        from models.async_db import AsyncSessionLocal
        from service.reminders import run_due_rules

        async def _tick():
            async with AsyncSessionLocal() as db:
                return await run_due_rules(db)

        results = _get_widget_loop().run_until_complete(_tick())
        if results:
            logger.info(f"提醒规则: {results}")
    except Exception as exc:  # noqa: BLE001 - 提醒失败不能拖垮任务 Worker
        logger.warning(f"提醒规则执行失败: {exc}")


def run_forever() -> None:
    """持续运行 Worker。

    生产环境应由 systemd / supervisor / nssm 等进程管理器托管该进程；进程退出后由外部系统拉起。
    """
    # Worker 可能先于 API 启动，需保证表结构就绪（幂等，进程内只跑一次）。
    bootstrap_database()
    poll_seconds = _env_float("TASK_WORKER_POLL_SECONDS", 2.0)
    widget_poll_seconds = _env_float("WIDGET_SCHEDULER_POLL_SECONDS", 60.0)
    reminder_poll_seconds = _env_float("REMINDER_POLL_SECONDS", 60.0)

    from service.widgets.scheduler import scheduler_enabled

    logger.info(
        f"后台任务 Worker 已启动，poll={poll_seconds}s，"
        f"组件调度={'开启' if scheduler_enabled() else '关闭'}（每 {widget_poll_seconds:g}s 一轮）"
    )
    last_widget_tick = 0.0
    last_reminder_tick = 0.0
    try:
        while True:
            handled = run_once()
            now = time.time()
            if now - last_widget_tick >= widget_poll_seconds:
                _run_widget_scheduler_tick()
                last_widget_tick = now
            if now - last_reminder_tick >= reminder_poll_seconds:
                _run_reminder_tick()
                last_reminder_tick = now
            if not handled:
                time.sleep(poll_seconds)
    finally:
        if _widget_loop is not None and not _widget_loop.is_closed():
            _widget_loop.close()


if __name__ == "__main__":
    run_forever()
