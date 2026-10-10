"""上传文件：流式写入临时文件，超限立即停止，不把整个文件读进内存。

上一版虽然有上限，但每个分块都放进列表、最后 `b"".join()`，一个 API Worker 同时处理几个大上传时仍会占用几百 MB。
现在每个分块读到就直接写进临时文件（分块 1MB，进程里同一时刻只有一个分块），业务代码拿到的是 `StoredUpload`
（路径 + 大小），入库时直接把临时文件移到最终位置（同一个文件系统上的 rename），整个过程不需要把文件装进内存。

三个上限（环境变量，默认值与 Nginx 的 `client_max_body_size 50m` 对齐）：
  KNOWLEDGE_UPLOAD_MAX_BYTES          单个文件，默认 50MB
  KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES  一次请求里所有文件累计，默认 50MB
  KNOWLEDGE_UPLOAD_MAX_FILES          批量上传的文件个数，默认 20
另有“用户级上传并发”限制：同一个用户同时进行的上传不超过 USER_MAX_CONCURRENT_UPLOADS（默认 3）。
超限都是立即拒绝：413（太大）/ 400（太多）/ 429（并发太多），并清理已写下的临时文件。
"""
import asyncio
import contextlib
import os
import shutil
import tempfile
import time
from typing import AsyncIterator, Iterable, List, Sequence

from fastapi import HTTPException, UploadFile

from utils.rate_limit import LimitExceeded, concurrency_limiter

CHUNK_BYTES = 1024 * 1024
_MB = 1024 * 1024
TOO_LARGE = 413         # 不用 status.HTTP_413_*：新版 Starlette 把旧名字标成了弃用
BAD_REQUEST = 400
TOO_MANY_REQUESTS = 429
_SWEEP_EVERY_SECONDS = 600
_ORPHAN_AFTER_SECONDS = 3600
_last_sweep = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def max_file_bytes() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_MAX_BYTES", 50 * _MB)


def max_request_bytes() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES", 50 * _MB)


def max_files() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_MAX_FILES", 20)


def incoming_dir() -> str:
    """临时文件放在知识库文件目录下的 .incoming：和最终位置在同一个文件系统，入库时的移动是原子的 rename，不是拷贝。"""
    from utils.path_tool import get_abs_path
    base = get_abs_path(os.getenv("KNOWLEDGE_FILE_PATH", "./knowledge_files"))
    path = os.path.join(base, ".incoming")
    os.makedirs(path, exist_ok=True)
    return path


def _sweep_orphans() -> None:
    """进程在上传途中被杀会留下临时文件：每隔一段时间清掉超过 1 小时的（正在写的不会这么久）。"""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_EVERY_SECONDS:
        return
    _last_sweep = now
    directory = incoming_dir()
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if now - os.path.getmtime(path) > _ORPHAN_AFTER_SECONDS:
                os.remove(path)
        except OSError:
            pass


class StoredUpload:
    """已经落在临时文件里的上传内容：业务代码只需要它的大小，入库时把文件移走。"""

    def __init__(self, path: str, size: int):
        self.path = path
        self.size = size

    def __len__(self) -> int:
        return self.size

    def __bool__(self) -> bool:
        return self.size > 0

    def move_to(self, destination: str) -> None:
        """移动到最终位置。正常是同一个文件系统上的 rename（不拷贝）；临时目录被配到别的磁盘 / 挂载卷上时
        rename 会失败，退回“拷贝再删除”——慢一些，但是流式的，仍然不占内存。之后这个对象不再持有文件。"""
        try:
            os.replace(self.path, destination)
        except OSError:
            shutil.move(self.path, destination)
        self.path = None

    def discard(self) -> None:
        """删除还没被移走的临时文件（已经移走的什么都不做）。"""
        if self.path:
            with contextlib.suppress(OSError):
                os.remove(self.path)
            self.path = None


def _too_large(what: str, limit: int) -> HTTPException:
    return HTTPException(TOO_LARGE, detail=f"{what}超过 {limit / _MB:g}MB 上限")


async def spool_upload(file: UploadFile, *, max_bytes: int = 0, name: str = "") -> StoredUpload:
    """把一个上传文件分块写进临时文件，最多 max_bytes（默认单文件上限）；多出哪怕 1 个字节就报 413，已写的内容删掉。"""
    limit = max_bytes or max_file_bytes()
    label = name or file.filename or "文件"
    await asyncio.to_thread(_sweep_orphans)
    handle = await asyncio.to_thread(tempfile.NamedTemporaryFile, "wb", dir=incoming_dir(), prefix="up_", suffix=".part", delete=False)
    total = 0
    try:
        while True:
            chunk = await file.read(min(CHUNK_BYTES, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _too_large(f"文件 {label}", limit)
            await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.close)
        return StoredUpload(handle.name, total)
    except BaseException:
        with contextlib.suppress(Exception):
            handle.close()
        with contextlib.suppress(OSError):
            os.remove(handle.name)
        raise


async def spool_uploads(files: Iterable[UploadFile]) -> List[StoredUpload]:
    """批量：个数、每个文件、累计三个上限都要满足；任何一个超限就整批拒绝，已经写下的临时文件全部清掉。"""
    files = list(files)
    if not files:
        return []
    if len(files) > max_files():
        raise HTTPException(BAD_REQUEST, detail=f"一次最多上传{max_files()}个文件")
    remaining = max_request_bytes()
    stored: List[StoredUpload] = []
    try:
        for file in files:
            try:
                item = await spool_upload(file, max_bytes=min(max_file_bytes(), remaining))
            except HTTPException as exc:
                if exc.status_code == TOO_LARGE and remaining < max_file_bytes():
                    raise _too_large("本次上传的文件总大小", max_request_bytes()) from None
                raise
            remaining -= item.size
            stored.append(item)
        return stored
    except BaseException:
        for item in stored:
            item.discard()
        raise


def _acquire_user_slot(user_id: int):
    try:
        return concurrency_limiter.acquire(
            key=f"knowledge_upload_inflight:{user_id}",
            limit=_env_int("USER_MAX_CONCURRENT_UPLOADS", 3),
            ttl_seconds=_env_int("UPLOAD_CONCURRENCY_TTL_SECONDS", 300),
            label="文件上传",
        )
    except LimitExceeded as exc:
        raise HTTPException(TOO_MANY_REQUESTS, detail=exc.message, headers={"Retry-After": str(exc.retry_after)}) from None


@contextlib.asynccontextmanager
async def spooled_uploads(user_id: int, files: Sequence[UploadFile]) -> AsyncIterator[List[StoredUpload]]:
    """占一个“用户级上传并发”名额 → 流式写临时文件 → 交给业务代码；退出时（成功或失败）删掉没被移走的临时文件、释放名额。"""
    lease = _acquire_user_slot(user_id)
    stored: List[StoredUpload] = []
    try:
        stored = await spool_uploads(files)
        yield stored
    finally:
        for item in stored:
            item.discard()
        lease.release()
