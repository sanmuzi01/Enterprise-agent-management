/** 部门助手对话面板的交互：确认后卡片更新、失败步骤标红、切换部门清空、输入法回车。
 * 流式接口和确认接口用替身，按真实的事件顺序推送；卡片、步骤、小结都是面板自己渲染的真实结果。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/chat', async (importActual) => {
  const actual = await importActual<typeof import('../api/chat')>()
  return { ...actual, sendStream: vi.fn(), confirmToolCall: vi.fn(), rejectToolCall: vi.fn() }
})

import * as chatApi from '../api/chat'
import type { SseEvent } from '../api/chat'
import EmbeddedAgentChatPanel from './EmbeddedAgentChatPanel.vue'

const sendStream = vi.mocked(chatApi.sendStream)
const confirmToolCall = vi.mocked(chatApi.confirmToolCall)
const rejectToolCall = vi.mocked(chatApi.rejectToolCall)

const draft = {
  id: 18, applicantUserId: 7, teamId: 3, status: 'DRAFT', totalAmount: 680,
  lines: [{ category: 'TRAVEL', amount: 680, description: '住宿费', invoiceNo: '88001234' }], createdAt: '2026-10-09T02:00:00Z',
}
const pendingSubmit = JSON.stringify({
  status: 'confirmation_required', confirmation_token: 'tok-1', tool_name: 'submit_expense_claim',
  tool_args: { request_id: 18 }, expires_at: '', message: '',
})

/** 让替身按顺序推送一串事件；error 为真时最后抛出异常（模拟连接中断） */
function streamOf(events: Partial<SseEvent>[], error?: Error) {
  sendStream.mockImplementationOnce(async ({ onEvent }) => {
    for (const e of events) onEvent(e as SseEvent)
    if (error) throw error
  })
}

const stepStates = (w: VueWrapper) =>
  w.findAll('[data-testid=agent-steps] li').map((li) => `${li.attributes('data-state')}:${li.text().replace('→', '').trim()}`)

async function ask(w: VueWrapper, text: string) {
  await w.find('[data-testid=agent-input]').setValue(text)
  await w.find('[data-testid=agent-input]').trigger('keydown', { key: 'Enter' })
  await flushPromises()
}

let wrapper: VueWrapper
beforeEach(() => {
  vi.clearAllMocks()
  wrapper = mount(EmbeddedAgentChatPanel, { props: { agentId: 1 } })
})
afterEach(() => wrapper.unmount())

describe('确认高风险操作后', () => {
  beforeEach(() => streamOf([
    { type: 'tool_call', name: 'create_expense_draft' },
    { type: 'tool_result', name: 'create_expense_draft', result: JSON.stringify(draft) },
    { type: 'tool_call', name: 'submit_expense_claim' },
    { type: 'tool_result', name: 'submit_expense_claim', result: pendingSubmit },
    { type: 'answer', content: '已起草，请确认提交。' },
    { type: 'done', conversation_id: 5 },
  ]))

  it('执行成功：卡片立刻变成待审批，步骤打勾，小结写明已办成，通知页面刷新', async () => {
    await ask(wrapper, '报销 680 元住宿费并提交')
    expect(wrapper.find('[data-kind=expense] header').text()).toContain('草稿')
    expect(stepStates(wrapper)).toContain('confirm:提交报销')
    expect(wrapper.find('[data-testid=agent-summary-attention]').text()).toContain('等你确认后才会执行')

    confirmToolCall.mockResolvedValueOnce({ tool_name: 'submit_expense_claim', result: JSON.stringify({ ...draft, status: 'SUBMITTED' }) })
    await wrapper.find('[data-testid=agent-confirm]').trigger('click')
    await flushPromises()

    expect(confirmToolCall).toHaveBeenCalledWith('tok-1')
    expect(wrapper.find('[data-kind=expense] header').text()).toContain('待审批')
    expect(wrapper.find('[data-kind=expense] [data-testid=agent-card-submit]').exists()).toBe(false)
    expect(wrapper.find('[data-testid=agent-confirm-done]').text()).toContain('报销单 #18')
    expect(stepStates(wrapper)).toEqual(['done:理解需求', 'done:创建报销草稿', 'done:提交报销', 'done:已确认执行'])
    expect(wrapper.find('[data-testid=agent-summary-done]').text()).toContain('提交报销（报销单 #18）')
    expect(wrapper.emitted('changed')?.length).toBeGreaterThanOrEqual(2)   // 起草一次、确认提交一次
  })

  it('执行失败：确认卡片和步骤标红并写原因，卡片保持草稿', async () => {
    await ask(wrapper, '报销并提交')
    confirmToolCall.mockResolvedValueOnce({ tool_name: 'submit_expense_claim', result: '{"error":"部门报销预算不足"}' })
    await wrapper.find('[data-testid=agent-confirm]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid=agent-confirm-failed]').text()).toContain('部门报销预算不足')
    expect(stepStates(wrapper)).toContain('error:提交报销')
    expect(wrapper.find('[data-kind=expense] header').text()).toContain('草稿')
    expect(wrapper.find('[data-testid=agent-summary-failed]').text()).toBe('没有办成：提交报销：部门报销预算不足')
  })

  it('取消：标成已取消，不执行', async () => {
    await ask(wrapper, '报销并提交')
    rejectToolCall.mockResolvedValueOnce(undefined as never)
    await wrapper.findAll('[data-testid=agent-confirmation] button')[1].trigger('click')
    await flushPromises()
    expect(confirmToolCall).not.toHaveBeenCalled()
    expect(stepStates(wrapper)).toContain('skipped:提交报销（已取消）')
    expect(wrapper.find('[data-testid=agent-confirmation]').text()).toContain('已取消，没有执行')
  })
})

describe('执行失败时步骤不显示“完成”', () => {
  it('流里返回错误：正在执行的步骤标红，并写明没有办成', async () => {
    streamOf([
      { type: 'tool_call', name: 'get_expense_budget' },
      { type: 'error', message: '模型服务暂时不可用' },
    ])
    await ask(wrapper, '查一下预算')
    expect(stepStates(wrapper)).toEqual(['done:理解需求', 'error:查询报销预算'])
    expect(wrapper.find('[data-testid=agent-summary-failed]').text()).toContain('查询报销预算')
    expect(wrapper.text()).toContain('出错了：模型服务暂时不可用')
  })

  it('连接中断：没跑完的步骤标红而不是打勾', async () => {
    streamOf([{ type: 'tool_call', name: 'create_purchase_draft' }], new Error('network'))
    await ask(wrapper, '起草采购')
    expect(stepStates(wrapper)).toContain('error:创建采购草稿')
    expect(stepStates(wrapper).some((s) => s.startsWith('done:创建采购草稿'))).toBe(false)
  })

  it('工具返回错误：那一步标红并带原因，写操作不算办成', async () => {
    streamOf([
      { type: 'tool_call', name: 'create_purchase_draft' },
      { type: 'tool_result', name: 'create_purchase_draft', result: '{"error":"部门采购预算不足"}' },
      { type: 'answer', content: '预算不足，没有起草。' },
    ])
    await ask(wrapper, '起草采购')
    expect(stepStates(wrapper)).toContain('error:创建采购草稿')
    expect(wrapper.find('[data-testid=agent-summary-failed]').text()).toContain('部门采购预算不足')
    expect(wrapper.find('[data-testid=agent-summary-done]').exists()).toBe(false)
    expect(wrapper.emitted('changed')).toBeUndefined()
  })
})

describe('切换部门', () => {
  it('换了助手就清空对话，下一句话开新会话', async () => {
    streamOf([{ type: 'answer', content: '好的' }, { type: 'done', conversation_id: 42 }])
    await ask(wrapper, '你好')
    expect(wrapper.findAll('[data-testid=agent-steps]').length).toBe(1)

    await wrapper.setProps({ agentId: 2 })
    expect(wrapper.find('[data-testid=agent-steps]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('好的')

    streamOf([{ type: 'answer', content: '新部门' }])
    await ask(wrapper, '你好')
    expect(sendStream.mock.calls[1][0]).toMatchObject({ agentId: 2, conversationId: null })
  })
})

describe('中文输入法回车', () => {
  it('选词时的回车不发送，选完再按回车才发送', async () => {
    const input = wrapper.find('[data-testid=agent-input]')
    await input.setValue('报销')
    await input.trigger('keydown', { key: 'Enter', isComposing: true })
    await input.trigger('keydown', { key: 'Enter', keyCode: 229 })
    await flushPromises()
    expect(sendStream).not.toHaveBeenCalled()
    expect((input.element as HTMLInputElement).value).toBe('报销')

    streamOf([{ type: 'answer', content: '收到' }])
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(sendStream).toHaveBeenCalledTimes(1)
    expect(sendStream.mock.calls[0][0].message).toBe('报销')
  })
})
