"""离线演示模型 `demo-offline`：不联网、不需要 API Key，用确定性规则代替大模型，
让演示环境（面试现场、断网、没有额度）里的 AI 整理链路也能完整跑通——
材料 → 整理成结构化草稿（带原文依据）→ 业务系统核对 → 人工核对 → 保存。

它不是“更聪明的模型”，明确只是规则抽取：
- 只在 `OFFLINE_DEMO_MODEL=1` 且非生产环境时存在（见 enabled()）；生产环境即使误设置也不会注册；
- 整理类请求（系统提示里带有某个工作流的整理要求）按工作流的 schema 输出 JSON，依据一律取原文的逐字片段，
  拿不准的写进 warnings，不编造；
- 普通聊天只给一句固定说明，不假装回答问题；绑定工具时不会发起工具调用。
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MODEL_NAME = "demo-offline"
NOTICE = "【离线演示模型】当前是规则抽取，不是真实大模型：仅能整理请假、报销、IT 工单、采购、客户跟进、会议纪要责任计划材料。"


def enabled() -> bool:
    from service.config_validation import is_production
    return os.getenv("OFFLINE_DEMO_MODEL", "").strip().lower() in ("1", "true", "yes") and not is_production()


# ---------------------------------------------------------------- 通用小工具

def _clauses(text: str) -> List[str]:
    return [c.strip() for c in re.split(r"[。；;！!？?\n]+", text) if c.strip()]


def _first(text: str, limit: int = 60) -> str:
    return text.strip()[:limit]


_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def _dates(text: str) -> List[str]:
    return [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE.findall(text)]


# ---------------------------------------------------------------- 各工作流的规则抽取

def _leave(source: str) -> Dict[str, Any]:
    kind = next((code for word, code in (("年假", "annual"), ("病假", "sick"), ("事假", "personal")) if word in source), None)
    dates = _dates(source)
    warnings = []
    if kind is None:
        warnings.append("没有明确假期类型，请补全")
    if len(dates) < 2:
        warnings.append("起止日期不完整或使用了相对日期（如“下周二”），请补全完整日期")
    evidence = next((c for c in _clauses(source) if any(w in c for w in ("请假", "年假", "病假", "事假", "休假"))), _first(source))
    reason = re.search(r"(?:原因是|因为|由于)([^，。；;]+)", source)
    return {"leave_type_code": kind, "start_date": dates[0] if len(dates) >= 2 else None,
            "end_date": dates[1] if len(dates) >= 2 else None, "reason": reason.group(1).strip() if reason else "",
            "evidence": evidence[:500], "warnings": warnings}


_EXPENSE_CATEGORIES = (
    ("TRAVEL", ("高铁", "机票", "火车", "酒店", "住宿", "出差", "差旅")),
    ("TRANSPORT", ("打车", "出租", "地铁", "滴滴", "公交", "停车", "加油")),
    ("MEAL", ("餐", "饭", "宴请", "招待", "咖啡")),
    ("OFFICE_SUPPLY", ("文具", "打印", "办公", "耗材", "快递")),
)


def _expense(source: str) -> Dict[str, Any]:
    lines, warnings = [], []
    for piece in re.split(r"[；;。\n]+", source):
        for part in re.split(r"，(?=[^，]*?\d+(?:\.\d+)?\s*元)", piece):
            amount = re.search(r"(\d+(?:\.\d{1,2})?)\s*元", part)
            if not amount or float(amount.group(1)) <= 0:
                continue
            invoice = re.search(r"发票号[：:]?\s*([A-Za-z0-9\-]{3,40})", part)
            category = next((c for c, words in _EXPENSE_CATEGORIES if any(w in part for w in words)), "OTHER")
            description = re.sub(r"(\d+(?:\.\d{1,2})?)\s*元|发票号[：:]?\s*[A-Za-z0-9\-]+|暂无发票|无发票", "", part)
            description = re.sub(r"^[\d月日号]+[，,\s]*", "", description).strip(" ，,、：:") or "费用"
            lines.append({"category": category, "amount": f"{float(amount.group(1)):.2f}", "description": description[:300],
                          "invoice_no": invoice.group(1) if invoice else None, "evidence": part.strip()[:500]})
            if not invoice:
                warnings.append(f"“{description[:20]}”没有发票号")
    if not lines:
        warnings.append("没有找到带“元”的明确金额，请补充")
    return {"lines": lines, "warnings": warnings}


def _ticket(source: str) -> Dict[str, Any]:
    rules = (
        ("DEVICE", ("申请", "领用", "换新"), ("笔记本", "电脑", "显示器", "键盘", "鼠标", "手机", "设备")),
        ("ACCOUNT", ("开通账号", "新账号", "账号开通", "注销账号", "开账号"), ()),
        ("PERMISSION", ("权限", "授权", "共享盘"), ()),
    )
    text = source
    category, warnings = "OTHER", []
    if any(w in text for w in ("故障", "无法", "打不开", "报错", "蓝屏", "连不上", "卡顿", "脱机", "密码", "忘记")):
        category = "INCIDENT"
    else:
        for code, any_words, all_nouns in rules:
            if any(w in text for w in any_words) and (not all_nouns or any(n in text for n in all_nouns)):
                category = code
                break
    priority = "NORMAL"
    if any(w in text for w in ("整个部门", "全公司", "所有人", "无法办公", "宕机")):
        priority, _ = "URGENT", warnings.append("影响范围较大，已建议紧急，请确认")
    elif category == "INCIDENT" and any(w in text for w in ("着急", "尽快", "今天", "客户", "会议", "马上")):
        priority = "HIGH"
    elif any(w in text for w in ("不急", "咨询", "想了解")):
        priority = "LOW"
    title = re.split(r"[，。；;\n]", text.strip())[0][:30] or "IT 服务请求"
    return {"category": category, "priority": priority, "title": title, "description": text.strip()[:2000],
            "evidence": _first(text, 30), "warnings": warnings}


def _procurement(source: str) -> Dict[str, Any]:
    items, warnings = [], []
    for match in re.finditer(r"\b([A-Z][A-Z0-9\-]{2,39})\b\s*(?:共|×|x|\*)?\s*(\d+)\s*(?:件|个|箱|包|台|把)?", source):
        items.append({"sku": match.group(1), "quantity": int(match.group(2)), "evidence": match.group(0).strip()})
    if not items:
        items.append({"sku": None, "quantity": None, "evidence": _first(source)})
        warnings.append("没有识别到 SKU 和数量，请对照产品目录补全")
    return {"items": items[:50], "warnings": warnings}


def _crm(source: str) -> Dict[str, Any]:
    clauses = _clauses(source)
    tasks = []
    for clause in clauses:
        if any(w in clause for w in ("发送", "安排", "跟进", "回访", "约定", "提供", "拜访")):
            due = _dates(clause)
            tasks.append({"title": clause[:100], "due_date": due[-1] if due else None, "evidence": clause[:500]})
    warnings = [f"“{t['title'][:20]}”没有明确日期" for t in tasks if t["due_date"] is None]
    return {"content": " ".join(clauses[:3])[:1000] or source[:1000], "evidence": _first(source, 100),
            "tasks": tasks[:20], "warnings": warnings}


_DUE = re.compile(r"(下下?周[一二三四五六日天]|本周[一二三四五六日天]|这周[一二三四五六日天]|周[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]"
                  r"|下个?月底|月底|\d{1,2}月底|明天|后天|今天|[一二三四五六七八九十\d]+天[后内])")
_NAME = r"([A-Za-z0-9_\-]{3,20}|[一-龥]{2,3})"
_ACT = r"(?:负责|牵头|跟进|完成|提交|整理|准备|输出|梳理|联调|发布|协调)"


def _responsibility(source: str) -> Dict[str, Any]:
    """规则抽取：只摘出原文里点名了人、带动作的句子；讨论、征求意见的句子进 unresolved。人名是否真是员工由系统另行匹配。"""
    clauses = _clauses(source)
    tasks: List[Dict[str, Any]] = []
    decisions: List[Dict[str, str]] = []
    unresolved: List[str] = []
    for clause in clauses:
        if any(w in clause for w in ("讨论", "考虑", "再议", "待定", "是否", "暂不决定")):
            unresolved.append(f"仅讨论、尚未形成决定：{clause[:100]}")
            continue
        owner = re.search(r"(?:^|[，、：:\s由让请])" + _NAME + r"(?=" + _ACT + ")", clause)
        if owner:
            name = owner.group(1)
            helpers = [n for n in re.findall(r"(?:协助|配合|协办)" + _NAME, clause) if n != name]
            reviewer = re.search(r"(?:由|请)?" + _NAME + r"(?:验收|审核|把关)", clause)
            due = _DUE.search(clause)
            deliverable = re.search(r"(?:提交|输出|交付)([^，。；;]{2,30})", clause)
            criteria = re.search(r"验收标准(?:是|为)?([^，。；;]{2,60})", clause)
            urgent = any(w in clause for w in ("紧急", "务必", "尽快"))
            tasks.append({
                "title": (clause.split("，")[0] if len(clause.split("，")[0]) >= 4 else clause)[:80], "responsible_name": name, "collaborator_names": helpers[:5],
                "reviewer_name": reviewer.group(1) if reviewer else None, "due_text": due.group(1) if due else None,
                "due_date": None, "deliverable": deliverable.group(1).strip() if deliverable else None,
                "acceptance_criteria": criteria.group(1).strip() if criteria else None,
                "priority": "URGENT" if urgent else "NORMAL", "depends_on": [], "evidence": clause[:500]})
        elif any(w in clause for w in ("决定", "同意", "通过", "已确定", "确定为")):
            decisions.append({"content": clause[:300], "evidence": clause[:500]})
        elif any(w in clause for w in ("需要", "要", "安排", "跟进", "确定")) and _DUE.search(clause):
            # 有事有期限但没有点名责任人：也列成一项，由系统标成"待补充主责员工"，不替人选
            due = _DUE.search(clause)
            tasks.append({"title": clause[:80], "responsible_name": None, "collaborator_names": [], "reviewer_name": None,
                          "due_text": due.group(1), "due_date": None, "deliverable": None, "acceptance_criteria": None,
                          "priority": "NORMAL", "depends_on": [], "evidence": clause[:500]})
            unresolved.append(f"没有点名责任人：{clause[:100]}")
    kind = ("MEETING" if "会议" in source or "纪要" in source else "CHAT" if "群" in source or "聊天" in source
            else "EMAIL" if "邮件" in source else "NOTICE" if "通知" in source else "OTHER")
    head = next((c for c in clauses if "会议" in c or "纪要" in c or "通知" in c), "")
    title = (head[:30] if head else "工作责任计划") or "工作责任计划"
    return {"title": title, "source_type": kind, "summary": "；".join(d["content"] for d in decisions)[:1000],
            "decisions": decisions[:30], "tasks": tasks[:30], "unresolved": unresolved[:30],
            "warnings": [] if tasks else ["没有识别到点名负责人的行动项"]}


_EXTRACTORS = {"leave": _leave, "expense": _expense, "ticket": _ticket, "procurement": _procurement, "crm": _crm,
               "responsibility": _responsibility}


def _workflow_kind(system_prompt: str) -> Optional[str]:
    from service.workflows import all_workflows
    for workflow in all_workflows():
        if workflow.instructions and workflow.instructions in system_prompt:
            return workflow.id
    return None


def respond(messages: List[Dict[str, str]]) -> Tuple[str, Dict[str, int]]:
    """返回 (回复文本, 用量)。整理请求输出 JSON；其他请求给一句固定说明。"""
    system = next((m["content"] for m in messages if m["role"] == "system"), "") or ""
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "") or ""
    kind = _workflow_kind(system)
    if kind in _EXTRACTORS:
        answer = json.dumps(_EXTRACTORS[kind](user), ensure_ascii=False)
    elif "连接正常" in user:
        answer = "连接正常（离线演示模型）"
    else:
        answer = NOTICE + "要体验完整对话，请在设置里连接真实模型。"
    return answer, {"input_tokens": len(system + user) // 2, "output_tokens": len(answer) // 2,
                    "total_tokens": (len(system + user) + len(answer)) // 2}


# ---------------------------------------------------------------- 接入大模型客户端 / LangChain

from service.llm.base import BaseLLM  # noqa: E402


class OfflineDemoClient(BaseLLM):
    def chat(self, messages, temperature: float = 0.5) -> str:
        answer, self.last_usage = respond(messages)
        return answer

    async def achat(self, messages, temperature: float = 0.5, web_search: bool = False) -> str:
        return self.chat(messages, temperature)

    def stream_chat(self, messages, temperature: float = 0.5):
        yield self.chat(messages, temperature)


def langchain_model():
    """给走 ReAct 引擎的 Agent 用：不发起工具调用，只回固定说明。"""
    from typing import Any
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class OfflineChatModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "offline-demo"

        def bind_tools(self, tools: Any, **kwargs: Any):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
            roles = {"human": "user", "system": "system", "ai": "assistant"}
            plain = [{"role": roles.get(m.type, "user"), "content": str(m.content)} for m in messages]
            answer, _ = respond(plain)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])

    return OfflineChatModel()
