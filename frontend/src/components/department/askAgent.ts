import { inject, type InjectionKey } from 'vue'

/** 业务模块把一条记录交给部门助手分析（“让助手分析”按钮）。
 * 部门工作台提供；不在工作台里（或部门助手没发布）时拿到的是 null，模块就不显示这个按钮。 */
export type AskDeptAgent = (prompt: string) => void

export const ASK_DEPT_AGENT: InjectionKey<AskDeptAgent | null> = Symbol('askDeptAgent')

export function useAskDeptAgent(): AskDeptAgent | null {
  return inject(ASK_DEPT_AGENT, null)
}
