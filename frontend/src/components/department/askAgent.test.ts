/** 卡片“在工作台中查看”直达那条记录：工作台广播一次，正在显示的、认识这种记录的模块打开它；
 * 模块是切分区后才挂载的，挂载时也要能拿到刚发出的请求；不认识的记录类型不处理。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, provide, ref } from 'vue'
import { FOCUS_RECORD, flashRecord, useFocusRecord, type FocusRequest } from './askAgent'

function harness(kinds: string[], handler: (id: number, kind: string) => void, initial: FocusRequest | null = null) {
  const focus = ref<FocusRequest | null>(initial)
  const Module = defineComponent({ setup() { useFocusRecord(kinds, handler); return () => h('div') } })
  const show = ref(false)
  const Page = defineComponent({
    setup() {
      provide(FOCUS_RECORD, focus)
      return () => h('div', show.value ? [h(Module)] : [])
    },
  })
  return { wrapper: mount(Page), focus, show }
}

describe('useFocusRecord', () => {
  it('模块已经显示：收到自己认识的记录就打开', async () => {
    const handler = vi.fn()
    const { focus, show } = harness(['ticket'], handler)
    show.value = true
    await nextTick()
    focus.value = { kind: 'ticket', id: 7, nonce: 1 }
    await nextTick()
    expect(handler).toHaveBeenCalledWith(7, 'ticket')
  })

  it('切分区后才挂载的模块：挂载时拿到刚发出的请求', async () => {
    const handler = vi.fn()
    const { show } = harness(['voucher'], handler, { kind: 'voucher', id: 5, nonce: 1 })
    show.value = true
    await nextTick()
    expect(handler).toHaveBeenCalledWith(5, 'voucher')
  })

  it('不认识的记录类型不处理；同一条记录再点一次（新的 nonce）会再打开', async () => {
    const handler = vi.fn()
    const { focus, show } = harness(['expense'], handler)
    show.value = true
    await nextTick()
    focus.value = { kind: 'ticket', id: 7, nonce: 1 }
    await nextTick()
    expect(handler).not.toHaveBeenCalled()
    focus.value = { kind: 'expense', id: 18, nonce: 2 }
    await nextTick()
    focus.value = { kind: 'expense', id: 18, nonce: 3 }
    await nextTick()
    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('不在部门工作台里（没有提供广播）时什么也不做', () => {
    const handler = vi.fn()
    mount(defineComponent({ setup() { useFocusRecord(['ticket'], handler); return () => h('div') } }))
    expect(handler).not.toHaveBeenCalled()
  })
})

describe('flashRecord', () => {
  it('列表还在加载时等它出现，再滚到眼前并高亮', async () => {
    vi.useFakeTimers()
    const scroll = vi.fn()
    const done = flashRecord('[data-record="expense-18"]')
    await vi.advanceTimersByTimeAsync(250)   // 列表还没渲染
    const row = document.createElement('li')
    row.dataset.record = 'expense-18'
    row.scrollIntoView = scroll
    document.body.appendChild(row)
    await vi.advanceTimersByTimeAsync(200)
    await done
    expect(scroll).toHaveBeenCalled()
    expect(row.classList.contains('ring-2')).toBe(true)
    await vi.advanceTimersByTimeAsync(3000)
    expect(row.classList.contains('ring-2')).toBe(false)
    row.remove()
    vi.useRealTimers()
  })
})
