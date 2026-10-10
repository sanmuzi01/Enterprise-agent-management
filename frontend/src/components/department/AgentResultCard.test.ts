/** 业务卡片上的按钮：草稿“确认提交”按单据类型调对应的业务接口、成功后卡片状态立刻变、失败写原因；
 * “去修改 / 在工作台中查看”带上要打开的那条记录；“下一步”按钮把话交给助手。 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/departmentFinance', () => ({ submitMyExpenseClaim: vi.fn() }))
vi.mock('../../api/departmentLeave', () => ({ submitMyLeaveRequest: vi.fn() }))
vi.mock('../../api/departmentProcurement', () => ({ submitMyPurchaseRequest: vi.fn() }))
vi.mock('../../api/departmentCrm', () => ({ confirmFollowup: vi.fn() }))

import { submitMyExpenseClaim } from '../../api/departmentFinance'
import { submitMyLeaveRequest } from '../../api/departmentLeave'
import { submitMyPurchaseRequest } from '../../api/departmentProcurement'
import { confirmFollowup } from '../../api/departmentCrm'
import { cardFromRecord } from '../../utils/agentCards'
import AgentResultCard from './AgentResultCard.vue'

const expense = cardFromRecord({ id: 18, applicantUserId: 7, teamId: 3, status: 'DRAFT', totalAmount: 680,
  lines: [{ category: 'TRAVEL', amount: 680, invoiceNo: 'A1' }], createdAt: '2026-10-09T02:00:00Z' })!

beforeEach(() => vi.clearAllMocks())

describe('草稿确认提交', () => {
  it.each([
    ['报销', expense, submitMyExpenseClaim],
    ['请假', cardFromRecord({ id: 5, leaveTypeCode: 'annual', startDate: '2026-10-12', endDate: '2026-10-12', days: 1, status: 'DRAFT' })!, submitMyLeaveRequest],
    ['采购', cardFromRecord({ id: 6, requesterUserId: 7, lines: [{ sku: 'A4', quantity: 1 }], totalAmount: 25, status: 'DRAFT' })!, submitMyPurchaseRequest],
    ['客户跟进', cardFromRecord({ id: 8, customerId: 9, content: '约了演示', status: 'DRAFT' })!, confirmFollowup],
  ])('%s：调对应的接口，成功后卡片立刻变成新状态、按钮消失、通知刷新', async (_label, card, api) => {
    vi.mocked(api as (id: number) => Promise<{ status: string }>).mockResolvedValue({ status: card.kind === 'followup' ? 'CONFIRMED' : 'SUBMITTED' })
    const w = mount(AgentResultCard, { props: { card } })
    await w.find('[data-testid=agent-card-submit]').trigger('click')
    await flushPromises()
    expect(api).toHaveBeenCalledWith(card.id)
    expect(w.find('header').text()).toContain(card.kind === 'followup' ? '已记录' : '待审批')
    expect(w.find('[data-testid=agent-card-submit]').exists()).toBe(false)
    expect(w.emitted('changed')).toHaveLength(1)
  })

  it('提交失败：写明原因，卡片仍是草稿、按钮还在，可以改了再提交', async () => {
    vi.mocked(submitMyExpenseClaim).mockRejectedValue({ response: { status: 400, data: { detail: '部门报销预算不足' } } })
    const w = mount(AgentResultCard, { props: { card: expense } })
    await w.find('[data-testid=agent-card-submit]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('部门报销预算不足')
    expect(w.find('header').text()).toContain('草稿')
    expect(w.find('[data-testid=agent-card-submit]').exists()).toBe(true)
    expect(w.emitted('changed')).toBeUndefined()
  })

  it('已提交的单子没有提交按钮', () => {
    const w = mount(AgentResultCard, { props: { card: { ...expense, status: 'SUBMITTED', canSubmit: false } } })
    expect(w.find('[data-testid=agent-card-submit]').exists()).toBe(false)
  })
})

describe('其它按钮', () => {
  it('“去修改”带上要打开的那条记录', async () => {
    const w = mount(AgentResultCard, { props: { card: expense } })
    await w.find('[data-testid=agent-card-open]').trigger('click')
    expect(w.emitted('open')?.[0]).toEqual([{ section: 'office', record: { kind: 'expense', id: 18 } }])
  })

  it('“下一步”按钮把配好的话交给助手', async () => {
    const card = cardFromRecord({ sku: 'A4-80', name: 'A4 纸', unit: '箱', unitPrice: 120, onHandQty: 3, safetyStockQty: 10, belowSafetyStock: true })!
    const w = mount(AgentResultCard, { props: { card } })
    await w.find('[data-testid=agent-card-followup]').trigger('click')
    expect(w.emitted('ask')?.[0][0]).toContain('A4-80')
  })
})
