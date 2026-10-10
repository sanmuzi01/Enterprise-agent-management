"""发票文字 → 结构化字段 + 每个字段的置信度 + 校验结果（纯函数，方便测试）。

置信度不是模型自己报的，而是按“怎么得到这个值、校验过没过”算的：
  - 文字版 PDF 里按标签（“发票号码：”“价税合计（小写）”……）直接取到：0.95
  - 图片 / 扫描件经过 OCR 后按标签取到：0.80（OCR 会认错字，必须人工核对）
  - 只能靠位置推断（比如没有“购买方 / 销售方”标记时按出现顺序）：0.70
  - 格式校验不过（号码位数、税号校验码）、金额对不上：降到 0.40
低于 REVIEW_THRESHOLD 的字段，员工必须逐项确认后才能用于报销。
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

REVIEW_THRESHOLD = 0.90
FIELDS = ("invoice_type", "invoice_code", "invoice_number", "issued_at", "seller_name", "seller_tax_id",
          "buyer_name", "buyer_tax_id", "amount_without_tax", "tax_amount", "total_amount", "currency")
FIELD_LABELS = {"invoice_type": "发票类型", "invoice_code": "发票代码", "invoice_number": "发票号码", "issued_at": "开票日期",
                "seller_name": "销售方名称", "seller_tax_id": "销售方税号", "buyer_name": "购买方名称",
                "buyer_tax_id": "购买方税号", "amount_without_tax": "金额（不含税）", "tax_amount": "税额",
                "total_amount": "价税合计", "currency": "币种"}
REQUIRED = ("invoice_number", "issued_at", "seller_name", "total_amount")

TYPES = ("电子发票（增值税专用发票）", "电子发票（普通发票）", "增值税电子专用发票", "增值税电子普通发票",
         "增值税专用发票", "增值税普通发票", "机动车销售统一发票", "通行费电子票据", "航空运输电子客票行程单",
         "铁路电子客票")
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
MONEY = r"[¥￥]?\s*(-?[\d,]+\.\d{2})"


def _money(value: Optional[str]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except InvalidOperation:
        return None


def uscc_valid(code: str) -> bool:
    """统一社会信用代码（18 位）校验码，GB 32100-2015。"""
    code = (code or "").upper()
    if len(code) != 18 or any(c not in _USCC_CHARS for c in code):
        return False
    total = sum(_USCC_CHARS.index(c) * w for c, w in zip(code[:17], _USCC_WEIGHTS))
    return _USCC_CHARS[(31 - total % 31) % 31] == code[17]


def tax_id_valid(code: str) -> bool:
    code = (code or "").upper()
    if len(code) == 18:
        return uscc_valid(code)
    return bool(re.fullmatch(r"[0-9A-Z]{15}|[0-9A-Z]{17}|[0-9A-Z]{20}", code))


def number_valid(number: str, code: Optional[str]) -> bool:
    """全电发票：20 位号码、没有发票代码；传统发票：8 位号码 + 10 / 12 位代码。"""
    number = number or ""
    if re.fullmatch(r"\d{20}", number):
        return not code
    return bool(re.fullmatch(r"\d{8}", number)) and bool(re.fullmatch(r"\d{10}|\d{12}", code or ""))


def _labelled(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _parties(text: str) -> Dict[str, Tuple[Optional[str], float]]:
    """名称 / 纳税人识别号按离它最近的“购买方 / 销售方”标记归属；没有标记时按出现顺序（先买方后卖方）推断。"""
    names = [(m.start(), m.group(1).strip()) for m in re.finditer(r"名\s*称\s*[:：]\s*([^\n:：]{2,60}?)(?=\s{2,}|\n|纳税人|统一社会|$)", text)]
    ids = [(m.start(), m.group(1)) for m in re.finditer(r"(?:纳税人识别号|统一社会信用代码)[/／纳税人识别号]*\s*[:：]\s*([0-9A-Za-z]{15,20})", text)]
    markers = sorted([(m.start(), "buyer") for m in re.finditer(r"购\s*买\s*方|购方|买\s*方", text)]
                     + [(m.start(), "seller") for m in re.finditer(r"销\s*售\s*方|销方|卖\s*方", text)])
    out: Dict[str, Tuple[Optional[str], float]] = {}

    def owner(pos: int) -> Optional[str]:
        before = [kind for at, kind in markers if at <= pos]
        return before[-1] if before else None

    for kind_key, items in (("name", names), ("tax_id", ids)):
        assigned: Dict[str, str] = {}
        if markers:
            for pos, value in items:
                party = owner(pos)
                if party and party not in assigned:
                    assigned[party] = value
            confidence = 0.95
        if not markers or len(assigned) < min(2, len(items)):
            assigned = dict(zip(("buyer", "seller"), [v for _, v in items]))
            confidence = 0.70
        for party in ("buyer", "seller"):
            out[f"{party}_{kind_key}"] = (assigned.get(party), confidence if assigned.get(party) else 0.0)
    return out


def parse(text: str, *, from_ocr: bool = False) -> Dict[str, Any]:
    """返回 {"fields": {字段: 值}, "confidence": {字段: 置信度}, "reference": 识别依据}。"""
    text = (text or "").replace("　", " ")
    base = 0.80 if from_ocr else 0.95
    fields: Dict[str, Optional[str]] = {f: None for f in FIELDS}
    conf: Dict[str, float] = {f: 0.0 for f in FIELDS}

    def put(field: str, value: Optional[str], confidence: float) -> None:
        if value:
            fields[field], conf[field] = value, round(min(confidence, base), 2)

    put("invoice_type", next((t for t in TYPES if t in text.replace(" ", "")), None), base)
    put("invoice_code", _labelled(text, r"发票代码\s*[:：]?\s*(\d{10,12})"), base)
    put("invoice_number", _labelled(text, r"发票号码\s*[:：]?\s*(\d{8,20})"), base)
    day = re.search(r"开票日期\s*[:：]?\s*(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})", text)
    if day:
        put("issued_at", f"{int(day.group(1)):04d}-{int(day.group(2)):02d}-{int(day.group(3)):02d}", base)
    for key, (value, c) in _parties(text).items():
        put(key, value.upper() if value and key.endswith("tax_id") else value, min(c, base))

    total = _labelled(text, r"[（(]\s*小\s*写\s*[)）]\s*" + MONEY)
    put("total_amount", total and str(_money(total)), base)
    sums = re.search(r"合\s*计\s*" + MONEY + r"\s*" + MONEY, text)
    if sums:
        put("amount_without_tax", str(_money(sums.group(1))), base)
        put("tax_amount", str(_money(sums.group(2))), base)
    if fields["total_amount"] is None and fields["amount_without_tax"] and fields["tax_amount"]:
        derived = _money(fields["amount_without_tax"]) + _money(fields["tax_amount"])
        put("total_amount", str(derived), 0.70)
    if any(fields[f] for f in ("total_amount", "amount_without_tax")):
        put("currency", "CNY", base)
    reference = "\n".join(line.strip() for line in text.splitlines() if line.strip())[:1500]
    return {"fields": fields, "confidence": conf, "reference": reference}


def validate(fields: Dict[str, Any], confidence: Dict[str, float], *, today: date, company_name: Optional[str] = None,
             known_seller: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, float], List[Dict[str, str]]]:
    """格式与一致性校验：不通过的字段降低置信度，并给出说明。返回 (新的置信度, 校验结果)。"""
    conf = dict(confidence)
    checks: List[Dict[str, str]] = []

    def flag(level: str, code: str, msg: str, *lower: str) -> None:
        checks.append({"level": level, "code": code, "text": msg})
        for f in lower:
            conf[f] = min(conf.get(f, 0.0), 0.40)

    for f in REQUIRED:
        if not fields.get(f):
            flag("error", "MISSING_FIELD", f"没有识别到{FIELD_LABELS[f]}，请手工填写", f)
    if fields.get("invoice_number") and not number_valid(fields["invoice_number"], fields.get("invoice_code")):
        flag("error", "INVOICE_NUMBER_FORMAT",
             "发票号码格式不对：全电发票是 20 位号码（没有发票代码），传统发票是 8 位号码加 10 / 12 位发票代码",
             "invoice_number", "invoice_code")
    for party in ("seller", "buyer"):
        tax_id = fields.get(f"{party}_tax_id")
        if tax_id and not tax_id_valid(tax_id):
            flag("warning", "TAX_ID_FORMAT", f"{FIELD_LABELS[party + '_tax_id']}「{tax_id}」位数或校验码不对", f"{party}_tax_id")
    net, tax, total = (_money(fields.get(k)) for k in ("amount_without_tax", "tax_amount", "total_amount"))
    if total is not None and total <= 0:
        flag("error", "AMOUNT_INVALID", "价税合计必须大于 0", "total_amount")
    if None not in (net, tax, total) and abs(net + tax - total) > Decimal("0.01"):
        flag("error", "AMOUNT_MISMATCH", f"金额 {net} + 税额 {tax} ≠ 价税合计 {total}，请核对",
             "amount_without_tax", "tax_amount", "total_amount")
    if fields.get("issued_at"):
        try:
            issued = date.fromisoformat(fields["issued_at"])
        except ValueError:
            flag("error", "DATE_FORMAT", "开票日期格式不对", "issued_at")
        else:
            if issued > today:
                flag("error", "DATE_IN_FUTURE", "开票日期晚于今天", "issued_at")
            elif issued < today - timedelta(days=365):
                flag("warning", "DATE_TOO_OLD", "发票开具已超过一年，请确认是否还能报销")
    if company_name and fields.get("buyer_name"):
        def norm(s: str) -> str:
            return re.sub(r"[\s（）()]", "", s)
        if norm(company_name) not in norm(fields["buyer_name"]) and norm(fields["buyer_name"]) not in norm(company_name):
            flag("warning", "BUYER_MISMATCH", f"购买方「{fields['buyer_name']}」不是本公司（{company_name}），抬头开错的发票一般不能报销")
    if known_seller:
        if known_seller.get("name") and known_seller.get("name") != fields.get("seller_name"):
            flag("warning", "SELLER_NAME_CHANGED",
                 f"这个税号以前开票的销售方是「{known_seller['name']}」，这次是「{fields.get('seller_name')}」，请核对", "seller_name")
        else:
            checks.append({"level": "info", "code": "KNOWN_SUPPLIER", "text": f"已知供应商：{known_seller.get('name')}"
                                                                          f"（之前报销过 {known_seller.get('count', 1)} 次）"})
    return conf, checks


def needs_review(confidence: Dict[str, float], fields: Dict[str, Any]) -> List[str]:
    """需要员工逐项确认的字段：有值但置信度低于阈值，或必填字段没识别到。"""
    return [f for f in FIELDS if (fields.get(f) and confidence.get(f, 0) < REVIEW_THRESHOLD)
            or (f in REQUIRED and not fields.get(f))]
