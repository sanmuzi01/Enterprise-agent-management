"""数据密级出口策略（第五轮审计 P0-1）。

Agent/KnowledgeSpace/Skill 都有 `sensitivity` 字段（public/internal/
confidential/restricted，见 models/init_db.py），但之前只是建了列，没有任何
业务代码真正读它——检索到的知识库内容不管密级是什么，都会原样拼进 system
prompt（`service/runtime/agent_runtime.py::_compose_kb_prompt`），发给 Agent
配置的模型，可能是 DeepSeek、智谱这类外部云 API。等于 `confidential`/
`restricted` 文档一样会被送到企业没有直接控制权的第三方服务。

这里在检索结果进入 prompt 之前加一道硬拦截，是"先堵住外发这个最紧急的口子"
的第一步，不含正则脱敏/语义 DLP（复杂度更高，配置项/误判率都需要先看这一步
的实际效果再决定要不要做，见 docs/enterprise-rbac-plan.md 相关记录）：

- `restricted`：不管 Agent 配的是什么模型都直接拒绝——当前平台没有任何
  "不出网"的本地模型选项（没有 Ollama 之类的集成，`service/llm/
  model_catalog.py` 里的每一个 chat 模型都是外部 HTTP API），没有安全的
  地方可以放这类内容，所以不尝试"脱敏后放行"，直接整体拒绝进入 prompt。
- `confidential`：只有 Agent 当前配置的模型在 `CONFIDENTIAL_TIER_ALLOWED_MODELS`
  这个环境变量配置的白名单里才放行——默认空（没配等于什么模型都不批准），
  需要企业管理员显式把认为可信的模型名加进这个逗号分隔列表，跟这个项目里
  其它"企业策略"统一走环境变量配置的惯例一致（SMS_PROVIDER/TRUSTED_HOSTS
  等），不是新建一整套数据库表 + 管理后台 UI——如果以后需要运行时热改，
  可以在这个基础上加一张表，现在先用最小成本堵住这个口子。
- `public`/`internal`：不限制（跟改动前的实际行为一致）。
"""
import os
from typing import Any, Dict, List, Optional, Tuple

from utils.logger_handler import get_logger

logger = get_logger("data_egress_policy")

_RESTRICTED = "restricted"
_CONFIDENTIAL = "confidential"


def _confidential_allowed_models() -> set:
    raw = os.getenv("CONFIDENTIAL_TIER_ALLOWED_MODELS", "")
    return {m.strip() for m in raw.split(",") if m.strip()}


def is_model_allowed(model_name: str, sensitivity: str) -> bool:
    """给定内容密级，判断当前 Agent 用的模型能不能访问这部分内容。"""
    if sensitivity == _RESTRICTED:
        return False
    if sensitivity == _CONFIDENTIAL:
        return model_name in _confidential_allowed_models()
    return True  # public / internal：不限制


async def filter_hits_by_sensitivity_async(
    db, hits: List[Dict[str, Any]], mode: str, agent_sensitivity: str, model_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按密级过滤检索到的 hits，返回 (放行的 hits, 被拒绝的 hits)。

    mode == "spaces"：密级来自每个 hit 的 source.space_id 对应的
    `KnowledgeSpace.sensitivity`——批量查一次全部涉及的 space_id，不是逐条
    查（这次检索里最多几个空间，不是几十个 chunk）。
    mode == "agent"（遗留的"Agent 私有库"检索，没有空间概念，见
    `service/rag/search_entry.py::search_for_agent_async`）：整批直接用
    `Agent.sensitivity` 本身。
    """
    if not hits:
        return [], []

    if mode != "spaces":
        if is_model_allowed(model_name, agent_sensitivity):
            return hits, []
        logger.warning(
            f"Agent 私有库内容按密级拒绝外发: sensitivity={agent_sensitivity}, model={model_name}, "
            f"hit_count={len(hits)}"
        )
        return [], hits

    space_ids = {
        h["source"]["space_id"] for h in hits
        if h.get("source", {}).get("space_id") is not None
    }
    sensitivities = await _load_space_sensitivities_async(db, space_ids) if space_ids else {}

    allowed: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for h in hits:
        sid = h.get("source", {}).get("space_id")
        sensitivity = sensitivities.get(sid, "internal")
        if is_model_allowed(model_name, sensitivity):
            allowed.append(h)
        else:
            blocked.append(h)

    if blocked:
        blocked_levels = sorted({
            sensitivities.get(h.get("source", {}).get("space_id"), "internal") for h in blocked
        })
        logger.warning(
            f"按密级拒绝 {len(blocked)}/{len(hits)} 条检索结果外发: levels={blocked_levels}, model={model_name}"
        )
    return allowed, blocked


async def _load_space_sensitivities_async(db, space_ids) -> Dict[int, str]:
    from sqlalchemy import select
    from models.init_db import KnowledgeSpace

    rows = (await db.execute(
        select(KnowledgeSpace.id, KnowledgeSpace.sensitivity).where(KnowledgeSpace.id.in_(space_ids))
    )).all()
    return {r[0]: r[1] for r in rows}


def build_refusal_note(blocked: List[Dict[str, Any]], sensitivities: Optional[Dict[int, str]] = None) -> str:
    """给用户看的一句话说明，附在检索结果里（不是拼进 system prompt 的正文，
    只是解释"为什么参考资料变少了"，避免用户以为知识库里根本没有相关内容）。"""
    if not blocked:
        return ""
    return (
        f"（另有 {len(blocked)} 条检索结果因内容密级限制未提供给当前模型，"
        "如需查看请联系企业管理员确认模型访问策略。）"
    )
