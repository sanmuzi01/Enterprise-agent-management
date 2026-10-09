import { describe, expect, it } from 'vitest'
import { buildMessageCards } from './agentCards'
import { summarizeReply, type SummaryStep } from './agentSummary'

const draft = { id: 18, applicantUserId: 7, teamId: 3, status: 'DRAFT', totalAmount: 680,
  lines: [{ category: 'TRAVEL', amount: 680, description: '住宿费', invoiceNo: null }], createdAt: '2026-10-09T02:00:00Z' }

describe('agentSummary', () => {
  it('写清办成了什么、做了几步、还要你处理什么', () => {
    const results = [
      { name: 'get_expense_budget', result: '{"teamId":3,"year":2026,"remainingAmount":12400}' },
      { name: 'create_expense_draft', result: JSON.stringify(draft) },
    ]
    const steps: SummaryStep[] = [
      { name: 'understand', label: '理解需求', state: 'done' },
      { name: 'get_expense_budget', label: '查询报销预算', state: 'done' },
      { name: 'create_expense_draft', label: '创建报销草稿', state: 'done' },
    ]
    const s = summarizeReply({ steps, results, pendingConfirmations: [], cards: buildMessageCards(results).cards })
    expect(s.done).toEqual(['创建报销草稿（报销单 #18）'])
    expect(s.stepCount).toBe(2)
    expect(s.attention).toContain('1 张草稿等你确认提交')
    expect(s.attention).toContain('发票：还没有填发票号')
    expect(s.failed).toEqual([])
  })

  it('失败的步骤带原因，失败的写操作不算办成', () => {
    const results = [{ name: 'create_purchase_draft', result: '{"error":"部门采购预算不足"}' }]
    const steps: SummaryStep[] = [
      { name: 'create_purchase_draft', label: '创建采购草稿', state: 'error', error: '部门采购预算不足' },
    ]
    const s = summarizeReply({ steps, results, pendingConfirmations: [], cards: [] })
    expect(s.done).toEqual([])
    expect(s.stepCount).toBe(0)
    expect(s.failed).toEqual(['创建采购草稿：部门采购预算不足'])
  })

  it('待确认的高风险操作列进“要你处理”', () => {
    const s = summarizeReply({ steps: [], results: [], pendingConfirmations: ['submit_expense_claim'], cards: [] })
    expect(s.attention[0]).toContain('等你确认后才会执行')
  })
})
