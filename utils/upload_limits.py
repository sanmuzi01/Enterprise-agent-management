"""上传文件的大小上限：分块读取，超限立即停止，不把整个文件无上限地读进内存。

FastAPI 解析 multipart 时已经把大文件落到临时文件里（UploadFile 是 SpooledTemporaryFile），这里要保证的是
“交给业务服务的内容”有明确上限：每个文件一个上限，一次请求里所有文件的累计再有一个上限，批量上传还限制文件个数。
超过上限时抛 413，不会先读完再判断（绕过代理、分块传输、配置错误时，这是最后一道防线）。

环境变量：
  KNOWLEDGE_UPLOAD_MAX_BYTES          单个文件，默认 50MB
  KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES  一次请求累计，默认 200MB
  KNOWLEDGE_UPLOAD_MAX_FILES          批量上传的文件个数，默认 20
"""
import os
from typing import Iterable, List

from fastapi import HTTPException, UploadFile

CHUNK_BYTES = 1024 * 1024
_MB = 1024 * 1024
TOO_LARGE = 413         # 不用 status.HTTP_413_*：新版 Starlette 把旧名字标成了弃用
BAD_REQUEST = 400


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def max_file_bytes() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_MAX_BYTES", 50 * _MB)


def max_request_bytes() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES", 200 * _MB)


def max_files() -> int:
    return _env_int("KNOWLEDGE_UPLOAD_MAX_FILES", 20)


def _too_large(what: str, limit: int) -> HTTPException:
    return HTTPException(TOO_LARGE, detail=f"{what}超过 {limit / _MB:g}MB 上限")


async def read_upload(file: UploadFile, *, max_bytes: int = 0, name: str = "") -> bytes:
    """读一个上传文件，最多读到 max_bytes（默认单文件上限）；多读到哪怕 1 个字节就报 413，已读的内容丢弃。"""
    limit = max_bytes or max_file_bytes()
    label = name or file.filename or "文件"
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(CHUNK_BYTES, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _too_large(f"文件 {label}", limit)
        chunks.append(chunk)
    return b"".join(chunks)


async def read_uploads(files: Iterable[UploadFile]) -> List[bytes]:
    """批量上传：个数、每个文件、累计三个上限都要满足；任何一个超限就整批拒绝。"""
    files = list(files)
    if not files:
        return []
    if len(files) > max_files():
        raise HTTPException(BAD_REQUEST, detail=f"一次最多上传{max_files()}个文件")
    remaining = max_request_bytes()
    contents: List[bytes] = []
    for file in files:
        try:
            content = await read_upload(file, max_bytes=min(max_file_bytes(), remaining))
        except HTTPException as exc:
            if exc.status_code == TOO_LARGE and remaining < max_file_bytes():
                raise _too_large("本次上传的文件总大小", max_request_bytes()) from None
            raise
        remaining -= len(content)
        contents.append(content)
    return contents
