"""批量整理的真实浏览器验收（前提：scripts\\demo.py start 已运行，用离线演示模型）。

demo_emp 一次选 3 份报销材料（2 份正常、1 份没有金额）→ 后台依次整理，页面显示进度 →
正常的可以点开核对并保存、失败的那份给出原因，可重试或带回原文手动处理。
用法：$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\\Scripts\\python.exe scripts\\e2e_batch_browser.py [--shots]
"""
import asyncio
import os
import pathlib
import sys
import tempfile

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("DEMO_URL", "http://localhost:5173")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo-backup" / "batch"
SHOTS = "--shots" in sys.argv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FILES = {
    "10月差旅.txt": "10月8日出差高铁票260元，发票号G9001；出租车48元，发票号T9002。",
    "办公用品.txt": "10月9日购买打印耗材129元，发票号P9003。",
    "只有说明.txt": "这个月的报销材料我还没整理好，具体金额等财务确认以后再补充上来。",
}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        await page.goto(f"{BASE}/login")
        await page.get_by_placeholder("3–20 个字符").fill("demo_emp")
        await page.get_by_placeholder("至少 6 位").fill("Demo@12345")
        await page.get_by_role("button", name="登录", exact=True).click()
        await page.wait_for_url("**/department", timeout=20000)
        skip = page.get_by_role("button", name="我先自己看看")
        if await skip.count():
            await skip.click()
        await page.wait_for_selector("[data-testid=home-cards]")

        toggle = page.locator("[data-testid=batch-tools-toggle]")   # AI 整理收在“批量与跨部门办理”里，默认折叠
        if await toggle.get_attribute("aria-expanded") != "true":
            await toggle.click()
        panel = page.locator("[data-testid=automation-panel]").first
        await panel.get_by_label("工作类型").select_option(label="费用材料 → 报销草稿")
        await panel.locator("[data-testid=batch-panel] summary").click()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            paths = []
            for name, text in FILES.items():
                path = pathlib.Path(folder) / name
                path.write_text(text, encoding="utf-8")
                paths.append(str(path))
            await panel.locator("[data-testid=batch-files]").set_input_files(paths)
            await expect(panel.locator("[data-testid=batch-staged] li")).to_have_count(3, timeout=30000)
        print("  ✔ 一次选了 3 份文件，逐份提取了文字")
        await panel.locator("[data-testid=batch-start]").click()
        await expect(panel.locator("[data-testid=batch-summary]")).to_contain_text("整理完成：3/3", timeout=60000)
        await expect(panel.locator("[data-testid=batch-summary]")).to_contain_text("1 份失败")
        items = panel.locator("[data-testid^=batch-item-]")
        await expect(items.nth(0)).to_contain_text("待核对")
        await expect(items.nth(2)).to_contain_text("失败")
        print("  ✔ 后台依次整理：2 份待核对、1 份失败，每份独立成败")
        if SHOTS:
            OUT.mkdir(parents=True, exist_ok=True)
            await panel.locator("[data-testid=batch-panel]").scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(OUT / "01-batch.png"))
        await items.nth(0).get_by_role("button", name="核对并保存").click()
        await expect(panel.locator("[data-testid=business-checks]")).to_be_visible(timeout=20000)
        print("  ✔ 点开其中一份，进入和单份整理完全一样的核对页")
        await items.nth(2).get_by_role("button", name="带回原文手动处理").click()
        await expect(page.locator("#automation-source")).to_have_value(FILES["只有说明.txt"])
        print("  ✔ 失败的一份可以把原文带回输入框手动处理")
        await context.close()
        await browser.close()
    print("\n批量整理浏览器验收通过")


if __name__ == "__main__":
    asyncio.run(main())
