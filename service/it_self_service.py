"""IT 自助解决：员工描述问题 → 推荐知识文章 → 员工明确点“已解决”或“没有解决，创建工单”。

口径（都写死在这里，不靠猜）：
  - 只打开看过文章不算解决；只有员工点了“已解决”（confirmed_solved=1）才计入自助解决率；
  - 点“没有解决，创建工单”的，工单号记在会话上（converted_ticket_id），自助会话和工单能对上；
  - 重新打开：员工点了“已解决”，7 天内又为同一类问题提工单；或者转出的工单被重开——都记为 reopened，
    计入“问题重新打开率”，也会拉低推荐它的文章的质量；
  - 低解决率（推荐 ≥ 5 次、解决率 < 30%）或低评分（评价 ≥ 3 次、有用 < 50%）的文章进入维护清单，
    提醒 IT 部门负责人更新（提醒规则 it_kb_maintenance）。
"""
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from models.init_db import ItArticleFeedback, ItSelfServiceSession
from service import it_service
from service.exceptions import Conflict, InvalidInput, NotFound
from utils.timeutil import utcnow

REOPEN_WINDOW = timedelta(days=7)
MAINTENANCE_MIN_RECOMMENDED = 5
MAINTENANCE_MAX_SOLVE_RATE = 0.30
MAINTENANCE_MIN_RATINGS = 3
MAINTENANCE_MIN_HELPFUL = 0.50


def _articles(row: ItSelfServiceSession) -> List[Dict[str, Any]]:
    return json.loads(row.article_ids_json or "[]")


def payload(row: ItSelfServiceSession) -> Dict[str, Any]:
    return {"id": row.id, "question": row.question, "classification": row.classification,
            "classification_label": it_service.CATEGORY_LABELS.get(row.classification), "articles": _articles(row),
            "confirmed_solved": None if row.confirmed_solved is None else bool(row.confirmed_solved),
            "solved_article_id": row.solved_article_id, "converted_ticket_id": row.converted_ticket_id,
            "reopened": bool(row.reopened), "started_at": row.started_at.isoformat() + "Z",
            "feedback_at": row.feedback_at.isoformat() + "Z" if row.feedback_at else None}


async def start(db, user_id: int, team_id: Optional[int], question: str) -> Dict[str, Any]:
    question = (question or "").strip()
    if len(question) < 2:
        raise InvalidInput("请描述一下遇到的问题")
    suggested = await it_service.suggest_solutions_async(db, user_id, team_id, question[:500])
    articles = [{"id": a.get("id"), "title": a.get("title"), "steps": a.get("steps"), "category": a.get("category")}
                for a in suggested.get("articles") or [] if a.get("id") is not None][:5]
    classification = suggested.get("classification") or {}
    row = ItSelfServiceSession(user_id=user_id, team_id=team_id, question=question[:2000],
                               classification=classification.get("category"),
                               article_ids_json=json.dumps(articles, ensure_ascii=False),
                               suggestion=articles[0]["title"] if articles else None)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {**payload(row), "classification_detail": classification}


async def _own(db, user_id: int, session_id: int) -> ItSelfServiceSession:
    row = (await db.execute(select(ItSelfServiceSession).where(ItSelfServiceSession.id == session_id,
                                                               ItSelfServiceSession.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("自助会话不存在")
    return row


async def _touch(db, row: ItSelfServiceSession, article_id: int, **values) -> None:
    """写文章反馈：一条 INSERT ... ON DUPLICATE KEY UPDATE。“打开文章”和“评价”是前端同时发出的两个请求，
    先查再插会撞唯一约束（浏览器检查时遇到过），所以必须一条语句完成；“看过”的时间只记第一次。"""
    from sqlalchemy import func
    from sqlalchemy.dialects.mysql import insert
    if article_id not in {a["id"] for a in _articles(row)}:
        raise InvalidInput("这篇文章不在推荐列表里")
    now = utcnow()
    stmt = insert(ItArticleFeedback).values(session_id=row.id, article_id=article_id, user_id=row.user_id,
                                            viewed_at=now, **values)
    updates = {"viewed_at": func.coalesce(ItArticleFeedback.viewed_at, stmt.inserted.viewed_at)}
    updates.update({k: stmt.inserted[k] for k in values})
    await db.execute(stmt.on_duplicate_key_update(**updates))


async def view_article(db, user_id: int, session_id: int, article_id: int) -> Dict[str, Any]:
    """记下“看过”。看过不等于解决，不改会话的解决状态。"""
    row = await _own(db, user_id, session_id)
    await _touch(db, row, article_id)
    await db.commit()
    return payload(row)


async def rate_article(db, user_id: int, session_id: int, article_id: int, helpful: bool) -> Dict[str, Any]:
    row = await _own(db, user_id, session_id)
    await _touch(db, row, article_id, helpful=1 if helpful else 0, rated_at=utcnow())
    await db.commit()
    return payload(row)


async def mark_solved(db, user_id: int, session_id: int, article_id: Optional[int] = None) -> Dict[str, Any]:
    """员工明确确认“已解决”。"""
    row = await _own(db, user_id, session_id)
    if row.converted_ticket_id:
        raise Conflict("这个问题已经转成工单了")
    if article_id is not None:
        await _touch(db, row, article_id)
    row.confirmed_solved, row.solved_article_id, row.feedback_at = 1, article_id, utcnow()
    await db.commit()
    return payload(row)


async def convert_to_ticket(db, user_id: int, session_id: int, team_id: int, category: str, priority: Optional[str],
                            title: str, description: str) -> Dict[str, Any]:
    """“没有解决，创建工单”：工单号记到会话上。7 天内点过“已解决”的同类问题，记为重新打开。"""
    row = await _own(db, user_id, session_id)
    if row.converted_ticket_id:
        raise Conflict(f"这个问题已经转成工单 #{row.converted_ticket_id}")
    ticket = await it_service.create_ticket_async(db, user_id, team_id, category, priority, title, description,
                                                  idempotency_key=f"it-self-service-{row.id}")
    row.converted_ticket_id, row.confirmed_solved, row.feedback_at = int(ticket["id"]), 0, utcnow()
    # 推荐的文章和之前“已解决”的那次有重叠：也是同一个问题（文字判断在 create_ticket_async 里已经做过）
    await _mark_recent_solved_reopened(db, user_id, row.classification or category, row.question,
                                       {a["id"] for a in _articles(row)}, exclude=row.id)
    await db.commit()
    return {"session": payload(row), "ticket": ticket}


def _bigrams(text: str) -> set:
    text = "".join(ch for ch in (text or "").lower() if not ch.isspace())
    return {text[i:i + 2] for i in range(len(text) - 1)}


def same_problem(solved: ItSelfServiceSession, text: str, article_ids: Optional[set] = None) -> bool:
    """“同一个问题又来了”：推荐的文章有重叠，或者两次描述明显相似（字的二元组重合 ≥ 30%）。
    只看分类不够——“忘记密码”和“VPN 连不上”都是“故障”，不能算同一个问题（浏览器检查时发现过）。"""
    if article_ids and article_ids & {a["id"] for a in _articles(solved)}:
        return True
    a, b = _bigrams(solved.question), _bigrams(text)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.30


async def _mark_recent_solved_reopened(db, user_id: int, classification: Optional[str], text: str,
                                       article_ids: Optional[set] = None, exclude: Optional[int] = None) -> None:
    since = utcnow() - REOPEN_WINDOW
    query = select(ItSelfServiceSession).where(
        ItSelfServiceSession.user_id == user_id, ItSelfServiceSession.confirmed_solved == 1,
        ItSelfServiceSession.reopened == 0, ItSelfServiceSession.feedback_at >= since)
    if classification:
        query = query.where(ItSelfServiceSession.classification == classification)
    if exclude:
        query = query.where(ItSelfServiceSession.id != exclude)
    for solved in (await db.execute(query)).scalars().all():
        if same_problem(solved, text, article_ids):
            solved.reopened, solved.reopened_at = 1, utcnow()


async def on_ticket_created(db, user_id: int, category: Optional[str], text: str = "") -> None:
    """提了工单（直接提或自助转过来）：7 天内点过“已解决”的同一个问题记为重新打开。"""
    await _mark_recent_solved_reopened(db, user_id, category, text)
    await db.commit()


async def on_ticket_reopened(db, ticket_id: int) -> None:
    """转出的工单被重开：对应的自助会话记为重新打开。"""
    await db.execute(update(ItSelfServiceSession).where(ItSelfServiceSession.converted_ticket_id == ticket_id,
                                                        ItSelfServiceSession.reopened == 0)
                     .values(reopened=1, reopened_at=utcnow()))
    await db.commit()


# ------------------------------------------------------------------ 指标（IT 部门人员看）

def compute_metrics(sessions: List[ItSelfServiceSession], feedback: List[ItArticleFeedback]) -> Dict[str, Any]:
    """纯函数：按会话和文章反馈算指标。"""
    total = len(sessions)
    recommended = [s for s in sessions if _articles(s)]
    solved = [s for s in sessions if s.confirmed_solved == 1]
    converted = [s for s in sessions if s.converted_ticket_id]
    reopened = [s for s in solved if s.reopened] + [s for s in converted if s.reopened]

    def rate(a: int, b: int) -> Optional[float]:
        return round(a / b, 4) if b else None

    articles: Dict[int, Dict[str, Any]] = {}
    for s in sessions:
        for a in _articles(s):
            item = articles.setdefault(a["id"], {"id": a["id"], "title": a.get("title"), "recommended": 0, "viewed": 0,
                                                 "solved": 0, "reopened": 0, "helpful": 0, "unhelpful": 0})
            item["recommended"] += 1
            if s.confirmed_solved == 1 and (s.solved_article_id in (None, a["id"])):
                item["solved"] += 1
                item["reopened"] += int(bool(s.reopened))
    for fb in feedback:
        item = articles.get(fb.article_id)
        if item is None:
            continue
        item["viewed"] += int(fb.viewed_at is not None)
        if fb.helpful == 1:
            item["helpful"] += 1
        elif fb.helpful == 0:
            item["unhelpful"] += 1
    maintenance = []
    for item in articles.values():
        # 重新打开的不算真正解决
        effective = item["solved"] - item["reopened"]
        item["solve_rate"] = rate(effective, item["recommended"])
        ratings = item["helpful"] + item["unhelpful"]
        item["helpful_rate"] = rate(item["helpful"], ratings)
        reasons = []
        if item["recommended"] >= MAINTENANCE_MIN_RECOMMENDED and (item["solve_rate"] or 0) < MAINTENANCE_MAX_SOLVE_RATE:
            reasons.append(f"推荐 {item['recommended']} 次，只解决了 {effective} 次")
        if ratings >= MAINTENANCE_MIN_RATINGS and (item["helpful_rate"] or 0) < MAINTENANCE_MIN_HELPFUL:
            reasons.append(f"{ratings} 个评价里只有 {item['helpful']} 个说有用")
        if reasons:
            maintenance.append({**item, "reasons": reasons})
    return {
        "sessions": total, "recommendations": sum(len(_articles(s)) for s in sessions),
        "sessions_with_recommendation": len(recommended), "confirmed_solved": len(solved),
        "converted_to_ticket": len(converted), "no_feedback": sum(1 for s in sessions if s.confirmed_solved is None),
        "self_solve_rate": rate(len(solved) - sum(1 for s in solved if s.reopened), len(recommended)),
        "reopen_rate": rate(len(reopened), len(solved) + len(converted)),
        "articles": sorted(articles.values(), key=lambda a: -a["recommended"]),
        "maintenance": sorted(maintenance, key=lambda a: (a["solve_rate"] or 0)),
    }


async def metrics(db, user_id: int, team_id: int, days: int = 30) -> Dict[str, Any]:
    ctx = await it_service._desk(db, user_id, team_id)          # 只有 IT 部门人员 / 企业管理员能看
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    sessions = list((await db.execute(select(ItSelfServiceSession).where(
        ItSelfServiceSession.started_at >= since, ItSelfServiceSession.team_id.in_(ctx["scope"])))).scalars().all())
    ids = [s.id for s in sessions]
    feedback = list((await db.execute(select(ItArticleFeedback).where(ItArticleFeedback.session_id.in_(ids))))
                    .scalars().all()) if ids else []
    return {"days": days, **compute_metrics(sessions, feedback)}


async def maintenance_list(db, team_ids: List[int], days: int = 90) -> List[Dict[str, Any]]:
    since = utcnow() - timedelta(days=days)
    sessions = list((await db.execute(select(ItSelfServiceSession).where(
        ItSelfServiceSession.started_at >= since, ItSelfServiceSession.team_id.in_(team_ids)))).scalars().all())
    ids = [s.id for s in sessions]
    feedback = list((await db.execute(select(ItArticleFeedback).where(ItArticleFeedback.session_id.in_(ids))))
                    .scalars().all()) if ids else []
    return compute_metrics(sessions, feedback)["maintenance"]
