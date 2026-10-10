"""HTTP 响应头辅助。"""
import re
import unicodedata
from urllib.parse import quote


def attachment_disposition(filename: str) -> str:
    """生成安全的 `Content-Disposition: attachment` 值（RFC 6266 / 5987）。

    HTTP 头只能是 Latin-1：直接把中文文件名放进 `filename="..."` 会让响应在编码时抛异常（500）。
    做法：`filename` 给一个 ASCII 兜底名，`filename*=UTF-8''...` 给百分号编码的真实名字，现代浏览器优先用后者。
    同时去掉引号、路径分隔符、控制字符（包括 CR/LF，防止响应头注入）和双向文字覆盖字符。
    """
    cleaned = "".join(ch for ch in (filename or "") if unicodedata.category(ch)[0] != "C" and ch not in '"\\/:*?<>|;,')
    cleaned = cleaned.strip().strip(".") or "download"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned.encode("ascii", "ignore").decode("ascii")).strip("_.") or "download"
    ext = re.search(r"\.[A-Za-z0-9]{1,8}$", cleaned)
    if ext and not ascii_name.lower().endswith(ext.group(0).lower()):
        ascii_name += ext.group(0)
    return f"attachment; filename=\"{ascii_name[:100]}\"; filename*=UTF-8''{quote(cleaned[:150], safe='')}"
