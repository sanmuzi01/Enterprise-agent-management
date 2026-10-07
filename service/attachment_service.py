"""聊天附件与脚本产出文件的存取。

按用户隔离：<ATTACHMENT_DIR>/u<user_id>/<id>/<文件名>。id 是随机 24 位十六进制，
读取时必须同时给出 user_id，所以别人猜到 id 也拿不到。不建表，过期的目录在上传时顺手清理。
"""
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
_ID_RE = re.compile(r"^[0-9a-f]{24}$")
BLOCKED_EXTS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".msi", ".scr", ".com", ".jar", ".sh", ".so", ".dylib"}


class AttachmentError(ValueError):
    """message 可直接展示给用户。"""


def _root() -> Path:
    return Path(os.getenv("ATTACHMENT_DIR") or (BASE_DIR / "data" / "attachments"))


def _ttl_seconds() -> int:
    try:
        return max(1, int(os.getenv("ATTACHMENT_TTL_DAYS", "7"))) * 86400
    except ValueError:
        return 7 * 86400


_WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip().strip(".")
    base = re.sub(r"[^\w\-. 一-鿿()（）]+", "_", base)[:120].strip()
    if base.split(".")[0].strip().lower() in _WINDOWS_DEVICE_NAMES:
        base = "_" + base          # 在 Windows 上，写名为 con.txt / NUL 的文件会写进设备，文件根本不会出现
    return base or "file"


def save(user_id: int, filename: str, content: bytes, check_ext: bool = True) -> Dict:
    name = safe_filename(filename)
    if check_ext and os.path.splitext(name.lower())[1] in BLOCKED_EXTS:
        raise AttachmentError("不支持上传可执行文件或脚本")
    if not content:
        raise AttachmentError("文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise AttachmentError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    user_dir = _root() / f"u{int(user_id)}"
    _purge_expired(user_dir)
    used_bytes, used_files = _usage(user_dir)
    if used_files >= _limit("ATTACHMENT_USER_MAX_FILES", 50):
        raise AttachmentError("你的文件数量已达上限，请等旧文件过期（保留 %d 天）后再上传" % (_ttl_seconds() // 86400))
    if used_bytes + len(content) > _limit("ATTACHMENT_USER_MAX_MB", 100) * 1024 * 1024:
        raise AttachmentError("你的文件空间已满，请等旧文件过期（保留 %d 天）后再上传" % (_ttl_seconds() // 86400))
    att_id = uuid.uuid4().hex[:24]
    folder = user_dir / att_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)
    return {"id": att_id, "name": name, "size": len(content)}


def resolve(user_id: int, att_id: str) -> Optional[Tuple[Path, str]]:
    """返回 (文件路径, 文件名)；id 非法、不属于该用户或已过期返回 None。"""
    if not isinstance(att_id, str) or not _ID_RE.match(att_id):
        return None
    folder = _root() / f"u{int(user_id)}" / att_id
    if not folder.is_dir():
        return None
    files = [p for p in folder.iterdir() if p.is_file()]
    return (files[0], files[0].name) if files else None


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _usage(user_dir: Path) -> Tuple[int, int]:
    """该用户当前占用的 (字节数, 文件数)。"""
    if not user_dir.is_dir():
        return 0, 0
    total = count = 0
    for child in user_dir.iterdir():
        if child.is_dir():
            for f in child.iterdir():
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
    return total, count


def _purge_expired(user_dir: Path) -> None:
    if not user_dir.is_dir():
        return
    cutoff = time.time() - _ttl_seconds()
    for child in user_dir.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue
