"""按演示脚本自动走一遍（真实浏览器 + 真实服务 + 离线演示模型），逐步截图并生成 docs/demo-backup/index.html。

两个用途：
1. 演示前的“彩排”：任何一步断言失败都会立刻报出是哪一步，比现场发现强；
2. 现场出问题时的“备份”：打开 docs/demo-backup/index.html 就是带说明的图文演示（没有录屏时的替代）。

前置：scripts\\demo.py start 已经跑起来（含演示数据）。用法：
    $env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\demo_walkthrough.py
"""
import asyncio
import html
import os
import pathlib
import sys

from playwright.async_api import async_playwright, expect

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo-backup"
BASE = os.environ.get("DEMO_URL", "http://localhost:5173")
PASSWORD = "Demo@12345"
SHOTS = []

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def login(browser, user):
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    await page.goto(f"{BASE}/login")
    await page.get_by_placeholder("3–20 个字符").fill(user)
    await page.get_by_placeholder("至少 6 位").fill(PASSWORD)
    await page.get_by_role("button", name="登录", exact=True).click()
    await page.wait_for_url("**/department", timeout=20000)
    skip = page.get_by_role("button", name="我先自己看看")
    if await skip.count():
        await skip.click()
    await page.wait_for_selector("[data-testid=home-cards]", timeout=20000)
    return page


async def shot(page, name, title, note, selector=None):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{len(SHOTS) + 1:02d}-{name}.png"
    if selector:
        await page.locator(selector).first.evaluate("el => el.scrollIntoView({ block: 'start' })")
    await page.wait_for_timeout(3500)   # 等提示条消失，截图更干净
    await page.screenshot(path=str(path), full_page=False)
    SHOTS.append((path.name, title, note))
    print(f"  ✔ {title}")


async def section(page, value):
    await page.locator(f"[data-testid=dept-section-{value}]").click()
    await page.wait_for_timeout(800)


async def open_batch_tools(page):
    """跨部门协同办理和 AI 整理收在概览的“批量与跨部门办理”里，默认折叠。"""
    toggle = page.locator("[data-testid=batch-tools-toggle]")
    if await toggle.get_attribute("aria-expanded") != "true":
        await toggle.click()
    await page.wait_for_timeout(300)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)

        print("角色 1：销售员工 demo_emp —— 一段话办多件事")
        page = await login(browser, "demo_emp")
        await shot(page, "emp-home", "员工登录后直接落在自己的部门工作台", "概览卡片按部门和身份变化：待办、部门客户、进行中的工单。", "[data-testid=home-cards]")
        await open_batch_tools(page)
        await page.locator("#orchestration-text").fill(
            "上周五出差高铁票 260 元，发票号 G8891；出租车 48 元，暂无发票。另外我的笔记本电脑蓝屏了，需要报修，整个组都等着用")
        await page.locator("[data-testid=orchestration-plan]").click()
        await expect(page.locator("[data-testid=orchestration-step-2]")).to_be_visible()
        await shot(page, "emp-plan", "一段话自动拆成各部门步骤", "每一步写明命中了哪些词、由哪个部门处理；拆错了可以改类型或标为不需要。", "[data-testid=orchestration-steps]")
        await page.locator("[data-testid=orchestration-start-1]").click()
        await expect(page.locator("[data-testid=business-checks]")).to_be_visible(timeout=20000)
        await shot(page, "emp-expense-check", "AI 整理报销并对照业务系统核对", "金额、发票号都带原文依据；系统读真实预算，提示“1 条费用没有发票号”。保存前必须人工核对。", "[data-testid=business-checks]")
        await page.locator("[data-testid=orchestration-start-2]").click()
        await expect(page.locator("[data-testid=orchestration-step-2]")).to_contain_text("待核对", timeout=20000)
        await shot(page, "emp-ticket", "IT 工单草稿：规则判断为故障 / 紧急", "“整个组都等着用”被识别为影响范围大，建议紧急；核对页还会给出自助排查建议。", "[data-testid=orchestration-steps]")
        await page.context.close()

        print("角色 2：销售负责人 demo_head —— 审批")
        page = await login(browser, "demo_head")
        await shot(page, "head-home", "负责人的“待我审批”卡片", "请假、报销、IT 申请、人事事项合计在一张卡片里，点击直达。", "[data-testid=home-cards]")
        await section(page, "office")
        await shot(page, "head-office", "办公事务：待审批的请假、报销、入职办理", "负责人在这里批准；批准报销会自动生成记账凭证草稿。")
        await page.context.close()

        print("角色 3：财务专员 demo_fin —— 核对记账凭证")
        page = await login(browser, "demo_fin")
        await section(page, "business")
        await page.locator("[data-testid^=voucher-row-]").first.click()
        await expect(page.locator("[data-testid=voucher-detail]")).to_be_visible()
        await shot(page, "fin-voucher", "自动生成的记账凭证：科目建议、依据、风险项", "每条分录有科目依据和置信度；无票据、大额、业务招待费等风险逐项说明；高风险必须勾选“已核对”才能入账。", "[data-testid=voucher-detail]")
        await page.context.close()

        print("角色 4：IT 工程师 demo_it —— 工单队列与 SLA")
        page = await login(browser, "demo_it")
        await section(page, "business")
        await expect(page.locator("[data-testid=desk-queue]")).to_be_visible()
        await shot(page, "it-queue", "IT 服务台：按处理时限排序的队列", "待接单、处理中、SLA 状态一目了然；接单、等待用户、解决都会留痕。", "[data-testid=desk-queue]")
        await page.locator("[data-testid=desk-tab-summary]").click()
        await expect(page.locator("[data-testid=desk-narrative]")).to_be_visible()
        await shot(page, "it-summary", "服务台汇总与效果指标", "SLA 达成率、平均首次响应、解决耗时、处理人负载——可量化的效率证据。", "[data-testid=desk-summary]")
        await page.context.close()

        print("角色 5：人事专员 demo_hr —— 入转调离")
        page = await login(browser, "demo_hr")
        await section(page, "business")
        await page.locator("[data-testid=hr-tab-hr]").click()
        await expect(page.locator("[data-testid=hr-cases]")).to_be_visible()
        await page.locator("[data-testid^=hr-row-]").first.click()
        await expect(page.locator("[data-testid=hr-detail]")).to_be_visible()
        await shot(page, "hr-case", "入职办理：跨部门办理清单", "人事、IT、财务、负责人、员工各办各的；必办项全部完成且检查无阻断才能办结。", "[data-testid=hr-detail]")
        await page.context.close()

        await browser.close()

    write_index()
    print(f"\n演示彩排通过，{len(SHOTS)} 张截图在 {OUT}（打开 index.html 即图文备份）")


def write_index():
    cards = "\n".join(
        f'<section><h2>{i}. {html.escape(title)}</h2><p>{html.escape(note)}</p><img src="{name}" alt="{html.escape(title)}"></section>'
        for i, (name, title, note) in enumerate(SHOTS, start=1))
    (OUT / "index.html").write_text(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>企业智能工作平台 · 演示备份</title>
<style>body{{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:1000px;margin:0 auto;padding:24px 16px;color:#1e293b;background:#f8fafc}}
h1{{font-size:22px}}section{{margin:28px 0;padding:16px;background:#fff;border:1px solid #e2e8f0;border-radius:8px}}h2{{font-size:17px;margin:0 0 4px}}
p{{margin:0 0 12px;color:#475569}}img{{max-width:100%;border:1px solid #e2e8f0;border-radius:6px}}</style></head>
<body><h1>企业智能工作平台 · 演示备份</h1><p>现场环境出问题时，按这个顺序讲。每张图下面是要讲的一句话；账号和密码见 docs/demo-script.md。</p>
{cards}</body></html>""", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
