import { describe, expect, it } from 'vitest'
import { isImeEnter } from './ime'

const key = (init: KeyboardEventInit & { keyCode?: number }) => {
  const event = new KeyboardEvent('keydown', { key: 'Enter', ...init })
  if (init.keyCode !== undefined) Object.defineProperty(event, 'keyCode', { value: init.keyCode })
  return event
}

describe('isImeEnter', () => {
  it('输入法选词时的回车（isComposing 或 keyCode 229）不算提交', () => {
    expect(isImeEnter(key({ isComposing: true }))).toBe(true)
    expect(isImeEnter(key({ keyCode: 229 }))).toBe(true)
    expect(isImeEnter(key({ keyCode: 13 }))).toBe(false)
  })
})

// 守卫：所有“回车提交”的输入框都要先判断输入法，不然拼音打到一半按回车选词就会把内容发出去。
// 新加输入框忘了判断，这里会失败并指出是哪个文件。
const sources = import.meta.glob('../**/*.vue', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

describe('回车提交的输入框都忽略输入法选词', () => {
  it('不用 keyup.enter（选词后的 keyup 已经判断不出输入法状态）', () => {
    const offenders = Object.entries(sources).filter(([, src]) => /@keyup\.enter/.test(src)).map(([file]) => file)
    expect(offenders).toEqual([])
  })

  it('每个 keydown.enter 都经过 isImeEnter', () => {
    const offenders: string[] = []
    for (const [file, src] of Object.entries(sources)) {
      for (const match of src.matchAll(/@keydown\.enter(?:\.[a-z]+)*="([^"]*)"/g)) {
        const handler = match[1]
        const guarded = handler.includes('isImeEnter')
          // 处理函数自己判断的（如对话页的 onComposerEnter），函数体里要能找到 isImeEnter
          || new RegExp(`const ${handler}\\s*=\\s*\\([^)]*\\)[^{]*\\{[^}]*isImeEnter`).test(src)
        if (!guarded) offenders.push(`${file}: ${match[0]}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
