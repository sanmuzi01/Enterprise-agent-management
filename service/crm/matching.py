"""活动 → 客户的自动关联。

优先级（从强到弱，命中强的就不再看弱的）：
  明确指定的客户 → 联系人邮箱完全一致 → 手机号完全一致 → 销售之前手动指定过的对应关系（别名）
  → 公司邮箱域名 → 公司名称 / 别名出现在标题或正文里 → 模糊匹配

置信度：≥ 0.90 自动关联；0.60～0.90 列出候选让销售选；< 0.60 不关联。
模糊匹配的置信度封顶 0.85——永远不会自动落库，只会作为候选。
"""
import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

AUTO = 0.90
ASK = 0.60
FUZZY_CAP = 0.85

# 公共邮箱的域名不能代表公司
PUBLIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "icloud.com",
    "me.com", "qq.com", "foxmail.com", "163.com", "126.com", "yeah.net", "sina.com", "sina.cn", "sohu.com",
    "aliyun.com", "139.com", "189.cn", "wo.cn", "tom.com", "proton.me", "protonmail.com",
}
# 只去掉一个公司后缀（“科技”“物流”这类是名字本身的一部分，不能去）
_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团", "公司", "co.,ltd.", "co.,ltd", "ltd.", "ltd",
             "inc.", "inc", "corp.", "corp", "llc")


@dataclass
class Directory:
    """一个部门能看到的客户、联系人和别名（只含本部门的数据）。"""
    customers: List[Dict[str, Any]]                     # [{id, name}]
    contacts: List[Dict[str, Any]] = field(default_factory=list)   # [{customerId, name, email, phone}]
    aliases: List[Tuple[str, str, int]] = field(default_factory=list)   # [(alias_type, alias_value, customer_id)]
    internal_domains: set = field(default_factory=set)   # 本公司的邮箱域名：这些地址是同事，不代表客户

    def is_internal(self, email: str) -> bool:
        return domain_of(email) in self.internal_domains

    def name_of(self, customer_id: int) -> str:
        return next((c["name"] for c in self.customers if int(c["id"]) == int(customer_id)), f"客户 {customer_id}")

    def has(self, customer_id: int) -> bool:
        return any(int(c["id"]) == int(customer_id) for c in self.customers)


@dataclass
class MatchResult:
    customer_id: Optional[int]
    confidence: float
    method: str
    candidates: List[Dict[str, Any]] = field(default_factory=list)   # [{customer_id, name, confidence, method}]

    @property
    def status(self) -> str:
        if self.method == "explicit":
            return "explicit"
        if self.customer_id is not None and self.confidence >= AUTO:
            return "auto"
        return "pending" if self.candidates else "unmatched"


def normalize_email(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value or "")
    return match.group(0).lower() if match else ""


def normalize_phone(value: str) -> str:
    digits = "".join(c for c in (value or "") if c.isdigit())
    return digits[-11:] if len(digits) >= 11 else (digits if len(digits) >= 7 else "")


def normalize_name(value: str) -> str:
    text = re.sub(r"[\s（）()·.,，、\-]", "", (value or "").lower())
    for suffix in _SUFFIXES:
        suffix = re.sub(r"[\s.,]", "", suffix)
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            return text[: -len(suffix)]
    return text


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def match(directory: Directory, *, explicit_customer_id: Optional[int] = None, emails: Iterable[str] = (),
          phones: Iterable[str] = (), text: str = "") -> MatchResult:
    if explicit_customer_id is not None:
        if directory.has(explicit_customer_id):
            return MatchResult(int(explicit_customer_id), 1.0, "explicit")
        return MatchResult(None, 0.0, "explicit_not_found")

    emails = {e for e in (normalize_email(x) for x in emails) if e and not directory.is_internal(e)}
    phones = {p for p in (normalize_phone(x) for x in phones) if p}
    alias = {(t, v): cid for t, v, cid in directory.aliases}

    def single(hits: Dict[int, str], confidence: float, method: str) -> Optional[MatchResult]:
        if len(hits) == 1:
            cid = next(iter(hits))
            return MatchResult(cid, confidence, method)
        if len(hits) > 1:      # 同一封邮件里有几个客户的人：不自动选，让销售选
            return MatchResult(None, 0.0, method + "_ambiguous", [
                {"customer_id": cid, "name": directory.name_of(cid), "confidence": 0.8, "method": method} for cid in hits])
        return None

    contact_emails = {normalize_email(c.get("email") or ""): int(c["customerId"]) for c in directory.contacts if c.get("email")}
    hits = {contact_emails[e]: e for e in emails if e in contact_emails}
    hits.update({alias[("email", e)]: e for e in emails if ("email", e) in alias})
    found = single(hits, 0.98, "contact_email")
    if found:
        return found

    contact_phones = {normalize_phone(c.get("phone") or ""): int(c["customerId"]) for c in directory.contacts if c.get("phone")}
    contact_phones.pop("", None)
    hits = {contact_phones[p]: p for p in phones if p in contact_phones}
    hits.update({alias[("phone", p)]: p for p in phones if ("phone", p) in alias})
    found = single(hits, 0.95, "contact_phone")
    if found:
        return found

    company_domains: Dict[str, set] = {}
    for c in directory.contacts:
        d = domain_of(normalize_email(c.get("email") or ""))
        if d and d not in PUBLIC_DOMAINS:
            company_domains.setdefault(d, set()).add(int(c["customerId"]))
    for (t, v), cid in alias.items():
        if t == "domain":
            company_domains[v] = {cid}
    hits = {}
    for e in emails:
        owners = company_domains.get(domain_of(e)) or set()
        if len(owners) == 1:
            hits[next(iter(owners))] = domain_of(e)
    found = single(hits, 0.92, "email_domain")
    if found:
        return found

    plain = normalize_name(text)
    names: Dict[int, List[str]] = {}
    for c in directory.customers:
        names.setdefault(int(c["id"]), []).append(normalize_name(c["name"]))
    for (t, v), cid in alias.items():
        if t == "name":
            names.setdefault(cid, []).append(normalize_name(v))
    hits = {cid: n for cid, variants in names.items() for n in variants if len(n) >= 2 and n in plain}
    found = single(hits, 0.90, "company_name")
    if found:
        return found

    # 模糊匹配：只作为候选（封顶 0.85，不会自动关联）
    candidates = []
    plain = plain[:300]          # 只在开头一段里找相近的名字（标题一般放在最前面），长邮件不逐字比对
    if plain:
        for cid, variants in names.items():
            best = max((_similarity(n, plain) for n in variants if n), default=0.0)
            score = round(min(FUZZY_CAP, best), 2)
            if score >= ASK:
                candidates.append({"customer_id": cid, "name": directory.name_of(cid), "confidence": score, "method": "fuzzy"})
    candidates.sort(key=lambda c: -c["confidence"])
    return MatchResult(None, candidates[0]["confidence"] if candidates else 0.0, "fuzzy" if candidates else "none",
                       candidates[:5])


def _similarity(name: str, text: str) -> float:
    """客户名和文本里最像的一段的相似度（文本里出现了相近的公司名，如“华星科技” vs “华星科枝”）。"""
    if not name or not text:
        return 0.0
    n = len(name)
    best = 0.0
    for size in {max(2, n - 1), n, n + 1}:
        for i in range(0, max(1, len(text) - size + 1)):
            window = text[i:i + size]
            if window[0] not in name and window[-1] not in name:
                continue
            ratio = difflib.SequenceMatcher(None, name, text[i:i + size]).ratio()
            if ratio > best:
                best = ratio
    return best
