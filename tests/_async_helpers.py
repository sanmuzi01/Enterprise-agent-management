"""跑异步测试协程的统一入口（Phase 3 收尾：修 asyncmy 测试警告）。

现象：`models/async_db.py` 的 `async_engine` 是进程级单例，但测试里到处直接写
`asyncio.run(coro)`——每次调用都新建一个事件循环、跑完就关掉。事件循环关掉之后，
连接池里还没被显式关闭的 asyncmy 连接会在某次垃圾回收时才尝试真正关闭连接，那时候
早就没有活着的事件循环去驱动它的 `await`，于是报
`AttributeError: 'NoneType' object has no attribute 'send'`——不是真的连接泄漏，
是"关闭动作发生在了错误的事件循环生命周期之外"。

根因既然是"进程级连接池 + 每次用完即关的事件循环"，正确修法是**在当前这个循环还活着
的时候，主动把这一轮用到的连接都交还/关闭**——即调用 `await async_engine.dispose()`，
不要留给垃圾回收在不确定的时机、不确定的循环上下文里去做。极少数测试文件已经手写过
这一步（test_agent_runtime_async.py 等），这里统一成一个helper，其余测试改成用它，
不用每个文件自己记得写。
"""
import asyncio
import os
from typing import Awaitable, Callable, TypeVar

os.environ.setdefault("ASYNC_DB_POOL", "null")   # 必须在导入 models.async_db 之前设置

from models.async_db import async_engine  # noqa: E402

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """跑一个协程，跑完（不管成功还是抛异常）都在同一个事件循环里 dispose 连接池。"""

    async def _wrapped() -> T:
        try:
            return await coro
        finally:
            await async_engine.dispose()

    return asyncio.run(_wrapped())


def run_async_factory(make_coro: Callable[[], Awaitable[T]]) -> T:
    """跟 run_async 一样，但接收一个"造协程"的函数而不是协程本身——协程只能被
    await 一次，测试里如果要在同一个 asyncio.run 里跑好几步、又想复用这个 helper
    做统一 dispose，用这个变体：`run_async_factory(lambda: _do())`。
    """
    return run_async(make_coro())
