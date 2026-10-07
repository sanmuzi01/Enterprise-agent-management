"""统一错误码目录：每个错误码说明给用户看什么、能不能重试、要不要进问题中心。

约定：预期的业务异常（校验失败、没有权限、资源不存在、冲突）不进问题中心也不进 Sentry；
代码缺陷、依赖不可用、数据不一致、任务最终失败才进。新增错误码在这里登记；没登记的 code 按 http_status 给默认值。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str            # 给用户看的话（不含内部细节）
    suggestion: str
    http_status: int
    retryable: bool = False
    retry_after: Optional[int] = None
    issue: bool = False     # 是否进入问题中心
    severity: str = "medium"
    category: str = "code"


_SPECS = [
    # 预期的业务异常：只给用户提示，不进问题中心
    ErrorSpec("invalid_input", "提交的内容不符合要求", "请按提示修改后重试", 400),
    ErrorSpec("unauthorized", "登录已失效", "请重新登录", 401),
    ErrorSpec("permission_denied", "没有权限执行这个操作", "如需使用请联系部门负责人或管理员", 403),
    ErrorSpec("not_found", "找不到对应的内容", "请刷新页面确认它是否还存在", 404),
    ErrorSpec("conflict", "操作与当前状态冲突", "请刷新后重试", 409),
    ErrorSpec("rate_limited", "操作太频繁", "请稍后再试", 429, retryable=True, retry_after=30),
    ErrorSpec("quota_exceeded", "用量额度已用完", "请等待额度重置或联系管理员", 429),
    # 依赖与系统故障：进入问题中心
    ErrorSpec("JAVA_SERVICE_UNAVAILABLE", "企业业务服务暂时不可用", "请稍后重试，系统已记录该问题", 503, True, 15,
              issue=True, severity="high", category="dependency"),
    ErrorSpec("JAVA_SERVICE_ERROR", "企业业务服务处理出错", "请稍后重试，系统已记录该问题", 502, True, 10,
              issue=True, severity="high", category="dependency"),
    ErrorSpec("MODEL_TIMEOUT", "AI 模型响应超时", "请稍后重试，或换一个模型", 504, True, 10,
              issue=True, severity="medium", category="dependency"),
    ErrorSpec("MODEL_UNAVAILABLE", "AI 模型服务暂时不可用", "请检查模型连接后重试", 502, True, 20,
              issue=True, severity="medium", category="dependency"),
    ErrorSpec("upstream_error", "依赖的外部服务出错", "请稍后重试，系统已记录该问题", 502, True, 15,
              issue=True, severity="medium", category="dependency"),
    ErrorSpec("DATABASE_ERROR", "数据库暂时不可用", "请稍后重试，系统已记录该问题", 503, True, 10,
              issue=True, severity="critical", category="dependency"),
    ErrorSpec("KAFKA_CONSUME_FAILED", "后台事件处理失败", "系统会自动重试，持续失败会通知管理员", 500, True, None,
              issue=True, severity="high", category="task"),
    ErrorSpec("TASK_FAILED", "后台任务最终失败", "请在任务列表里查看原因并重试", 500, True, None,
              issue=True, severity="medium", category="task"),
    ErrorSpec("FRONTEND_ERROR", "页面出现错误", "请刷新页面重试；如果持续出现，请把问题编号告诉管理员", 500, False, None,
              issue=True, severity="medium", category="code"),
    ErrorSpec("SECURITY_ANOMALY", "检测到异常的访问行为", "如非本人操作请联系管理员", 403,
              issue=True, severity="high", category="security"),
    ErrorSpec("INTERNAL_ERROR", "系统出了点问题", "请稍后重试；如果持续出现，请把问题编号告诉管理员", 500, True, 10,
              issue=True, severity="high", category="code"),
    ErrorSpec("app_error", "系统出了点问题", "请稍后重试；如果持续出现，请把问题编号告诉管理员", 500, True, 10,
              issue=True, severity="high", category="code"),
]
CATALOG = {spec.code: spec for spec in _SPECS}


def spec_for(code: Optional[str], http_status: int = 500) -> ErrorSpec:
    if code in CATALOG:
        return CATALOG[code]
    if http_status >= 500:
        return CATALOG["INTERNAL_ERROR"]
    return ErrorSpec(code or "error", "请求没有成功", "请稍后重试", http_status)


def should_report(code: Optional[str], http_status: int) -> bool:
    """是否进入问题中心 / Sentry：5xx 且目录里没有明确标成"不进"；安全异常无论状态码都进。"""
    spec = spec_for(code, http_status)
    if spec.category == "security" and spec.issue:
        return True
    return http_status >= 500 and (spec.issue or code not in CATALOG)
