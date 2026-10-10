import { describe, expect, it } from 'vitest'
import { MAX_CARDS_PER_REPLY, buildMessageCards, cardFromRecord, money, toolError } from './agentCards'

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
    expect(card.target).toEqual({ section: 'office', record: { kind: 'expense', id: 18 } })
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
    const many = Array.from({ length: MAX_CARDS_PER_REPLY + 3 }, (_, i) => ({ ...expenseDraft, id: i + 1 }))
    const { cards, hidden } = buildMessageCards([{ name: 'get_my_expense_claims', result: JSON.stringify(many) }])
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(hidden).toBe(3)
  })

  it('多个列表累计也最多显示几张：两个工具各返回 5 张，只显示 5 张，其余给数量', () => {
    const listA = Array.from({ length: 5 }, (_, i) => ({ ...expenseDraft, id: i + 1, status: 'SUBMITTED' }))
    const listB = Array.from({ length: 5 }, (_, i) => ({ id: 100 + i, title: `工单 ${i}`, slaDueAt: '2026-10-09T08:00:00Z', status: 'OPEN' }))
    const { cards, hidden } = buildMessageCards([
      { name: 'get_my_expense_claims', result: JSON.stringify(listA) },
      { name: 'get_my_it_tickets', result: JSON.stringify(listB) },
    ])
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(hidden).toBe(5)
  })

  it('办理产生的单子优先显示，不会被前面查出来的长列表挤掉', () => {
    const list = Array.from({ length: 8 }, (_, i) => ({ ...expenseDraft, id: i + 1, status: 'SUBMITTED' }))
    const { cards, hidden } = buildMessageCards([
      { name: 'get_my_expense_claims', result: JSON.stringify(list) },
      { name: 'create_expense_draft', result: JSON.stringify({ ...expenseDraft, id: 99 }) },
    ])
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(cards.some((c) => c.id === 99 && c.status === 'DRAFT')).toBe(true)
    expect(hidden).toBe(4)
  })

  it('单条办理结果本身超过上限：也只显示 5 张（最后办的几张），其余计入数量', () => {
    const results = Array.from({ length: MAX_CARDS_PER_REPLY + 2 }, (_, i) => (
      { name: 'create_expense_draft', result: JSON.stringify({ ...expenseDraft, id: 200 + i }) }))
    const { cards, hidden } = buildMessageCards(results)
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(cards.map((c) => c.id)).toEqual([202, 203, 204, 205, 206])
    expect(hidden).toBe(2)
  })

  it('单独的预算卡片也占名额：预算 + 一堆办理结果合计不超过 5 张', () => {
    const results = [
      { name: 'get_expense_budget', result: '{"teamId":3,"year":2026,"remainingAmount":500}' },
      ...Array.from({ length: 6 }, (_, i) => ({ name: 'create_it_ticket', result: JSON.stringify({ id: 300 + i, title: `工单 ${i}`, slaDueAt: '2026-10-09T08:00:00Z', status: 'OPEN' }) })),
    ]
    const { cards, hidden } = buildMessageCards(results)
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(cards[0].kind).toBe('budget')
    expect(hidden).toBe(2)
  })

  it('列表里出现过、后来又被单独办理的单子，按办理结果算并保留最新状态', () => {
    const list = Array.from({ length: 6 }, (_, i) => ({ ...expenseDraft, id: i + 1 }))
    const { cards } = buildMessageCards([
      { name: 'get_my_expense_claims', result: JSON.stringify(list) },
      { name: 'submit_expense_claim', result: JSON.stringify({ ...expenseDraft, id: 6, status: 'SUBMITTED' }) },
    ])
    expect(cards).toHaveLength(MAX_CARDS_PER_REPLY)
    expect(cards.find((c) => c.id === 6)?.status).toBe('SUBMITTED')
  })

  it('请假单、工单、采购单、跟进记录都能识别', () => {
    expect(cardFromRecord({ id: 1, leaveTypeCode: 'annual', startDate: '2026-10-12', endDate: '2026-10-13', days: 2, status: 'DRAFT' })?.kind).toBe('leave')
    expect(cardFromRecord({ id: 2, title: '打印机脱机', slaDueAt: '2026-10-09T08:00:00Z', status: 'OPEN', statusLabel: '待处理' })?.kind).toBe('ticket')
    expect(cardFromRecord({ id: 3, requesterUserId: 7, lines: [{ sku: 'A4', productName: 'A4 纸', quantity: 10, unitPrice: 25 }], totalAmount: 250, status: 'DRAFT' })?.target?.section).toBe('business')
    expect(cardFromRecord({ id: 4, customerId: 9, content: '约了下周演示', status: 'DRAFT' })?.canSubmit).toBe(true)
  })

  it('请假余额不会被当成请假单', () => {
    const card = cardFromRecord({ leaveTypeCode: 'annual', leaveTypeName: '年假', remainingDays: 0, year: 2026 })!
    expect(card.kind).toBe('leave_balance')
    expect(card.fields[0]).toMatchObject({ value: '0 天', warn: true })
  })

  it('库存低于安全线：提醒并给出“起草补货申请”的下一步', () => {
    const card = cardFromRecord({ sku: 'A4-80', name: 'A4 纸', unit: '箱', unitPrice: 120, onHandQty: 3, safetyStockQty: 10, belowSafetyStock: true })!
    expect(card.kind).toBe('inventory')
    expect(card.tone).toBe('warn')
    expect(card.followUps?.[0].prompt).toContain('A4-80')
  })

  it('凭证草稿：风险逐条列出，去财务记账里核对', () => {
    const card = cardFromRecord({
      id: 5, expenseClaimId: 18, voucherDate: '2026-10-09', status: 'DRAFT', summary: '差旅费', totalAmount: 680,
      riskLevel: 'WARN', risks: [{ code: 'NO_INVOICE', level: 'WARN', message: '没有发票号' }],
      entries: [{ direction: 'D', subjectName: '管理费用-差旅费', amount: 680 }, { direction: 'C', subjectName: '其他应付款', amount: 680 }],
    })!
    expect(card.kind).toBe('voucher')
    expect(card.statusText).toBe('待核对')
    expect(card.fields.find((f) => f.label === '风险')).toMatchObject({ value: '没有发票号', warn: true })
    expect(card.target).toEqual({ section: 'business', record: { kind: 'voucher', id: 5 } })
  })

  it('入转调离预检：有阻断项时标红并指向发起办理', () => {
    const card = cardFromRecord({ checks: [{ code: 'DEVICE', level: 'BLOCK', message: '名下还有 1 台笔记本未归还' }], riskLevel: 'BLOCK', canSubmit: false })!
    expect(card.kind).toBe('hr_precheck')
    expect(card.tone).toBe('danger')
    expect(card.fields[0]).toMatchObject({ label: '必须处理', warn: true })
  })

  it('IT 自助排查：列出方案，没解决可以一键转成工单', () => {
    const card = cardFromRecord({
      classification: { category: 'HARDWARE', categoryLabel: '硬件故障', priority: 'P3', priorityLabel: '一般', reasons: [] },
      articles: [{ id: 1, title: '打印机脱机处理', steps: '…', score: 0.9 }],
    })!
    expect(card.kind).toBe('it_solution')
    expect(card.followUps?.[0].label).toContain('提交工单')
  })

  it('责任计划草稿：直接打开这份计划补全并发布', () => {
    const card = cardFromRecord({ planId: 42, status: '草稿', taskCount: 3, needSupplement: [{ seq: 2, title: 'x', problems: ['验收人'] }], unresolved: [] })!
    expect(card.kind).toBe('resp_plan')
    expect(card.target).toEqual({ section: 'collab', tab: 'plans', planId: 42 })
    expect(card.fields.find((f) => f.label === '待补全')?.value).toBe('第 2 项缺验收人')
  })

  it('商机、客户、人事任务、责任事项都能识别', () => {
    expect(cardFromRecord({ id: 1, customerId: 9, stage: 'PROPOSAL', amount: 50000, updatedAt: '2026-10-09T00:00:00Z' })?.statusText).toBe('方案报价')
    expect(cardFromRecord({ id: 9, name: '华东医美', industry: '医美', contacts: [], recentFollowUps: [], opportunities: [] })?.kind).toBe('customer')
    expect(cardFromRecord({ taskId: 3, caseId: 1, caseTypeLabel: '入职', employeeName: '李四', title: '开通邮箱', ownerLabel: 'IT', dueDate: '2026-10-10', overdue: true })?.tone).toBe('danger')
    expect(cardFromRecord({ id: 7, title: '新版首页联调', statusLabel: '执行中', responsibleName: '张三', dueDate: '2026-10-16', overdue: false })?.target).toEqual({ section: 'collab', tab: 'mine' })
  })

  it('认不出来的数据和错误结果不做卡片，错误能单独取出', () => {
    expect(cardFromRecord({ foo: 1 })).toBeNull()
    expect(buildMessageCards([{ name: 'x', result: 'not json' }]).cards).toEqual([])
    expect(toolError('{"error":"预算不足"}')).toBe('预算不足')
    expect(toolError(JSON.stringify(expenseDraft))).toBeNull()
  })
})
