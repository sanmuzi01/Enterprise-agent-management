import json
import logging
import os
import sys
import time

from datetime import datetime, timezone

from utils.path_tool import get_abs_path

#日志保存的根目录
LOG_ROOT = get_abs_path("logs")

#确保日志的目录存在
os.makedirs(LOG_ROOT, exist_ok=True)

def _ensure_utf8_streams() -> None:
    """输出被重定向到文件/管道（后台服务、CI、容器日志）时统一用 UTF-8，避免 Windows 默认 GBK 把中文日志写成乱码。
    交互式终端保持系统编码（改了反而会在 cmd 里显示乱码）；errors=replace 保证任何字符都不会让日志调用抛异常。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 —— 流不支持 reconfigure（比如被测试框架替换）时保持原样
            pass


_ensure_utf8_streams()

# 每条日志都带上当前请求的 trace_id（没有请求上下文时为 "-"），不需要每个调用点自己传
_original_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    try:
        from service.observability.context import current_trace_id
        record.trace_id = current_trace_id() or "-"
    except Exception:  # noqa: BLE001
        record.trace_id = "-"
    return record


logging.setLogRecordFactory(_record_factory)


class JsonFormatter(logging.Formatter):
    """LOG_FORMAT=json 时输出结构化日志（给 Loki / 云日志用）。消息和异常都经过脱敏；trace_id 是字段，不是索引标签。"""

    def format(self, record: logging.LogRecord) -> str:
        from service.observability.context import current_fields
        from service.observability.redact import redact_text
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname, "logger": record.name, "service": os.getenv("SERVICE_NAME", "agent-service"),
            "environment": os.getenv("APP_ENV", "development"), "trace_id": getattr(record, "trace_id", "-"),
            "message": redact_text(record.getMessage()),
        }
        payload.update({k: v for k, v in current_fields().items() if k in ("user_id", "department_id", "operation", "request_id")})
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class RedactFilter(logging.Filter):
    """所有 handler 统一脱敏：令牌、密钥、手机号、身份证号、password=… 在落盘或输出之前就被遮盖。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from service.observability.redact import redact_text
            record.msg = redact_text(record.getMessage())
            record.args = None
            if record.exc_info:   # 异常堆栈里的消息同样可能带密码、令牌：先格式化再脱敏
                record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
        except Exception:  # noqa: BLE001 —— 脱敏失败不能让日志调用抛异常
            pass
        return True


_REDACT = RedactFilter()


class ConsoleLevelFilter(logging.Filter):
    """控制台日志的最低级别可以用环境变量 CONSOLE_LOG_LEVEL 临时抬高（比如测试时设成 CRITICAL 免得刷屏）。

    每条日志发出时才读取环境变量，所以不依赖模块导入顺序；只影响控制台，文件日志仍按原级别完整记录。
    未设置或写错时不做任何额外过滤。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        raw = os.getenv("CONSOLE_LOG_LEVEL", "").strip().upper()
        level = logging.getLevelName(raw) if raw else 0
        return not isinstance(level, int) or record.levelno >= level


_CONSOLE_LEVEL = ConsoleLevelFilter()


#日志的格式配置：error info debug
DEFAULT_LOG_FORMAT = (JsonFormatter() if os.getenv("LOG_FORMAT", "").lower() == "json"
                      else logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - [%(trace_id)s] - %(message)s"))
#定义日志
def get_logger(
        name:str="agent",
        console_level:int=logging.INFO,
        file_level:int=logging.DEBUG,
        log_file = None,
)->logging.Logger:

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    #避免重复添加Handler，判断
    if logger.handlers:
        return logger

    #控制台Handler,把日志打印到控制终端
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOG_FORMAT)
    console_handler.addFilter(_REDACT)
    console_handler.addFilter(_CONSOLE_LEVEL)

    logger.addHandler(console_handler)

    #文件Handler
    if not log_file:
        log_file = os.path.join(
            LOG_ROOT, #日志文件存放的绝对路径
            f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
        )
        # 创建文件处理器（按天切割，自动保留7天）
        from logging.handlers import TimedRotatingFileHandler
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(DEFAULT_LOG_FORMAT)
        file_handler.addFilter(_REDACT)

        logger.addHandler(file_handler)
    return logger

logger = get_logger()
# 用户行为日志辅助函数（配合 log_to_csv 解析）
def log_user_behavior(user_id: int, action: str, status: str, start: float):
    """
    打印 USER_BEHAVIOR 格式日志
    :param user_id: 用户ID（未知时传 0）
    :param action: 行为类型，如 "login" / "register"
    :param status: "success" 或 "fail"
    :param start: 开始时间戳，由 time.time() 产生
    """
    duration = int((time.time() - start) * 1000)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"USER_BEHAVIOR|{user_id}|{action}|{status}|{duration}|{now}")
#快捷获取日志管理器