import html
import json
import ipaddress
import os
import re
import socket
import threading
import time
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests import RequestException


class CrawlerError(ValueError):
    """网页抓取失败或 URL 不允许访问。"""


class _ReadableHTMLParser(HTMLParser):
    """把 HTML 转成适合入库检索的纯文本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.meta_parts: List[str] = []
        self.priority_parts: List[str] = []
        self.body_parts: List[str] = []
        self._tag_stack: List[str] = []
        self._skip_depth = 0
        self._priority_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attrs_map = {str(key).lower(): value for key, value in attrs if key}
        if tag == "meta":
            meta_name = (attrs_map.get("name") or attrs_map.get("property") or "").strip().lower()
            if meta_name in {"description", "og:description", "twitter:description"}:
                content = (attrs_map.get("content") or "").strip()
                if content:
                    self.meta_parts.append(content)
            return
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        if tag in {"main", "article"}:
            self._priority_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.body_parts.append("\n")
            if self._priority_depth:
                self.priority_parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.body_parts.append("\n")
            if self._priority_depth:
                self.priority_parts.append("\n")
        if tag in {"main", "article"} and self._priority_depth:
            self._priority_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._tag_stack and self._tag_stack[-1] == "title":
            self.title_parts.append(text)
            return
        self.body_parts.append(text)
        self.body_parts.append(" ")
        if self._priority_depth:
            self.priority_parts.append(text)
            self.priority_parts.append(" ")

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self.title_parts))[:200]

    @property
    def meta_text(self) -> str:
        return _normalize_text("\n\n".join(self.meta_parts))

    @property
    def priority_text(self) -> str:
        return _normalize_text("".join(self.priority_parts))

    @property
    def text(self) -> str:
        return _normalize_text("".join(self.body_parts))


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，配置错误时使用默认值。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------- DNS 固定（防重绑定）
# 校验时解析一次，真正建连接时 HTTP 库又会解析一次：攻击者控制的域名可以让第二次返回 127.0.0.1（DNS rebinding）。
# 校验通过后把解析结果在固定表里留 _PIN_SECONDS 秒，包装后的 getaddrinfo 对这些主机直接返回校验过的地址。
# 固定表只含“已通过校验的地址”，所以跨线程共享是安全的（httpx/asyncio 的解析在线程池里跑，线程局部变量够不着）。
_PIN_SECONDS = 30.0
_pins: Dict[str, Tuple[float, List[str]]] = {}
_pin_lock = threading.Lock()
_orig_getaddrinfo = getattr(socket.getaddrinfo, "_original", socket.getaddrinfo)


def _pin_key(host) -> str:
    return host.strip().lower().rstrip(".") if isinstance(host, str) else ""


def _pin(hostname: str, addresses) -> None:
    now = time.monotonic()
    with _pin_lock:
        for key in [k for k, (expires, _) in _pins.items() if expires <= now]:
            del _pins[key]
        _pins[_pin_key(hostname)] = (now + _PIN_SECONDS, sorted(set(addresses)))


def _pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    entry = _pins.get(_pin_key(host))
    if entry and entry[0] > time.monotonic():
        try:
            port_number = int(port) if port not in (None, "") else 0
        except (TypeError, ValueError):
            port_number = None
        if port_number is not None:
            results = []
            for address in entry[1]:
                v6 = ":" in address
                if family not in (0, socket.AF_INET6 if v6 else socket.AF_INET):
                    continue
                results.append((socket.AF_INET6 if v6 else socket.AF_INET, type or socket.SOCK_STREAM, proto or 0, "",
                                (address, port_number, 0, 0) if v6 else (address, port_number)))
            if results:
                return results
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


if getattr(socket.getaddrinfo, "_pinned", False) is not True:
    _pinned_getaddrinfo._pinned = True
    _pinned_getaddrinfo._original = _orig_getaddrinfo
    socket.getaddrinfo = _pinned_getaddrinfo


def _normalize_text(text: str) -> str:
    """压缩网页里的空白字符，让入库内容更适合切块和检索。"""

    text = html.unescape(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")     # 本地代理（Clash 等 fake-ip 模式）把外部域名解析到这里


def _embedded_ipv4(ip):
    """IPv6 里嵌入的 IPv4（::ffff:127.0.0.1、NAT64 64:ff9b::7f00:1）：必须按嵌入的 IPv4 判断，不能因为是 IPv6 就放过。"""
    if ip.version != 6:
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(ip.packed[-4:])
    return None


def _is_blocked_ip(address: str) -> bool:
    """判断 IP 是否属于生产爬虫不应访问的地址。

    只放行“全球可路由”的地址：内网、回环、链路本地（含 169.254.169.254 云元数据）、保留、未指定、多播，
    以及共享地址空间 100.64.0.0/10（阿里云元数据 100.100.100.200 就在这里，`is_private` 不包含它）、
    文档/基准测试网段都会被拒绝。
    """

    ip = ipaddress.ip_address(address)
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        ip = embedded
    return (not ip.is_global) or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local


def _legacy_ipv4(hostname: str):
    """inet_aton 风格的 IPv4 写法：2130706433、0x7f000001、0177.0.0.1、127.1 ……

    `ipaddress` 不认这些，但操作系统的解析器和大多数 HTTP 库认，所以攻击者会用它们绕过“字符串是不是内网 IP”的判断。
    规则：1–4 段，每段十进制 / 0x 十六进制 / 0 开头八进制，最后一段补满剩余字节。解析不了返回 None。
    """

    parts = hostname.split(".")
    if not 1 <= len(parts) <= 4 or not all(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", part) for part in parts):
        return None
    values = []
    for part in parts:
        try:
            values.append(int(part, 16) if part[:2].lower() == "0x" else int(part, 8) if len(part) > 1 and part[0] == "0" else int(part, 10))
        except ValueError:
            return None
    *head, last = values
    if any(v > 255 for v in head) or last >= 256 ** (4 - len(head)):
        return None
    number = 0
    for v in head:
        number = (number << 8) | v
    number = (number << (8 * (4 - len(head)))) | last
    return str(ipaddress.IPv4Address(number))


def _literal_ip(hostname: str):
    """主机名本身是不是 IP（含上面的各种数字写法），是则返回规范形式。"""

    try:
        return str(ipaddress.ip_address(hostname))
    except ValueError:
        return _legacy_ipv4(hostname)


def _is_development() -> bool:
    """判断当前是否为开发环境。"""

    return os.getenv("APP_ENV", "development").strip().lower() not in {"production", "prod"}


def _allow_private_dns_resolution() -> bool:
    """是否允许域名经本机代理解析到私有地址。

    有些 Windows 本地代理会把所有外部域名解析到 198.18.0.0/15 这类私有网段。
    生产环境不能默认放开；开发环境允许该行为，便于本地调试爬虫。
    """

    return _env_bool("CRAWLER_ALLOW_PRIVATE_DNS", _is_development())


def _browser_fallback_enabled() -> bool:
    """是否启用浏览器渲染兜底抓取。

    默认关闭，避免每次抓网页都启动浏览器。生产环境遇到强 JS 渲染网站时，
    安装 Playwright Chromium 后设置 CRAWLER_BROWSER_FALLBACK=1 即可启用。
    """

    return _env_bool("CRAWLER_BROWSER_FALLBACK", False)


def _is_blocked_hostname(hostname: str) -> bool:
    """拦截明显不能作为爬虫目标的主机名。"""

    name = hostname.strip().lower().rstrip(".")
    return (
        name == "localhost"
        or name.endswith(".localhost")
        or name in {"metadata.google.internal"}
    )


def _resolve_host(hostname: str) -> Iterable[str]:
    """解析域名对应的所有 IP，避免只检查第一个地址造成 SSRF 漏洞。"""

    try:
        resolver = socket.getaddrinfo
        if getattr(resolver, "_pinned", False) is True:      # MagicMock 的任意属性都是真值，必须严格比较
            resolver = _orig_getaddrinfo        # 校验必须是一次新的解析，不能读到固定表（被测试替换成假解析器时照常使用它）
        infos = resolver(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CrawlerError("域名解析失败，无法抓取该 URL") from exc
    return {item[4][0] for item in infos}


def validate_crawl_url(url: str) -> str:
    """校验 URL 是否允许被爬虫访问。

    生产环境默认禁止访问内网、localhost、链路本地地址和保留地址，防止 SSRF。
    本地调试如确实需要访问内网，可临时设置 CRAWLER_ALLOW_PRIVATE_NETWORK=1。
    开发环境额外容忍的只有“域名被本地代理解析到 198.18.0.0/15”这一种情况，字面 IP 和其他内网解析结果照常拒绝。
    校验通过的域名解析结果会被固定一小段时间（见 _pin），建连接时不再重新解析，防 DNS 重绑定。
    """

    raw_url = (url or "").strip()
    if raw_url and "://" not in raw_url:
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise CrawlerError("只支持 http/https URL")
    if not parsed.hostname:
        raise CrawlerError("URL 缺少域名")
    if parsed.username or parsed.password or "@" in parsed.netloc or "\\" in parsed.netloc:
        raise CrawlerError("URL 不允许包含用户名或密码")

    allow_private = _env_bool("CRAWLER_ALLOW_PRIVATE_NETWORK", False)
    hostname = parsed.hostname
    if _is_blocked_hostname(hostname) and not allow_private:
        raise CrawlerError("不允许抓取 localhost 或云元数据地址")
    literal = _literal_ip(hostname)
    if literal is not None:
        addresses, is_literal_ip = [literal], True
    else:
        addresses, is_literal_ip = list(_resolve_host(hostname)), False

    blocked_addresses = [address for address in addresses if _is_blocked_ip(address)]
    if not allow_private and blocked_addresses:
        only_fake_ip = all(ipaddress.ip_address(a) in _FAKE_IP_NET for a in blocked_addresses)
        if not is_literal_ip and only_fake_ip and _allow_private_dns_resolution():
            _pin(hostname, addresses)
            return parsed.geturl()
        raise CrawlerError("不允许抓取内网、localhost 或保留地址")
    if not is_literal_ip and not allow_private:
        _pin(hostname, addresses)
    return parsed.geturl()


def _safe_file_name(url: str, title: str = "") -> str:
    """根据网页标题或 URL 生成安全的 Markdown 文件名。"""

    parsed = urlparse(url)
    raw = title or f"{parsed.netloc}{parsed.path}" or "web-page"
    raw = re.sub(r"https?://", "", raw, flags=re.I)
    raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", raw).strip("-._")
    raw = raw[:80] or "web-page"
    return f"网页-{raw}.md"


def _decode_response(response: requests.Response, content: bytes) -> str:
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    return content.decode(encoding, errors="replace")


def _extract_jsonld_text(raw_text: str) -> str:
    """从 JSON-LD 里提取文章正文、摘要和标题，兼容不少前端渲染型页面。"""

    matches = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        raw_text,
        flags=re.I | re.S,
    )
    parts: List[str] = []

    def collect(value):
        if isinstance(value, dict):
            for key in ("headline", "name", "description", "articleBody", "text"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
            graph = value.get("@graph")
            if isinstance(graph, list):
                for child in graph:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for match in matches:
        payload = html.unescape(match).strip()
        try:
            collect(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return _normalize_text("\n\n".join(parts))


def _select_best_text(*candidates: str) -> str:
    """从多个正文候选里选出最适合入库的一份。"""

    normalized = [_normalize_text(item) for item in candidates if _normalize_text(item)]
    if not normalized:
        return ""

    min_length = _env_int("CRAWLER_MIN_TEXT_LENGTH", 50)
    for item in normalized:
        if len(item) >= min_length:
            return item
    return max(normalized, key=len)


def _download(url: str) -> requests.Response:
    """带重定向校验和大小限制下载网页。"""

    timeout = float(os.getenv("CRAWLER_TIMEOUT_SECONDS", "10"))
    max_redirects = _env_int("CRAWLER_MAX_REDIRECTS", 5)
    max_bytes = _env_int("CRAWLER_MAX_BYTES", 2 * 1024 * 1024)
    user_agent = os.getenv(
        "CRAWLER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AgentPlatformCrawler/1.0",
    )

    session = requests.Session()
    current_url = validate_crawl_url(url)
    for _ in range(max_redirects + 1):
        try:
            response = session.get(
                current_url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                },
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        except RequestException as exc:
            raise CrawlerError(f"网页连接失败或超时: {current_url}") from exc
        if response.is_redirect:
            location = response.headers.get("Location")
            if not location:
                raise CrawlerError("网页重定向缺少 Location")
            current_url = validate_crawl_url(urljoin(current_url, location))
            continue

        try:
            response.raise_for_status()
        except RequestException as exc:
            raise CrawlerError(f"网页返回错误状态码: {response.status_code}") from exc
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and not any(item in content_type for item in ("text/html", "text/plain", "application/xhtml")):
            raise CrawlerError("只支持抓取 HTML 或纯文本网页")

        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise CrawlerError(f"网页内容超过限制，最大 {max_bytes} 字节")
            chunks.append(chunk)
        response._content = b"".join(chunks)
        return response

    raise CrawlerError("网页重定向次数过多")


def _extract_text_from_html(raw_text: str) -> Tuple[str, str]:
    """从 HTML 中提取标题和正文。"""

    parser = _ReadableHTMLParser()
    parser.feed(raw_text)
    jsonld_text = _extract_jsonld_text(raw_text)
    text = _select_best_text(parser.priority_text, jsonld_text, parser.text, parser.meta_text)
    return parser.title, text


def _render_page_html(url: str) -> Tuple[str, str]:
    """用无头浏览器渲染页面后读取 HTML。

    这个函数只在 CRAWLER_BROWSER_FALLBACK=1 时调用。Playwright 没安装时给出清晰错误，
    不影响默认的轻量抓取路径。
    """

    if not _browser_fallback_enabled():
        raise CrawlerError("网页正文过短，未提取到有效内容；如该网页依赖浏览器渲染，可启用浏览器抓取兜底")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CrawlerError(
            "浏览器抓取未安装：请安装 playwright，并执行 python -m playwright install chromium"
        ) from exc

    safe_url = validate_crawl_url(url)
    timeout_ms = int(float(os.getenv("CRAWLER_BROWSER_TIMEOUT_SECONDS", "20")) * 1000)
    wait_until = os.getenv("CRAWLER_BROWSER_WAIT_UNTIL", "networkidle")
    user_agent = os.getenv(
        "CRAWLER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AgentPlatformCrawler/1.0",
    )
    blocked_resource_types = {"image", "media", "font"}
    validated_hosts: Dict[str, bool] = {}

    def is_request_allowed(request_url: str) -> bool:
        parsed = urlparse(request_url)
        if parsed.scheme not in {"http", "https"}:
            return False
        cache_key = parsed.netloc.lower()
        if cache_key in validated_hosts:
            return validated_hosts[cache_key]
        try:
            validate_crawl_url(request_url)
            validated_hosts[cache_key] = True
        except CrawlerError:
            validated_hosts[cache_key] = False
        return validated_hosts[cache_key]

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
            page = browser.new_page(user_agent=user_agent, locale="zh-CN")

            def route_handler(route):
                request = route.request
                if request.resource_type in blocked_resource_types or not is_request_allowed(request.url):
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", route_handler)
            page.goto(safe_url, wait_until=wait_until, timeout=timeout_ms)
            final_url = validate_crawl_url(page.url)
            rendered_html = page.content()
            browser.close()
            return final_url, rendered_html
    except PlaywrightTimeoutError as exc:
        raise CrawlerError("浏览器渲染网页超时") from exc
    except CrawlerError:
        raise
    except Exception as exc:
        raise CrawlerError(f"浏览器渲染网页失败: {exc}") from exc


def crawl_url_to_markdown(url: str) -> Dict[str, object]:
    """抓取一个网页并转换成可入库的 Markdown 文档。"""

    used_browser = False
    browser_error_text = ""
    min_text_length = _env_int("CRAWLER_MIN_TEXT_LENGTH", 50)
    try:
        response = _download(url)
        final_url = response.url
        response_content = getattr(response, "content", getattr(response, "_content", b""))
        raw_text = _decode_response(response, response_content)
        content_type = response.headers.get("Content-Type", "").lower()
    except CrawlerError as download_error:
        if not _browser_fallback_enabled():
            raise
        try:
            final_url, raw_text = _render_page_html(url)
            content_type = "text/html"
            used_browser = True
        except CrawlerError as browser_error:
            raise CrawlerError(f"{download_error}；浏览器兜底也失败：{browser_error}") from browser_error

    if "text/html" in content_type or "<html" in raw_text[:500].lower():
        title, text = _extract_text_from_html(raw_text)
        if len(text) < min_text_length and not used_browser:
            try:
                final_url, raw_text = _render_page_html(url)
                title, text = _extract_text_from_html(raw_text)
                used_browser = True
            except CrawlerError as browser_error:
                browser_error_text = str(browser_error)
    else:
        title = ""
        text = _normalize_text(raw_text)

    if len(text) < min_text_length:
        if browser_error_text:
            raise CrawlerError(f"网页正文过短，未提取到有效内容；浏览器兜底失败：{browser_error_text}")
        raise CrawlerError("网页正文过短，未提取到有效内容")

    file_name = _safe_file_name(final_url, title)
    markdown = (
        f"# {title or file_name.removesuffix('.md')}\n\n"
        f"来源：{final_url}\n\n"
        f"{text}\n"
    )
    content = markdown.encode("utf-8")
    return {
        "url": final_url,
        "title": title,
        "file_name": file_name,
        "file_type": "md",
        "content": content,
        "size": len(content),
    }
