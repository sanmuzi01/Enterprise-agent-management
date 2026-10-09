/** 部门工作台顶部的部门助手：今日发现、一键处理（成功 / 部分失败 / 全部失败 / 没有可处理的）、切换部门清空输入。
 * 业务接口用替身；对话面板只是占位（它的交互在 EmbeddedAgentChatPanel.test.ts 里单独测）。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/financeVouchers', () => ({ listUnbooked: vi.fn(), generateFromClaim: vi.fn() }))
vi.mock('../../api/itService', () => ({ deskTickets: vi.fn(), deskAssign: vi.fn() }))

import { generateFromClaim, listUnbooked } from '../../api/financeVouchers'
import { deskAssign, deskTickets } from '../../api/itService'
import type { HomeCard, WorkspaceAgent } from '../../api/enterpriseWorkspace'
import DepartmentAgentHub from './DepartmentAgentHub.vue'

const agent: WorkspaceAgent = {
  id: 9, name: '财务报销助手', agent_type: 'department', department_code: 'finance', team_id: 3, team_name: '财务部',
  model_name: 'glm-4', model_configured: true, description: '报销与记账', examples: ['查询本部门今年的报销预算'], skill_count: 1,
}
const card = (key: string, value: number, tone: HomeCard['tone']): HomeCard => ({ key, label: key, value, hint: '', section: 'business', tone })
const cards = [card('unbooked', 2, 'warn'), card('it_unassigned', 3, 'warn'), card('it_overdue', 1, 'danger'), card('approvals', 1, 'warn')]
const httpError = (detail: string) => Object.assign(new Error(detail), { response: { status: 400, data: { detail } } })

let wrapper: VueWrapper
function mountHub(props: Record<string, unknown> = {}) {
  wrapper = mount(DepartmentAgentHub, {
    props: { agent, centralAgent: null, cards, departmentCode: 'finance', departmentName: '财务部', isHead: true, teamId: 3, ...props },
    global: { stubs: { RouterLink: true, EmbeddedAgentChatPanel: true } },
  })
  return wrapper
}
const quickResult = () => wrapper.find('[data-testid=agent-quick-result]')

beforeEach(() => vi.clearAllMocks())
afterEach(() => wrapper?.unmount())

describe('今日发现', () => {
  it('写明发现几件事、几件有风险；能一键处理的显示处理按钮', () => {
    mountHub()
    expect(wrapper.find('[data-testid=agent-found]').text()).toBe('今天发现 4 件需要处理的事，其中 1 件已逾期或有风险。')
    expect(wrapper.find('[data-testid=suggestion-quick-unbooked]').text()).toBe('一键生成凭证草稿')
    expect(wrapper.find('[data-testid=suggestion-quick-it_unassigned]').text()).toBe('接最急的一张')
    expect(wrapper.find('[data-testid=suggestion-ask-approvals]').exists()).toBe(true)
  })
})

describe('一键生成凭证草稿', () => {
  beforeEach(() => vi.mocked(listUnbooked).mockResolvedValue([{ id: 1 }, { id: 2 }] as never))

  it('全部成功：写明生成了几笔，刷新业务并跳到财务记账', async () => {
    vi.mocked(generateFromClaim).mockResolvedValue({} as never)
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-unbooked]').trigger('click')
    await flushPromises()
    expect(generateFromClaim).toHaveBeenCalledTimes(2)
    expect(quickResult().text()).toBe('已为 2 笔报销生成凭证草稿，请到「财务记账」逐张核对后入账')
    expect(quickResult().classes()).toContain('bg-emerald-50')
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(wrapper.emitted('open')?.[0]).toEqual([{ section: 'business' }])
  })

  it('部分失败：成功的照常生成，失败的逐笔写明原因', async () => {
    vi.mocked(generateFromClaim).mockResolvedValueOnce({} as never).mockRejectedValueOnce(httpError('报销单已作废'))
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-unbooked]').trigger('click')
    await flushPromises()
    expect(quickResult().text()).toContain('已为 1 笔报销生成凭证草稿；1 笔没有成功')
    expect(quickResult().text()).toContain('报销单 #2')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('全部失败：标红，不刷新也不跳转', async () => {
    vi.mocked(generateFromClaim).mockRejectedValue(httpError('业务服务暂时不可用'))
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-unbooked]').trigger('click')
    await flushPromises()
    expect(quickResult().text()).toContain('2 笔没有成功')
    expect(quickResult().classes()).toContain('bg-red-50')
    expect(wrapper.emitted('changed')).toBeUndefined()
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('处理期间按钮不能重复点', async () => {
    let release: () => void = () => {}
    vi.mocked(generateFromClaim).mockImplementation(() => new Promise((r) => { release = () => r({} as never) }))
    mountHub()
    const button = wrapper.find('[data-testid=suggestion-quick-unbooked]')
    await button.trigger('click')
    await flushPromises()
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(listUnbooked).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    release()
    await flushPromises()
  })
})

describe('接最急的一张工单', () => {
  it('接下队列里最急的一张，并直接打开它', async () => {
    vi.mocked(deskTickets).mockResolvedValue([{ id: 7, title: '打印机脱机' }, { id: 8, title: '不急' }] as never)
    vi.mocked(deskAssign).mockResolvedValue({} as never)
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-it_unassigned]').trigger('click')
    await flushPromises()
    expect(deskTickets).toHaveBeenCalledWith(3, { assignee: 'unassigned' })
    expect(deskAssign).toHaveBeenCalledWith(3, 7, { take: true })
    expect(quickResult().text()).toBe('已接单：工单 #7「打印机脱机」，已为你打开')
    expect(wrapper.emitted('open')?.[0]).toEqual([{ section: 'business', record: { kind: 'ticket', id: 7 } }])
  })

  it('没有待接单的工单：如实告知，不调用接单', async () => {
    vi.mocked(deskTickets).mockResolvedValue([] as never)
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-it_unassigned]').trigger('click')
    await flushPromises()
    expect(deskAssign).not.toHaveBeenCalled()
    expect(quickResult().text()).toBe('现在没有待接单的工单了')
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('服务端拒绝（不是 IT 部门）：标红并写明原因', async () => {
    vi.mocked(deskTickets).mockRejectedValue(httpError('IT 服务台仅对IT部门开放'))
    mountHub()
    await wrapper.find('[data-testid=suggestion-quick-it_unassigned]').trigger('click')
    await flushPromises()
    expect(quickResult().classes()).toContain('bg-red-50')
    expect(quickResult().text()).toContain('IT 服务台仅对IT部门开放')
  })
})

describe('切换部门', () => {
  const inputValue = () => (wrapper.find('[data-testid=agent-flagship-input]').element as HTMLTextAreaElement).value

  it('同一种业务类型的两个部门之间切换（销售一部 → 销售二部）：材料和一键处理结果都清空', async () => {
    vi.mocked(deskTickets).mockResolvedValue([] as never)
    mountHub({ teamId: 3, departmentCode: 'sales', departmentName: '销售一部' })
    await wrapper.find('[data-testid=agent-flagship-input]').setValue('今天和华东医美王总通话，确认采购 20 台')
    await wrapper.find('[data-testid=suggestion-quick-it_unassigned]').trigger('click')
    await flushPromises()
    expect(quickResult().exists()).toBe(true)

    await wrapper.setProps({ teamId: 4, departmentCode: 'sales', departmentName: '销售二部' })
    expect(inputValue()).toBe('')
    expect(quickResult().exists()).toBe(false)
  })

  it('不同业务类型的部门之间切换：招牌场景换成新部门的', async () => {
    mountHub()
    expect(wrapper.find('[data-testid=agent-flagship]').text()).toContain('报销')
    await wrapper.find('[data-testid=agent-flagship-input]').setValue('上海出差住宿 680 元')
    await wrapper.setProps({ teamId: 5, departmentCode: 'it', departmentName: 'IT 部' })
    expect(wrapper.find('[data-testid=agent-flagship]').text()).toContain('故障')
    expect(inputValue()).toBe('')
  })

  it('批量生成凭证途中切走：不再对旧部门继续生成，也不把结果显示到新部门', async () => {
    vi.mocked(listUnbooked).mockResolvedValue([{ id: 1 }, { id: 2 }, { id: 3 }] as never)
    let release: () => void = () => {}
    vi.mocked(generateFromClaim).mockImplementationOnce(() => new Promise((r) => { release = () => r({} as never) }))
      .mockResolvedValue({} as never)
    mountHub({ teamId: 3, departmentCode: 'finance' })
    await wrapper.find('[data-testid=suggestion-quick-unbooked]').trigger('click')
    await flushPromises()
    expect(generateFromClaim).toHaveBeenCalledWith(3, 1)

    await wrapper.setProps({ teamId: 4, departmentName: '财务二部' })
    expect(wrapper.find('[data-testid=suggestion-quick-unbooked]').attributes('disabled')).toBeUndefined()   // 新部门里可以立刻再点
    release()
    await flushPromises()

    expect(generateFromClaim).toHaveBeenCalledTimes(1)                       // 第 2、3 笔不再处理
    expect(vi.mocked(generateFromClaim).mock.calls.every(([team]) => team === 3)).toBe(true)   // 从没用新部门去调旧部门的单子
    expect(quickResult().exists()).toBe(false)
    expect(wrapper.emitted('changed')).toBeUndefined()
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('接单途中切走：结果不显示到新部门，也不跳转', async () => {
    vi.mocked(deskTickets).mockResolvedValue([{ id: 7, title: '打印机脱机' }] as never)
    let release: () => void = () => {}
    vi.mocked(deskAssign).mockImplementation(() => new Promise((r) => { release = () => r({} as never) }))
    mountHub({ teamId: 3 })
    await wrapper.find('[data-testid=suggestion-quick-it_unassigned]').trigger('click')
    await flushPromises()
    expect(deskAssign).toHaveBeenCalledWith(3, 7, { take: true })

    await wrapper.setProps({ teamId: 4 })
    release()
    await flushPromises()
    expect(quickResult().exists()).toBe(false)
    expect(wrapper.emitted('open')).toBeUndefined()
  })
})
