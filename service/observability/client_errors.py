"""前端页面错误进入问题中心。

浏览器里的脚本错误（组件渲染报错、未处理的 Promise 拒绝）只有用户自己看得到，管理员完全不知道。前端把它们报到这里，
和后端故障一样按 fingerprint 聚合成一条问题：同一处代码出错多少次累计多少次，换了打包哈希、行号、页面上的数字不会拆成新问题。

边界：
- 不记录用户身份和页面内容，只记录错误名、规范化后的路由、出错的文件名（不含打包哈希和行列号）、脱敏后的消息和调用栈；
- 浏览器扩展注入的脚本、跨域的 "Script error."、ResizeObserver 这类无意义的噪声直接丢弃；
- 匿名可报（登录页出错也要能看到），所以有按 IP 和全局两层限流，字段长度都有上限。
"""
import re
from typing import Any, Dict, Optional

from service.observability import issues
from service.observability.redact import redact_text

SERVICE = "web-frontend"
ERROR_CODE = "FRONTEND_ERROR"

_NOISE = re.compile(r"ResizeObserver loop|^Script error\.?$|Non-Error promise rejection captured|Loading chunk .* failed|Failed to fetch dynamically imported module", re.I)
_EXTENSION = re.compile(r"(chrome|moz|safari)-extension://", re.I)
_FRAME_FILE = re.compile(r"([\w.\-]+\.(?:vue|ts|tsx|js|mjs))(?:\?[^\s:)]*)?(?::\d+){0,2}")
_HASH = re.compile(r"-[A-Za-z0-9_]{8}(?=\.(?:js|mjs)$)")
_ID = re.compile(r"/(?:\d+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})(?=/|$)")
_NAME = re.compile(r"[A-Za-z]{1,40}")


def normalize_route(route: str) -> str:
    path = (route or "/").split("?")[0].split("#")[0][:120] or "/"
    return _ID.sub("/:id", path)


def frames_of(stack: str, limit: int = 3) -> str:
    """调用栈里最靠前的几个文件名（去掉打包哈希、查询串和行列号），相邻重复只算一个。"""
    files = []
    for line in (stack or "").splitlines():
        match = _FRAME_FILE.search(line)
        if not match:
            continue
        name = _HASH.sub("", match.group(1))
        if not files or files[-1] != name:
            files.append(name)
        if len(files) >= limit:
            break
    return ">".join(files)


def is_noise(message: str, stack: str) -> bool:
    return bool(_NOISE.search(message or "")) or bool(_EXTENSION.search(stack or ""))


def report(*, name: str, message: str, stack: str = "", route: str = "", source: str = "", user_agent: str = "",
           trace_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """登记一次页面错误；噪声返回 {"ignored": True}；登记失败返回 None（只记日志，不影响页面）。"""
    if is_noise(message, stack):
        return {"ignored": True}
    error_name = (_NAME.match(name or "") or [None])[0] or "Error"
    route = normalize_route(route)
    frames = frames_of(stack)
    # 没有调用栈（比如 Promise 拒绝了一个字符串）时用消息本身做区分，把其中的数字抹掉避免每次都是新问题
    discriminator = frames or re.sub(r"\d+", "N", redact_text(message))[:80]
    outcome = issues.record_occurrence(
        error_code=ERROR_CODE, http_status=500, operation=f"{error_name}@{route}", message=message, trace_id=trace_id,
        dependency=discriminator, service=SERVICE,
        extra={"route": route, "source": (source or "")[:60], "frames": frames, "stack": redact_text(stack or "")[:2500],
               "user_agent": (user_agent or "")[:160]})
    return outcome
