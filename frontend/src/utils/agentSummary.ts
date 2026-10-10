/** 一次回复的结果小结：助手替你办成了什么、做了几步、还有什么要你处理、哪一步没成功。
 *
 * 只根据实际发生的事情（工具调用结果、待确认的操作、卡片上标出的缺项）来写，不估算“节省了多少时间”。
 */
import { cardFromRecord, parseJson, toolError, type BusinessCard, type ToolResultInput } from './agentCards'
import { toolDisplayName } from './displayNames'

export interface SummaryStep {
  name: string
  label: string
  state: 'running' | 'done' | 'error' | 'confirm' | 'skipped'
  /** 失败原因（工具返回的错误） */
  error?: string
}

export interface ReplySummary {
  /** 办成的事，例如“创建报销草稿（报销单 #18）” */
  done: string[]
  /** 助手替你完成的步骤数（查询、起草、提交……，不含“理解需求”） */
  stepCount: number
  /** 需要你处理的事：待确认的操作、等你提交的草稿、卡片上标出的缺项 */
  attention: string[]
  /** 没成功的步骤和原因 */
  failed: string[]
}

const KIND_NOUN: Partial<Record<BusinessCard['kind'], string>> = {
  expense: '报销单', leave: '请假单', purchase: '采购申请', followup: '跟进记录', ticket: '工单',
  voucher: '凭证', hr_case: '人事事项', resp_plan: '责任计划', opportunity: '商机',
}

// 会改业务数据的工具：办成了才算“完成了一件事”；只读查询只算步骤
const WRITE_PREFIXES = ['create_', 'submit_', 'approve_', 'reject_', 'update_', 'confirm_', 'accept_', 'cancel_',
  'generate_', 'record_', 'report_', 'take_', 'resolve_', 'complete_', 'reopen_', 'extract_', 'verify_', 'request_', 'raise_']
export const isWriteTool = (name: string) => WRITE_PREFIXES.some((p) => name.startsWith(p))

const INTERNAL_STEPS = new Set(['understand', 'wait'])

function recordRef(result: string): string {
  const card = cardFromRecord(parseJson(result))
  if (!card) return ''
  const noun = KIND_NOUN[card.kind]
  return noun && card.id != null ? `${noun} #${card.id}` : ''
}

export function summarizeReply(input: {
  steps: SummaryStep[]
  results: ToolResultInput[]
  pendingConfirmations: string[]
  cards: BusinessCard[]
}): ReplySummary {
  const done: string[] = []
  for (const { name, result } of input.results) {
    if (!isWriteTool(name) || toolError(result)) continue
    const ref = recordRef(result)
    done.push(ref ? `${toolDisplayName(name)}（${ref}）` : toolDisplayName(name))
  }
  const stepCount = input.steps.filter((s) => !INTERNAL_STEPS.has(s.name) && s.state === 'done').length
  const failed = input.steps
    .filter((s) => s.state === 'error' && !INTERNAL_STEPS.has(s.name))   // “等待你确认”失败只是前面那一步失败的结果，不重复列
    .map((s) => (s.error ? `${s.label}：${s.error}` : s.label))

  const attention: string[] = input.pendingConfirmations.map((name) => `${toolDisplayName(name)}：等你确认后才会执行`)
  const drafts = input.cards.filter((c) => c.canSubmit && c.status === 'DRAFT')
  if (drafts.length) attention.push(`${drafts.length} 张草稿等你确认提交`)
  for (const card of input.cards) {
    for (const f of card.fields) if (f.warn) attention.push(`${f.label}：${f.value}`)
  }
  return { done: [...new Set(done)], stepCount, attention: [...new Set(attention)].slice(0, 5), failed }
}
