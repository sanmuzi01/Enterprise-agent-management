"""工作流的"业务系统核对"：拿整理结果去查真实业务数据，把结论放到核对页上。

结论分三级：info（参考信息）、warning（需要人确认）、blocker（按业务规则保存/提交会被拒绝）。
核对只读，不写任何业务数据；保存时仍以 Java 业务系统的校验为准，这里只是让人提前看到。
"""
import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from service import enterprise_hub_client as hub

UNAVAILABLE = "业务系统暂不可用，未能完成核对；保存时仍以业务系统校验为准"


def info(text: str) -> Dict[str, str]:
    return {"level": "info", "text": text}


def warning(text: str) -> Dict[str, str]:
    return {"level": "warning", "text": text}


def blocker(text: str) -> Dict[str, str]:
    return {"level": "blocker", "text": text}


async def read(path: str, user_id: int, team_id: Optional[int], scope: str, operation: str,
               params: Optional[Dict[str, Any]] = None) -> Any:
    if params:
        path = f"{path}?{urlencode(params)}"
    return await asyncio.to_thread(hub.call, "GET", path, user_id, team_id, [scope], operation)


def money(value) -> str:
    return f"¥{float(value):,.2f}"


async def run_checks(workflow, user_id: int, team_id: int, data: Dict[str, Any], work) -> List[Dict[str, str]]:
    if workflow.business_checks is None:
        return []
    try:
        return await workflow.business_checks(user_id, team_id, data, work)
    except hub.EnterpriseHubError:
        return [warning(UNAVAILABLE)]
    except Exception:  # noqa: BLE001 —— 核对失败不能影响整理结果本身
        return [warning(UNAVAILABLE)]
