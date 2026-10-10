"""压缩包与文件路径的统一安全检查（zip 炸弹、路径穿越、Windows 特有的路径花样）。

所有“用户上传的 zip / xlsx / docx / 技能包”在真正解压或交给解析库之前都过这里：
- **压缩炸弹**：成员数、解压后总大小、单个成员的压缩比（几百 KB 的文件解压出几 GB）；
  这里按压缩包头部声明的大小判断，而 `zipfile` 读取时本来就按声明的大小截断，所以伪造头部声明小体积没有用；
- **路径穿越**：`..`、绝对路径、Windows 盘符（`C:/x` 在 `os.path.join` 里会让前面的目录整个失效）、
  UNC（`//server/share`）、NTFS 备用数据流（`a.txt:evil`）、控制字符与 NUL、`CON` / `NUL` 这类保留设备名、
  末尾带点或空格的名字（Windows 会悄悄去掉）；
- 写文件前再用 `ensure_within` 做一次真实路径包含检查（兜底：不信任任何字符串层面的判断）。
"""
import io
import os
import zipfile
from typing import List

MAX_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_RATIO = 150            # 单个成员压缩比上限：正常的 xlsx / docx 文本部分一般在 5–30 倍
RATIO_FLOOR_BYTES = 1024 * 1024   # 小于 1MB 的成员不看压缩比（空白填充的小文件压缩比天然高）

_RESERVED = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ArchiveRejected(ValueError):
    """压缩包不安全；消息可以直接展示给用户。"""


def safe_member_name(name: str) -> bool:
    if not name or len(name) > 260:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or any(ord(ch) < 32 for ch in normalized) or ":" in normalized:
        return False
    for part in normalized.split("/"):
        if part == "..":
            return False
        if part not in (".", "") and part != part.rstrip(". "):
            return False
        if part.split(".")[0].strip().lower() in _RESERVED:
            return False
    return True


def inspect_zip(content: bytes, *, max_members: int = MAX_MEMBERS, max_uncompressed: int = MAX_UNCOMPRESSED_BYTES,
                max_ratio: int = MAX_RATIO, check_names: bool = True) -> List[zipfile.ZipInfo]:
    """打开压缩包做体检，返回成员列表；不安全就抛 ArchiveRejected。不解压任何内容。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ArchiveRejected("文件已损坏，无法打开") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > max_members:
            raise ArchiveRejected(f"压缩包里有 {len(infos)} 个文件，超过上限 {max_members}，已拒绝处理")
        total = 0
        for info in infos:
            total += info.file_size
            if total > max_uncompressed:
                raise ArchiveRejected("文件解压后过大或结构异常，已拒绝处理")
            if info.file_size > RATIO_FLOOR_BYTES and info.file_size > max_ratio * max(info.compress_size, 1):
                raise ArchiveRejected("文件压缩比异常（疑似压缩炸弹），已拒绝处理")
            if check_names and not safe_member_name(info.filename):
                raise ArchiveRejected("压缩包里含有不安全的文件路径，已拒绝处理")
        return infos


def ensure_within(base: str, target: str) -> str:
    """写文件前的兜底：目标真实路径必须在 base 目录内，否则抛 ArchiveRejected。返回目标的绝对路径。"""
    real_base = os.path.realpath(base)
    real_target = os.path.realpath(target)
    try:
        inside = os.path.commonpath([real_base, real_target]) == real_base
    except ValueError:        # 不同盘符
        inside = False
    if not inside:
        raise ArchiveRejected("压缩包里含有不安全的文件路径，已拒绝处理")
    return real_target
