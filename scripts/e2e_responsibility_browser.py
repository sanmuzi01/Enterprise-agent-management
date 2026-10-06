"""责任协同的真实浏览器验收（Playwright + 真实前端/后端/Java/MySQL，AI 整理用离线演示模型）。

前置：scripts\\demo.py start 已经跑起来（演示数据 + 离线演示模型）。
故事线与 scripts/e2e_responsibility.py 相同，但全部通过页面操作完成：
负责人粘贴会议纪要 → AI 提取 → 核对保存为草稿 → 补全缺口并发布 → 员工接受/报告受阻/提交 → 验收人退回再验收 → 负责人看板与周报。
用法：$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\e2e_responsibility_browser.py [--shots]
  --shots  把关键页面截图存到 docs/demo-backup/responsibility/，并生成 index.html（现场出问题时的图文备份）。
"""
import asyncio
import html
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright, expect

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo-backup" / "responsibility"
BASE = os.environ.get("DEMO_URL", "http://localhost:5173")
PASSWORD = "Demo@12345"
SHOTS_ON = "--shots" in sys.argv
SHOTS = []
STAMP = datetime.now(timezone(timedelta(hours=8))).strftime("%m%d%H%M")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

MEETING = (
    "10月12日例会纪要：会议决定新版首页本月发布。"
    "demo_emp负责新版首页联调，下周五前提交可部署的前端构建包，验收标准是测试环境回归通过且无阻断问题，由demo_owner验收。"
    "demo_newbie负责整理客户反馈清单，月底前提交反馈汇总表，由demo_owner验收。"
    "demo_emp负责更新发布说明文档，下周三前提交最新版文档，验收标准是覆盖全部新功能，由demo_owner验收。"
    "供应商对账需要在下周三前完成。"
    "新版首页的埋点方案要在下周五前确定。"
    "关于是否增加会员页的问题，大家还在讨论，暂不决定。")


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
    if not SHOTS_ON:
        print(f"  ✔ {title}")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{len(SHOTS) + 1:02d}-{name}.png"
    if selector:
        await page.locator(selector).first.evaluate("el => el.scrollIntoView({ block: 'start' })")
    await page.wait_for_timeout(3200)   # 等提示条消失
    await page.screenshot(path=str(path), full_page=False)
    SHOTS.append((path.name, title, note))
    print(f"  ✔ {title}")


async def open_collab(page, tab=None):
    await page.locator("[data-testid=dept-section-collab]").click()
    await page.wait_for_selector("[data-testid=resp-module]")
    if tab:
        await page.locator(f"[data-testid=resp-tab-{tab}]").click()
        await page.wait_for_timeout(500)


async def do_action(page, action, **fields):
    """在责任详情里点一个动作按钮、填表单、提交。"""
    await page.locator(f"[data-testid=resp-action-{action}]").click()
    form = page.locator(f"[data-testid=resp-form-{action}]")
    await expect(form).to_be_visible()
    for key, value in fields.items():
        field = form.locator(f"[data-testid=resp-field-{key}]")
        if isinstance(value, bool) or key.startswith("approve"):
            await field.select_option(label=value)
        elif await field.evaluate("el => el.tagName") == "SELECT":
            await field.select_option(label=value)
        else:
            await field.fill(str(value))
    await form.locator("[data-testid=resp-action-submit]").click()
    await expect(form).to_be_hidden(timeout=15000)


async def open_task_by_title(page, text_):
    row = page.locator("[data-testid^=resp-task-]").filter(has_text=text_).first
    await row.click()
    await expect(page.locator("[data-testid=resp-task-detail]")).to_be_visible()
    await expect(page.locator("[data-testid=resp-task-detail] h3")).to_contain_text(text_[:6])


async def status_text(page):
    return (await page.locator("[data-testid=resp-task-status]").inner_text()).strip()


def reset_responsibility_data():
    """彩排前清掉演示企业里上一次留下的责任计划（只动责任协同相关数据），让每次都从干净的状态开始。"""
    from urllib.parse import quote_plus
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    load_dotenv(ROOT / ".env")
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host, port = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1"), os.getenv("ENTERPRISE_DB_PORT", "3306")
    main_db = create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{os.getenv('DB_NAME', 'agent_sql')}?charset=utf8mb4")
    ent = create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{os.getenv('ENTERPRISE_DB_NAME', 'enterprise_business')}?charset=utf8mb4")
    with main_db.begin() as conn:
        teams = [r[0] for r in conn.execute(text("SELECT t.id FROM teams t JOIN organizations o ON o.id=t.organization_id WHERE o.name LIKE '星河科技%'")).all()]
        users = [r[0] for r in conn.execute(text(r"SELECT id FROM `user` WHERE name LIKE 'demo\_%'")).all()]
        if users:
            ids = ",".join(str(u) for u in users)
            conn.execute(text(f"DELETE FROM automation_work WHERE kind='responsibility' AND user_id IN ({ids})"))
            conn.execute(text(rf"DELETE FROM work_item WHERE rule LIKE 'resp\_%' AND user_id IN ({ids})"))
    if teams:
        ids = ",".join(str(t) for t in teams)
        mine = f"SELECT id FROM responsibility_plan WHERE team_id IN ({ids}) AND title LIKE '10月12日例会纪要%'"   # 只清彩排产生的，保留种子里的历史计划
        with ent.begin() as conn:
            for sql in (f"DELETE FROM responsibility_event WHERE plan_id IN ({mine})",
                        f"DELETE FROM responsibility_deliverable WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({mine}))",
                        f"DELETE FROM responsibility_dependency WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({mine}))",
                        f"DELETE FROM responsibility_collaborator WHERE task_id IN (SELECT id FROM responsibility_task WHERE plan_id IN ({mine}))",
                        f"DELETE FROM responsibility_task WHERE plan_id IN ({mine})",
                        f"DELETE FROM responsibility_plan WHERE team_id IN ({ids}) AND title LIKE '10月12日例会纪要%'"):
                conn.execute(text(sql))


async def main():
    started = time.time()
    reset_responsibility_data()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)

        print("角色 1：销售负责人 demo_head —— 粘贴会议纪要，AI 提取，补全并发布")
        page = await login(browser, "demo_head")
        await shot(page, "head-home", "部门首页新增责任协同卡片", "待我接受、我的执行中、部门逾期、本周完成率——按身份显示。", "[data-testid=home-cards]")
        await open_collab(page, "extract")
        await page.locator("#automation-source-responsibility").fill(MEETING)
        await page.get_by_role("button", name="开始整理").click()
        await expect(page.locator("[data-testid=business-checks]")).to_be_visible(timeout=30000)
        checks = await page.locator("[data-testid=business-checks]").inner_text()
        assert "共整理出 5 项责任" in checks and "还没有主责员工" in checks, checks
        await shot(page, "extract", "AI 从会议纪要提取 5 项责任，并指出缺口",
                   "姓名只匹配本企业有效成员，期限按“今天”换算，原文没写的留空待补充；仅讨论的内容不会变成责任事项。", "[data-testid=business-checks]")
        await page.get_by_label("我已核对原文").check()
        await page.get_by_role("button", name="确认保存业务草稿").click()
        await expect(page.locator("[data-testid=open-plan-result]")).to_be_visible(timeout=20000)
        await page.locator("[data-testid=open-plan-result]").click()
        await expect(page.locator("[data-testid=resp-plan-detail]")).to_be_visible()
        await expect(page.locator("[data-testid=resp-draft-banner]")).to_contain_text("还没有通知任何员工")
        await expect(page.locator("[data-testid=resp-publish]")).to_be_disabled()
        await shot(page, "draft", "草稿计划：逐项补全，缺口一目了然", "缺主责员工、验收标准、验收人的责任事项会标红；补全前不能发布，也没有通知任何员工。", "[data-testid=resp-plan-detail]")

        # 补全：第 2 项缺验收标准；第 4、5 项缺责任人/交付物/验收标准/验收人
        async def fill(seq, **values):
            for key, value in values.items():
                field = page.locator(f"[data-testid=resp-{key}-{seq}]")
                if await field.evaluate("el => el.tagName") == "SELECT":
                    await field.select_option(label=value)
                else:
                    await field.fill(value)
            await page.locator(f"[data-testid=resp-save-{seq}]").click()
            await page.wait_for_timeout(800)

        await fill(2, criteria="汇总表覆盖本月全部客户反馈")
        await fill(4, responsible="demo_newbie", reviewer="demo_owner", deliverable="对账差异清单", criteria="差异项全部有说明")
        await fill(5, responsible="demo_emp", reviewer="demo_owner", deliverable="埋点方案文档", criteria="覆盖首页全部关键事件")
        await expect(page.locator("[data-testid=resp-publish-box]")).to_contain_text("信息齐全")
        await page.locator("[data-testid=resp-publish-confirm]").check()
        await shot(page, "ready", "缺口补全后由负责人确认发布", "发布是负责人的决定：要勾选确认；员工会收到待接受的责任。", "[data-testid=resp-publish-box]")
        await page.locator("[data-testid=resp-publish]").click()
        await expect(page.locator("[data-testid=resp-plan-status]")).to_contain_text("进行中", timeout=15000)
        await page.context.close()

        print("角色 2：销售员工 demo_emp —— 查看依据、接受、受阻、提交")
        page = await login(browser, "demo_emp")
        await expect(page.locator("[data-testid=home-card-resp_accept]")).to_contain_text("待我接受")
        accept_count = await page.locator("[data-testid=home-card-resp_accept] p").nth(1).inner_text()
        assert accept_count.strip() not in ("0", ""), accept_count
        await open_collab(page, "mine")
        await open_task_by_title(page, "demo_emp负责新版首页联调")
        await expect(page.locator("[data-testid=resp-criteria]")).to_contain_text("测试环境回归通过")
        await shot(page, "emp-task", "员工先看原文依据、交付物和验收标准再决定是否接受", "接受是员工自己的承诺：指派人和别人都不能替他接受；对期限不合适可以提异议。", "[data-testid=resp-task-detail]")
        await do_action(page, "accept")
        assert "执行中" in await status_text(page)
        await do_action(page, "block", reason="缺少测试环境账号")
        assert "受阻" in await status_text(page)
        await expect(page.locator("[data-testid=resp-blocked]")).to_contain_text("缺少测试环境账号")
        await page.context.close()

        print("角色 1（续）：负责人看到受阻并协调")
        page = await login(browser, "demo_head")
        await open_collab(page, "team")
        await expect(page.locator("[data-testid=resp-narrative]")).to_contain_text("受阻")
        await shot(page, "head-board", "负责人的部门看板：受阻、未接受、逾期一目了然", "周报文字只陈述系统里的事实，不评价员工；指标看闭环质量，不看完成数量。", "[data-testid=resp-team]")
        await page.locator("[data-testid^=resp-task-]").filter(has_text="demo_emp负责新版首页联调").first.click()
        await do_action(page, "unblock", note="已找运维开通账号")
        assert "执行中" in await status_text(page)
        await page.context.close()

        print("角色 2（续）：员工提交成果")
        page = await login(browser, "demo_emp")
        await open_collab(page, "mine")
        await open_task_by_title(page, "demo_emp负责新版首页联调")
        await do_action(page, "submit", summary="构建包已上传到制品库", link="https://example.com/build/1")
        assert "待验收" in await status_text(page)
        await expect(page.locator("[data-testid=resp-actions]")).to_have_count(0)   # 提交后员工不能自己验收
        await page.context.close()

        print("延期：员工申请，负责人决定")
        later = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=30)).strftime("%Y-%m-%d")
        page = await login(browser, "demo_emp")
        await open_collab(page, "mine")
        await open_task_by_title(page, "demo_emp负责更新发布说明文档")
        await do_action(page, "accept")
        await do_action(page, "request-extension", proposed_date=later, reason="发布说明要等新功能冻结")
        await expect(page.locator("[data-testid=resp-task-detail]")).to_contain_text(f"申请延期到 {later}")
        await page.context.close()
        page = await login(browser, "demo_head")
        await open_collab(page, "team")
        await page.locator("[data-testid^=resp-task-]").filter(has_text="demo_emp负责更新发布说明文档").first.click()
        await do_action(page, "decide-extension", approve="同意", note="同意")
        await expect(page.locator("[data-testid=resp-task-detail]")).to_contain_text(later)
        await shot(page, "extension", "延期由员工申请、负责人决定", "改期限必须有原因并留下记录；员工不能自己改，也不会被静默改动。", "[data-testid=resp-task-detail]")
        await page.context.close()

        print("角色 3：验收人 demo_owner —— 对照标准验收，退回一次再通过")
        page = await login(browser, "demo_owner")
        await open_collab(page, "review")
        await open_task_by_title(page, "demo_emp负责新版首页联调")
        await expect(page.locator("[data-testid=resp-deliverables]")).to_contain_text("构建包已上传")
        await shot(page, "reviewer", "验收人对照验收标准验收", "只有指定的验收人能验收，主责人和指派人都不能；可以退回并写明原因。", "[data-testid=resp-task-detail]")
        await do_action(page, "rework", reason="回归用例没有覆盖登录页")
        assert "执行中" in await status_text(page)
        await page.context.close()

        page = await login(browser, "demo_emp")
        await open_collab(page, "mine")
        await open_task_by_title(page, "demo_emp负责新版首页联调")
        await do_action(page, "submit", summary="补充了登录页回归用例")
        await page.context.close()

        page = await login(browser, "demo_owner")
        await open_collab(page, "review")
        await open_task_by_title(page, "demo_emp负责新版首页联调")
        await do_action(page, "verify", note="符合验收标准")
        assert "已完成" in await status_text(page)
        await expect(page.locator("[data-testid=resp-timeline]")).to_contain_text("验收退回")
        await shot(page, "done", "履责记录只增不改：谁、何时、做了什么", "接受、受阻、解除、提交、退回、验收全部留痕，员工也能看到每次变更。", "[data-testid=resp-timeline]")
        await page.context.close()

        await browser.close()
    if SHOTS_ON:
        write_index()
    print(f"\n责任协同浏览器验收通过，用时 {time.time() - started:.0f} 秒")


def write_index():
    cards = "\n".join(
        f'<section><h2>{i}. {html.escape(title)}</h2><p>{html.escape(note)}</p><img src="{name}" alt="{html.escape(title)}"></section>'
        for i, (name, title, note) in enumerate(SHOTS, start=1))
    (OUT / "index.html").write_text(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>责任协同 · 演示备份</title>
<style>body{{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:1000px;margin:0 auto;padding:24px 16px;color:#1e293b;background:#f8fafc}}
h1{{font-size:22px}}section{{margin:28px 0;padding:16px;background:#fff;border:1px solid #e2e8f0;border-radius:8px}}h2{{font-size:17px;margin:0 0 4px}}
p{{margin:0 0 12px;color:#475569}}img{{max-width:100%;border:1px solid #e2e8f0;border-radius:6px}}</style></head>
<body><h1>责任协同 · 演示备份</h1><p>从会议纪要到验收闭环的图文备份。账号和密码见 docs/demo-script.md。</p>
{cards}</body></html>""", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
