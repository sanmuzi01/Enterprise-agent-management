import { describe, expect, it } from 'vitest'
import { MAX_LIST_CARDS, buildMessageCards, cardFromRecord, money, toolError } from './agentCards'

const expenseDraft = {
  id: 18, applicantUserId: 7, teamId: 3, status: 'DRAFT', totalAmount: 680,
  lines: [{ category: 'TRAVEL', amount: 680, description: '出差住宿费', invoiceNo: null }],
  approverUserId: null, decisionNote: null, createdAt: '2026-10-09T02:00:00Z', submittedAt: null, decidedAt: null,
}

describe('agentCards', () => {
  it('报销草稿：类型、金额、发票缺失提醒，可直接确认提交', () => {
    const card = cardFromRecord(expenseDraft)!
    expect(card.kind).toBe('expense')
    expect(card.title).toBe('报销草稿')
    expect(card.canSubmit).toBe(true)
    expect(card.section).toBe('office')
    const fields = Object.fromEntries(card.fields.map((f) => [f.label, f]))
    expect(fields['类型'].value).toBe('差旅')
    expect(fields['金额'].value).toBe(money(680))
    expect(fields['发票'].warn).toBe(true)
  })

  it('同一次回答里查过预算：余额并进报销卡片，不单独成卡', () => {
    const { cards } = buildMessageCards([
      { name: 'get_expense_budget', result: JSON.stringify({ teamId: 3, year: 2026, remainingAmount: 12400 }) },
      { name: 'create_expense_draft', result: JSON.stringify(expenseDraft) },
    ])
    expect(cards).toHaveLength(1)
    expect(cards[0].fields.find((f) => f.label === '预算余额')?.value).toBe(money(12400))
  })

  it('只查了预算：单独显示预算卡片', () => {
    const { cards } = buildMessageCards([{ name: 'get_expense_budget', result: '{"teamId":3,"year":2026,"remainingAmount":500}' }])
    expect(cards.map((c) => c.kind)).toEqual(['budget'])
  })

  it('已提交的单子不能在卡片上再次提交', () => {
    const card = cardFromRecord({ ...expenseDraft, status: 'SUBMITTED' })!
    expect(card.canSubmit).toBe(false)
    expect(card.title).toBe('报销单 #18')
    expect(card.statusText).toBe('待审批')
  })

  it('同一张单子先建草稿再查状态：只留最后一次', () => {
    const { cards } = buildMessageCards([
      { name: 'create_expense_draft', result: JSON.stringify(expenseDraft) },
      { name: 'get_expense_status', result: JSON.stringify({ ...expenseDraft, status: 'SUBMITTED' }) },
    ])
    expect(cards).toHaveLength(1)
    expect(cards[0].status).toBe('SUBMITTED')
  })

  it('列表结果只展开前几张，其余给数量', () => {
    const many = Array.from({ length: MAX_LIST_CARDS + 3 }, (_, i) => ({ ...expenseDraft, id: i + 1 }))
    const { cards, hidden } = buildMessageCards([{ name: 'get_my_expense_claims', result: JSON.stringify(many) }])
    expect(cards).toHaveLength(MAX_LIST_CARDS)
    expect(hidden).toBe(3)
  })

  it('请假单、工单、采购单、跟进记录都能识别', () => {
    expect(cardFromRecord({ id: 1, leaveTypeCode: 'annual', startDate: '2026-10-12', endDate: '2026-10-13', days: 2, status: 'DRAFT' })?.kind).toBe('leave')
    expect(cardFromRecord({ id: 2, title: '打印机脱机', slaDueAt: '2026-10-09T08:00:00Z', status: 'OPEN', statusLabel: '待处理' })?.kind).toBe('ticket')
    expect(cardFromRecord({ id: 3, requesterUserId: 7, lines: [{ sku: 'A4', productName: 'A4 纸', quantity: 10, unitPrice: 25 }], totalAmount: 250, status: 'DRAFT' })?.section).toBe('business')
    expect(cardFromRecord({ id: 4, customerId: 9, content: '约了下周演示', status: 'DRAFT' })?.canSubmit).toBe(true)
  })

  it('认不出来的数据和错误结果不做卡片，错误能单独取出', () => {
    expect(cardFromRecord({ foo: 1 })).toBeNull()
    expect(buildMessageCards([{ name: 'x', result: 'not json' }]).cards).toEqual([])
    expect(toolError('{"error":"预算不足"}')).toBe('预算不足')
    expect(toolError(JSON.stringify(expenseDraft))).toBeNull()
  })
})
