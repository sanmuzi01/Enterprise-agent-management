"""依赖状态变化 → 问题中心：熔断打开时登记（或累加）一次依赖故障，恢复时在同一个问题上写一条“已恢复”记录。
只在状态变化时触发（见 http_resilience.CircuitBreaker），所以一次 Java 服务中断只有一次有效告警。"""
from service.observability import context as trace_context
from service.observability.issues import add_system_event, make_fingerprint, record_occurrence, SERVICE_NAME
from models.init_db import SessionLocal, SystemIssue
from sqlalchemy import select

_CODES = {"enterprise_hub": "JAVA_SERVICE_UNAVAILABLE"}
_NAMES = {"enterprise_hub": "企业业务服务（Java）"}


def _code(name: str) -> str:
    return _CODES.get(name, "upstream_error")


def on_circuit_change(name: str, state: str) -> None:
    code = _code(name)
    if state == "opened":
        record_occurrence(error_code=code, http_status=503, operation=f"依赖 {_NAMES.get(name, name)}", dependency=name,
                          message=f"{_NAMES.get(name, name)} 连续失败，熔断已打开", trace_id=trace_context.current_trace_id(),
                          extra={"circuit": "opened"})
        return
    fingerprint = make_fingerprint(service=SERVICE_NAME, error_code=code, operation=f"依赖 {_NAMES.get(name, name)}", dependency=name)
    db = SessionLocal()
    try:
        issue_id = db.execute(select(SystemIssue.id).where(SystemIssue.fingerprint == fingerprint)).scalar()
    finally:
        db.close()
    if issue_id:
        add_system_event(issue_id, "dependency_recovered", f"{_NAMES.get(name, name)}已恢复（熔断关闭）")


def install() -> None:
    from service.http_resilience import circuit_breaker
    circuit_breaker.listener = on_circuit_change
