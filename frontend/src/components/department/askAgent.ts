import { inject, nextTick, watch, type InjectionKey, type Ref } from 'vue'

/** 业务模块把一条记录交给部门助手分析（“让助手分析”按钮）。
 * 部门工作台提供；不在工作台里（或部门助手没发布）时拿到的是 null，模块就不显示这个按钮。 */
export type AskDeptAgent = (prompt: string) => void

export const ASK_DEPT_AGENT: InjectionKey<AskDeptAgent | null> = Symbol('askDeptAgent')

export function useAskDeptAgent(): AskDeptAgent | null {
  return inject(ASK_DEPT_AGENT, null)
}

/** 助手回复里的业务卡片 → 工作台里对应的那条记录（打开详情或高亮那一行）。
 * 工作台切好分区后广播一次；正在显示、认识这种记录的模块自己处理。nonce 让同一条记录可以被再次聚焦。 */
export interface FocusRequest {
  kind: string
  id: number
  nonce: number
}

export const FOCUS_RECORD: InjectionKey<Ref<FocusRequest | null>> = Symbol('focusRecord')

/** 模块里登记“我能打开哪些记录”。模块是切分区后才挂载的，所以挂载时也看一眼当前的聚焦请求。 */
export function useFocusRecord(kinds: string[], handler: (id: number, kind: string) => void) {
  const focus = inject(FOCUS_RECORD, null)
  if (!focus) return
  watch(focus, (request) => {
    if (request && kinds.includes(request.kind)) handler(request.id, request.kind)
  }, { immediate: true })
}

/** 把某一行滚到眼前并闪一下。列表可能还在加载，最多等两秒。 */
export async function flashRecord(selector: string) {
  for (let i = 0; i < 20; i++) {
    await nextTick()
    const el = document.querySelector<HTMLElement>(selector)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('ring-2', 'ring-indigo-400', 'ring-inset')
      setTimeout(() => el.classList.remove('ring-2', 'ring-indigo-400', 'ring-inset'), 2500)
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}
