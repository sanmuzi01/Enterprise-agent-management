"""试点评价的真实浏览器验收（前提：scripts\\demo.py start 已运行，用离线演示模型）。

demo_emp 整理一份报销 → 核对保存 → 给评分、填手工办理时间和一句话 → 页面确认已记录；再生成试点报告，检查评价被匿名汇总。
用法：$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\e2e_pilot_browser.py
"""
import asyncio
import os
import pathlib
import subprocess
import sys
import tempfile

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("DEMO_URL", "http://localhost:5173")
ROOT = pathlib.Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
        page = await (await browser.new_context(viewport={"width": 1280, "height": 900})).new_page()
        await page.goto(f"{BASE}/login")
        await page.get_by_placeholder("3–20 个字符").fill("demo_emp")
        await page.get_by_placeholder("至少 6 位").fill("Demo@12345")
        await page.get_by_role("button", name="登录", exact=True).click()
        await page.wait_for_url("**/department", timeout=20000)
        skip = page.get_by_role("button", name="我先自己看看")
        if await skip.count():
            await skip.click()
        await page.wait_for_selector("[data-testid=home-cards]")
        panel = page.locator("[data-testid=automation-panel]").first
        await panel.get_by_label("工作类型").select_option(label="费用材料 → 报销草稿")
        await panel.locator("#automation-source").fill("10月20日出差高铁票310元，发票号PL0001；市内交通56元，发票号PL0002。")
        await panel.get_by_role("button", name="开始整理").click()
        await expect(panel.locator("[data-testid=business-checks]")).to_be_visible(timeout=30000)
        await panel.get_by_label("我已核对原文").check()
        await panel.get_by_role("button", name="确认保存业务草稿").click()
        await expect(panel.locator("[data-testid=feedback-form]")).to_be_visible(timeout=30000)
        print("  ✔ 保存成果后出现评价表单")
        await expect(panel.locator("[data-testid=feedback-submit]")).to_be_disabled()
        await panel.locator("[data-testid=feedback-rating]").select_option(value="4")
        await panel.locator("[data-testid=feedback-minutes]").fill("18")
        await panel.locator("[data-testid=feedback-comment]").fill("金额和发票号都提对了，日期还要手动改")
        await panel.locator("[data-testid=feedback-submit]").click()
        await expect(panel.locator("[data-testid=feedback-done]")).to_contain_text("已记录")
        print("  ✔ 评分、手工办理时间、一句话评价提交成功")
        await browser.close()

    out = pathlib.Path(tempfile.gettempdir()) / "pilot-report-e2e.md"
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "pilot_report.py"), "--days", "1", "--out", str(out)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr[-500:]
    text = out.read_text(encoding="utf-8")
    assert "金额和发票号都提对了" in text, text[:800]
    assert "demo_emp" not in text, "报告里不应出现用户名"
    assert "样本量小于" in text, "样本少时报告应当提醒"
    print("  ✔ 试点报告汇总了这条评价，匿名，并提醒样本量不足")
    print("\n试点评价浏览器验收通过")


if __name__ == "__main__":
    asyncio.run(main())
