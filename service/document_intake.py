"""文件导入：把 PDF / Word / Excel / 邮件 / 图片 / 文本文件提取成文字，交给 AI 工作成果的"整理材料"输入框。

只提取文字，不落盘保存、不进入知识库：文件写进临时目录，解析完立刻删除；提取出来的文字由用户在输入框里核对、
修改后再整理——模型看到的、保存在工作成果里的、依据校验用的，都是这份文字，所以"原文依据必须逐字出现"的规则不变。

安全与边界：
- 只认白名单扩展名，并核对文件头（改了扩展名的可执行文件、伪装成 PDF 的文本都会被拒绝）；
- docx/xlsx 是压缩包，先检查成员数和解压后总大小（防压缩炸弹）；PDF 最多 100 页；解析最多 60 秒；
- 扫描件/图片需要视觉模型 OCR，会把图片发给模型供应商：按材料密级执行外发策略，受限材料一律不 OCR，
  机密材料只允许受信任模型；没有配置视觉模型时给出明确提示，不悄悄产出空文本；
- 邮件（.eml）只取主题、收发件人、日期和正文（HTML 去标签），附件只列名称不解析；
- 只做成员身份校验：调用者必须是该部门的有效成员（与整理材料同一规则）。
"""
import asyncio
import email
import html
import os
import re
import tempfile
from email import policy
from typing import Any, Dict, List

from service.data_egress_policy import is_model_allowed
from service.exceptions import InvalidInput, PermissionDenied
from utils.logger_handler import get_logger

logger = get_logger("document_intake")

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_ZIP_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
PARSE_TIMEOUT_SECONDS = 60
MAX_TEXT_CHARS = 200000

TEXT_TYPES = {"txt", "md", "csv"}
IMAGE_TYPES = {"png", "jpg", "jpeg"}
SUPPORTED = sorted(TEXT_TYPES | IMAGE_TYPES | {"pdf", "docx", "xlsx", "eml"})
LABELS = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "eml": "邮件", "txt": "文本", "md": "Markdown", "csv": "CSV",
          "png": "图片", "jpg": "图片", "jpeg": "图片"}


def file_type_of(filename: str) -> str:
    ext = os.path.splitext(os.path.basename(filename or ""))[1].lstrip(".").lower()
    if ext not in SUPPORTED:
        raise InvalidInput(f"不支持的文件类型「.{ext or '无'}」，支持：{'、'.join('.' + t for t in SUPPORTED)}")
    return ext


def _check_signature(kind: str, content: bytes) -> None:
    """文件头必须与扩展名一致；文本类拒绝二进制内容。"""
    head = content[:16]
    ok = True
    if kind == "pdf":
        ok = content[:1024].lstrip().startswith(b"%PDF-")
    elif kind in ("docx", "xlsx"):
        ok = head.startswith(b"PK\x03\x04")
    elif kind == "png":
        ok = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif kind in ("jpg", "jpeg"):
        ok = head.startswith(b"\xff\xd8\xff")
    elif kind in TEXT_TYPES | {"eml"}:
        ok = b"\x00" not in content[:4096] and not head.startswith((b"MZ", b"PK\x03\x04", b"%PDF", b"\x7fELF"))
    if not ok:
        raise InvalidInput(f"文件内容与扩展名「.{kind}」不符，请确认文件没有损坏或改过扩展名")


def _check_archive(content: bytes) -> None:
    from service import archive_guard
    try:
        archive_guard.inspect_zip(content, max_members=MAX_ZIP_MEMBERS, max_uncompressed=MAX_UNCOMPRESSED_BYTES)
    except archive_guard.ArchiveRejected as exc:
        raise InvalidInput(str(exc)) from None


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def parse_eml(content: bytes) -> Dict[str, Any]:
    message = email.message_from_bytes(content, policy=policy.default)
    headers = [f"{label}：{message.get(key)}" for label, key in (("主题", "Subject"), ("发件人", "From"), ("收件人", "To"),
                                                                ("抄送", "Cc"), ("日期", "Date")) if message.get(key)]
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body is not None else ""
    if not text.strip():
        rich = message.get_body(preferencelist=("html",))
        text = _strip_html(rich.get_content()) if rich is not None else ""
    names = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
    parts = ["\n".join(headers), text.strip()]
    if names:
        parts.append("【附件（未解析）】" + "、".join(names))
    return {"text": "\n\n".join(p for p in parts if p), "attachments": names}


def _clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _vision_model(user_id: int):
    from models.init_db import SessionLocal
    from service.llm.vision_ocr import resolve_vision_model
    db = SessionLocal()
    try:
        return resolve_vision_model(db, user_id)
    finally:
        db.close()


def _parse_sync(kind: str, content: bytes, user_id: int, allow_ocr: bool) -> Dict[str, Any]:
    """在线程里解析（解析库都是同步、可能很慢的代码）。"""
    warnings: List[str] = []
    if kind == "eml":
        parsed = parse_eml(content)
        return {"text": parsed["text"], "warnings": warnings, "ocr": False,
                "attachments": parsed["attachments"]}
    if kind in TEXT_TYPES:
        return {"text": _decode(content), "warnings": warnings, "ocr": False}
    from models.init_db import SessionLocal
    from service.rag import rag_service
    with tempfile.TemporaryDirectory(prefix="intake_") as folder:
        path = os.path.join(folder, f"upload.{kind}")
        with open(path, "wb") as handle:
            handle.write(content)
        pages = None
        if kind == "pdf":
            from pypdf import PdfReader
            try:
                pages = len(PdfReader(path).pages)
            except Exception:  # noqa: BLE001
                raise InvalidInput("PDF 已损坏或有密码保护，无法读取") from None
            if pages > MAX_PDF_PAGES:
                raise InvalidInput(f"PDF 有 {pages} 页，超过 {MAX_PDF_PAGES} 页上限，请拆分后再导入")
        db = SessionLocal() if allow_ocr else None
        try:
            try:
                text = rag_service.parse_document(path, kind, db=db, user_id=user_id if allow_ocr else None)
            except InvalidInput:
                raise
            except ValueError as exc:   # 图片需要 OCR 但没有视觉模型 / 当前不允许
                raise InvalidInput(str(exc)) from None
            except Exception:  # noqa: BLE001
                logger.warning("文件解析失败", exc_info=True)
                raise InvalidInput("文件无法解析，请确认文件没有损坏或加密") from None
        finally:
            if db is not None:
                db.close()
        ocr = "OCR识别" in text
        if kind == "pdf" and not allow_ocr:
            warnings.append("按材料密级或模型设置，扫描页没有做图片文字识别；没有文字层的页面会被跳过")
        return {"text": text, "warnings": warnings, "ocr": ocr, "pages": pages}


async def extract_text(db, user_id: int, team_id: int, filename: str, content: bytes, sensitivity: str = "internal") -> Dict[str, Any]:
    from service.automation_work_service import authorize
    try:
        await authorize(db, user_id, team_id)
    except PermissionDenied:
        raise
    if sensitivity not in ("internal", "confidential", "restricted"):
        raise InvalidInput("不支持的材料密级")
    if sensitivity == "restricted":
        raise InvalidInput("受限材料禁止交给模型处理，不能导入整理")
    kind = file_type_of(filename)
    if not content:
        raise InvalidInput("文件是空的")
    if len(content) > MAX_BYTES:
        raise InvalidInput(f"文件超过 {MAX_BYTES // 1024 // 1024}MB 上限")
    _check_signature(kind, content)
    if kind in ("docx", "xlsx"):
        _check_archive(content)

    allow_ocr = False
    if kind in IMAGE_TYPES or kind == "pdf":
        model = await asyncio.to_thread(_vision_model, user_id)
        allow_ocr = bool(model) and is_model_allowed(model[0], sensitivity)
        if kind in IMAGE_TYPES and not allow_ocr:
            if model:
                raise InvalidInput("这份材料的密级不允许把图片发给视觉模型识别，请改传文字版文件")
            raise InvalidInput("识别图片文字需要先在「设置 → 模型连接」配置支持视觉的模型（GLM-4V、GPT-4o 或 GPT-4o-mini）")
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_parse_sync, kind, content, user_id, allow_ocr), PARSE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise InvalidInput("文件解析超时，请拆分文件或改传文字版") from None
    text = _clean(result["text"])
    if not text or text == "无文字内容":
        raise InvalidInput("没有从文件里提取到文字：扫描件需要配置视觉模型，或请改传文字版文件")
    warnings = list(result["warnings"])
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        warnings.append("文件很长，只保留了前面的部分文字")
    from service import audit_service
    await audit_service.record_async(user_id, "automation.import_file", resource_type="team", resource_id=team_id,
                                     detail={"type": kind, "size": len(content), "chars": len(text), "ocr": result["ocr"]})
    return {"text": text, "file_name": os.path.basename(filename), "file_type": kind, "label": LABELS[kind], "chars": len(text),
            "pages": result.get("pages"), "ocr": result["ocr"], "warnings": warnings,
            "attachments": result.get("attachments", [])}
