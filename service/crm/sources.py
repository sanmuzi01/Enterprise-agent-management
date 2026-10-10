"""把邮件（.eml / IMAP 取到的原始邮件）和日历（.ics）解析成统一的活动草稿。

- 邮件：主题、正文（HTML 去标签）、时间、发件人 / 收件人 / 抄送；附件走现有的文档解析
  （service/document_intake.py：白名单扩展名 + 文件头校验 + 防压缩炸弹，不做图片 OCR），解析出的文字附在正文后面。
  去重键是 Message-ID（没有时用内容哈希），同一封邮件不管转发几次、同步几次都只生成一条活动。
- 日历：每个 VEVENT 一条会议活动：标题、时间、参会人（邮箱用于关联客户）、会议纪要（DESCRIPTION）、关联文档链接。
  去重键是 UID（重复会议加上 RECURRENCE-ID）。
"""
import email
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Dict, List, Optional

from service.exceptions import InvalidInput
from utils.logger_handler import get_logger

logger = get_logger("crm_sources")

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_CHARS = 20000
MAX_CONTENT_CHARS = 60000


@dataclass
class ActivityDraft:
    activity_type: str
    external_source_id: str
    occurred_at: datetime                   # UTC，不带时区
    title: str
    content: str
    participants: List[Dict[str, str]] = field(default_factory=list)    # [{name, email, role}]
    attachments: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)

    @property
    def emails(self) -> List[str]:
        return [p["email"] for p in self.participants if p.get("email")]


def _utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# ------------------------------------------------------------------ 邮件

def parse_email(raw: bytes, *, own_addresses: Optional[List[str]] = None) -> ActivityDraft:
    from service import document_intake
    if not raw or len(raw) > 25 * 1024 * 1024:
        raise InvalidInput("邮件为空或超过 25MB")
    message = email.message_from_bytes(raw, policy=policy.default)
    subject = str(message.get("Subject") or "（无主题）").strip()
    try:
        occurred = parsedate_to_datetime(str(message.get("Date"))) if message.get("Date") else None
    except (TypeError, ValueError):
        occurred = None
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body is not None else ""
    if not text.strip():
        rich = message.get_body(preferencelist=("html",))
        text = document_intake._strip_html(rich.get_content()) if rich is not None else ""

    own = {a.lower() for a in own_addresses or []}
    participants = []
    for role, header in (("from", "From"), ("to", "To"), ("cc", "Cc")):
        for name, address in getaddresses([str(v) for v in message.get_all(header, [])]):
            address = (address or "").strip().lower()
            if address and address not in own:
                participants.append({"name": name or "", "email": address, "role": role})

    attachment_names, attachment_texts = [], []
    for part in list(message.iter_attachments())[:MAX_ATTACHMENTS]:
        name = part.get_filename()
        if not name:
            continue
        attachment_names.append(name)
        extracted = _attachment_text(name, part.get_payload(decode=True) or b"")
        if extracted:
            attachment_texts.append(f"【附件：{name}】\n{extracted[:MAX_ATTACHMENT_CHARS]}")

    content = document_intake._clean(text)
    if attachment_texts:
        content += "\n\n" + "\n\n".join(attachment_texts)
    message_id = str(message.get("Message-ID") or "").strip().strip("<>")
    source_id = message_id or "sha256:" + hashlib.sha256(raw).hexdigest()
    return ActivityDraft("email", source_id[:255], _utc(occurred), subject[:300], content[:MAX_CONTENT_CHARS],
                         participants, attachment_names)


def _attachment_text(name: str, content: bytes) -> str:
    """附件进入现有文档解析（不做 OCR，不外发）。不支持的类型、损坏的文件只记名称。"""
    from service import document_intake
    try:
        kind = document_intake.file_type_of(name)
        if kind in document_intake.IMAGE_TYPES or kind == "eml" or not content or len(content) > MAX_ATTACHMENT_BYTES:
            return ""
        document_intake._check_signature(kind, content)
        if kind in ("docx", "xlsx"):
            document_intake._check_archive(content)
        return document_intake._clean(document_intake._parse_sync(kind, content, 0, False)["text"])
    except InvalidInput:
        return ""
    except Exception:  # noqa: BLE001 —— 附件解析失败不影响邮件本身入库
        logger.warning(f"邮件附件解析失败：{name}", exc_info=True)
        return ""


# ------------------------------------------------------------------ 日历

def _unfold(text: str) -> List[str]:
    lines: List[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _ics_time(value: str, params: Dict[str, str]) -> Optional[datetime]:
    value = value.strip()
    try:
        if len(value) == 8:
            return datetime.strptime(value, "%Y%m%d")
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        local = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    # 带 TZID 的本地时间：国内日历基本都是 Asia/Shanghai；其他时区用 zoneinfo 换算
    tz = params.get("TZID")
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz) if tz else ZoneInfo("Asia/Shanghai")
    except Exception:  # noqa: BLE001 —— Windows 自定义时区名等
        return local - timedelta(hours=8)
    return local.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)


def parse_calendar(raw: bytes, *, own_addresses: Optional[List[str]] = None) -> List[ActivityDraft]:
    from service import document_intake
    text = document_intake._decode(raw or b"")
    if "BEGIN:VCALENDAR" not in text:
        raise InvalidInput("不是日历文件（.ics）")
    own = {a.lower() for a in own_addresses or []}
    drafts, event = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            event = {"attendees": [], "links": []}
            continue
        if line == "END:VEVENT" and event is not None:
            draft = _event_draft(event, own)
            if draft:
                drafts.append(draft)
            event = None
            continue
        if event is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = dict(p.split("=", 1) for p in raw_params if "=" in p)
        name = name.upper()
        if name in ("SUMMARY", "DESCRIPTION", "LOCATION", "UID", "RECURRENCE-ID", "STATUS"):
            event[name] = _unescape(value)
            if name == "RECURRENCE-ID":
                event["RECURRENCE-ID"] = value
        elif name in ("DTSTART", "DTEND"):
            event[name] = _ics_time(value, params)
        elif name in ("ATTENDEE", "ORGANIZER"):
            address = value.split(":", 1)[-1].strip().lower() if value.lower().startswith("mailto:") else ""
            if address and address not in own:
                event["attendees"].append({"name": params.get("CN", "").strip('"'), "email": address,
                                           "role": "organizer" if name == "ORGANIZER" else "attendee"})
        elif name in ("URL", "ATTACH") and value.startswith(("http://", "https://")):
            event["links"].append(value)
    return drafts


def _event_draft(event: Dict[str, Any], own: set) -> Optional[ActivityDraft]:
    if not event.get("UID") or event.get("STATUS", "").upper() == "CANCELLED":
        return None
    uid = event["UID"] + (f"#{event['RECURRENCE-ID']}" if event.get("RECURRENCE-ID") else "")
    start, end = event.get("DTSTART"), event.get("DTEND")
    lines = []
    if start:
        lines.append(f"时间：{(start + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}"
                     + (f" ～ {(end + timedelta(hours=8)).strftime('%H:%M')}（北京时间）" if end else "（北京时间）"))
    if event.get("LOCATION"):
        lines.append(f"地点：{event['LOCATION']}")
    if event["attendees"]:
        lines.append("参会人：" + "、".join(a["name"] or a["email"] for a in event["attendees"]))
    if event.get("DESCRIPTION"):
        lines.append("会议纪要 / 说明：\n" + event["DESCRIPTION"].strip())
    if event["links"]:
        lines.append("关联文档：" + "\n".join(event["links"]))
    # 同一个 UID 只记一条（会议改期后再导入不会多出一条）
    return ActivityDraft("meeting", ("ics:" + uid)[:255], _utc(start), (event.get("SUMMARY") or "（无标题会议）")[:300],
                         "\n".join(lines)[:MAX_CONTENT_CHARS], event["attendees"], [], event["links"])


def phones_in(text: str) -> List[str]:
    return re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text or "")
