"""Exercise the real Vue panel with isolated API fixtures; requires Vite on 5174."""
import asyncio
import json
import os
from urllib.parse import urlparse

import sys

from playwright.async_api import async_playwright, expect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from service.workflows import catalog  # noqa: E402  用真实的工作流注册表驱动页面


async def main():
    works = []
    errors = []
    dept = {"code": "sales"}

    def checks_for(proposal):
        bad = [i['sku'] for i in proposal.get('items', []) if i.get('sku') == 'BAD-SKU']
        return ([{"level": "blocker", "text": "SKU BAD-SKU 不在产品目录中，保存会被拒绝，请改为真实 SKU"}] if bad
                else [{"level": "info", "text": "库存与预算核对通过"}])

    async def api(route):
        request = route.request
        path = urlparse(request.url).path
        if not path.startswith('/api/'):
            await route.continue_()
            return
        body = request.post_data_json if request.post_data else {}
        if path.endswith('/automation/workflows'):
            result = catalog(dept["code"])
        elif path.endswith('/llm_config/list'):
            result = [{"id": 1, "model_name": "test-model", "kind": "chat", "is_active": 1}]
        elif path.endswith('/crm/customers'):
            result = [{"id": 42, "name": "测试客户"}]
        elif path.endswith('/automation') and request.method == 'GET':
            result = {"items": works, "stats": {"total": len(works), "ready": sum(w['status'] == 'ready' for w in works),
                      "applied": sum(w['status'] == 'applied' for w in works), "failed": 0,
                      "elapsed_ms": 1200, "total_tokens": 100, "edited": 0}}
        elif path.endswith('/automation'):
            src = body['source_text']
            proposal = {
                "crm": {"content": "客户希望试用", "evidence": src, "warnings": [],
                        "tasks": [{"title": "发送方案", "due_date": "2026-10-08", "evidence": src}]},
                "expense": {"lines": [{"category": "TRAVEL", "amount": "260.00", "description": "高铁票",
                                       "invoice_no": "G001", "evidence": src}], "warnings": []},
                # 开始日期原文不明确：模型留空，用户补全后才能保存。
                "leave": {"leave_type_code": "annual", "start_date": None, "end_date": "2026-10-14",
                          "reason": "家庭事务", "evidence": src, "warnings": ["开始日期不明确，请补全"]},
                "procurement": {"items": [{"sku": "BAD-SKU", "quantity": 10, "evidence": src}], "warnings": []},
            }[body['kind']]
            result = {**body, "id": str(len(works) + 1), "status": "ready", "proposal": proposal,
                      "business_checks": checks_for(proposal),
                      "elapsed_ms": 1200, "completed_tasks": [], "created_at": "2026-10-01T00:00:00Z",
                      "business_result": None, "error_message": None}
            works.insert(0, result)
        else:
            work_id = path.split('/automation/')[1].split('/')[0]
            result = next(w for w in works if w['id'] == work_id)
            if path.endswith('/checks'):
                result['business_checks'] = checks_for(body['proposal'])
            elif path.endswith('/apply') and any(i.get('sku') == 'BAD-SKU' for i in body['proposal'].get('items', [])):
                # 与后端一致：业务系统明确拒绝后退回可修改状态，并带上业务原因。
                result.update(status='ready', error_message='业务系统未接受：产品不存在: BAD-SKU。请修改后重新保存，或将原文带回重新整理')
            elif path.endswith('/apply'):
                result.update(status='applied', proposal=body['proposal'], business_result={"id": 123},
                              error_message=None)
            elif '/tasks/' in path:
                result['completed_tasks'] = [0] if body['done'] else []
        await route.fulfill(content_type='application/json', body=json.dumps(result))

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHANNEL') or None)
        page = await browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('**/api/**', api)
        await page.route('**/__automation-test*', lambda route: route.fulfill(content_type='text/html', body='''
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body><div id="app"></div><script type="module">
            import {createApp} from '/node_modules/.vite/deps/vue.js';
            import Panel from '/src/components/AutomationWorkPanel.vue';
            import '/src/style.css';
            const dept = new URLSearchParams(location.search).get('dept') || 'sales';
            createApp(Panel, {teamId: 1, departmentCode: dept}).mount('#app');
            </script></body></html>'''))
        await page.goto('http://127.0.0.1:5174/__automation-test')
        await page.get_by_label('保存到哪个客户').select_option('42')
        await page.locator('#automation-source').fill('客户希望先试用，约定2026年10月8日发送方案。')
        await page.get_by_role('button', name='开始整理', exact=True).click()
        await page.get_by_label('跟进内容', exact=True).fill('核对后：客户希望先试用。')
        save = page.get_by_role('button', name='确认保存业务草稿', exact=True)
        await expect(save).to_be_disabled()
        await page.get_by_label('我已核对原文', exact=False).check()
        await save.click()
        await expect(page.get_by_text('已保存跟进草稿 #123', exact=False)).to_be_visible()
        assert works[0]['proposal']['content'] == '核对后：客户希望先试用。'
        await page.get_by_label('发送方案 · 2026-10-08', exact=True).check()
        assert works[0]['completed_tasks'] == [0]
        await page.get_by_label('工作类型', exact=False).select_option('expense')
        await page.locator('#automation-source').fill('10月1日出差高铁票260元，发票号G001。')
        await page.get_by_role('button', name='开始整理', exact=True).click()
        await expect(page.get_by_text('合计 ¥260.00')).to_be_visible()
        await page.get_by_label('金额（元）', exact=True).fill('250.00')
        await expect(page.get_by_text('合计 ¥250.00')).to_be_visible()
        await page.get_by_label('我已核对原文', exact=False).check()
        await save.click()
        await expect(page.get_by_text('已保存报销草稿 #123', exact=False)).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        await page.reload()
        await page.get_by_role('button', name='费用材料整理', exact=False).click()
        await expect(page.get_by_text('合计 ¥250.00')).to_be_visible()
        await page.set_viewport_size({"width": 1280, "height": 900})

        # OA 请假：开始日期缺失时不能保存，补全后才可保存。
        await page.get_by_label('工作类型', exact=False).select_option('leave')
        await page.locator('#automation-source').fill('我想请年假到2026年10月14日，处理家庭事务。')
        await page.get_by_role('button', name='开始整理', exact=True).click()
        await expect(page.get_by_text('开始日期不明确，请补全')).to_be_visible()
        await expect(page.get_by_text('请补全开始日期。')).to_be_visible()
        await page.get_by_label('我已核对原文', exact=False).check()
        await expect(save).to_be_disabled()
        await page.get_by_label('开始日期', exact=True).fill('2026-10-12')
        await page.get_by_label('我已核对原文', exact=False).check()
        await save.click()
        await expect(page.get_by_text('已保存请假草稿 #123', exact=False)).to_be_visible()
        assert works[0]['proposal']['start_date'] == '2026-10-12', works[0]['proposal']

        # 采购：错误 SKU 被业务系统拒绝 → 退回可修改 → 改正后保存。
        dept["code"] = "procurement"
        await page.goto('http://127.0.0.1:5174/__automation-test?dept=procurement')
        await expect(page.get_by_label('工作类型', exact=False)).to_have_value('procurement')
        await page.locator('#automation-source').fill('需要补货 BAD-SKU 共10件。')
        await page.get_by_role('button', name='开始整理', exact=True).click()
        checks = page.get_by_test_id('business-checks')
        await expect(checks.get_by_text('SKU BAD-SKU 不在产品目录中', exact=False)).to_be_visible()
        await expect(checks.get_by_text('会被拒绝', exact=True)).to_be_visible()
        await page.get_by_label('我已核对原文', exact=False).check()
        await save.click()
        await expect(page.get_by_text('产品不存在: BAD-SKU', exact=False)).to_be_visible()
        await expect(page.get_by_role('button', name='将原文带回重新整理')).to_be_visible()
        await page.get_by_label('产品 SKU', exact=True).fill('PAPER-A4')
        await expect(page.get_by_text('内容已修改，以下结论可能已过期', exact=False)).to_be_visible()
        await page.get_by_role('button', name='重新核对业务数据').click()
        await expect(checks.get_by_text('库存与预算核对通过')).to_be_visible()
        await expect(checks.get_by_text('SKU BAD-SKU 不在产品目录中', exact=False)).to_have_count(0)
        await page.get_by_label('我已核对原文', exact=False).check()
        await save.click()
        await expect(page.get_by_text('已保存采购草稿 #123', exact=False)).to_be_visible()
        assert works[0]['proposal']['items'][0]['sku'] == 'PAPER-A4', works[0]['proposal']
        # 通用校验：重复 SKU 与数量越界都按工作流声明拦截在前端。
        await page.locator('#automation-source').fill('需要补货 BAD-SKU 共10件，再来一次。')
        await page.get_by_role('button', name='开始整理', exact=True).click()
        await page.get_by_label('产品 SKU', exact=True).fill('PAPER-A4')
        await page.get_by_label('采购数量', exact=True).fill('0')
        await expect(page.get_by_text('采购数量需为 1 至 1,000,000 的整数。')).to_be_visible()
        # 非采购部门看不到采购入口，销售部门才有 CRM 入口。
        assert await page.locator('option[value="crm"]').count() == 0
        assert not errors, errors
        print('PASS: CRM, expense, OA leave (incomplete -> fixed), procurement (blocker check -> rejected SKU -> '
              'edit -> recheck -> fixed), '
              'history reload, mobile layout; mocked APIs')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
