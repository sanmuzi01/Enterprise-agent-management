import request from '../utils/request'

export type WorkKind = string
export type Proposal = Record<string, any> & { warnings: string[] }

export interface FormField {
  type: 'text' | 'textarea' | 'date' | 'select' | 'integer' | 'money' | 'evidence' | 'list' | 'note' | 'sum'
  key?: string
  label?: string
  max?: number
  min?: number
  rows?: number
  required?: boolean
  nullable?: boolean
  placeholder?: string
  wide?: boolean
  options?: { value: string; label: string }[]
  options_from?: 'members' | 'reviewers'
  item?: string
  fields?: FormField[]
  unique_by?: string
  unique_message?: string
  text?: string
  list?: string
  field?: string
}
export interface FormRule { type: 'date_order'; start: string; end: string; message: string }
export interface WorkflowInfo {
  id: string; title: string; name: string; draft_name: string
  source_label: string; example: string; hint: string
  form: FormField[]; rules: FormRule[]; needs_customer: boolean
  followups: { key: string; title: string; due: string } | null
  available: boolean; availability: string
}
export interface WorkflowCatalog { workflows: WorkflowInfo[]; default_id: string | null }

export interface BusinessCheck { level: 'info' | 'warning' | 'blocker'; text: string }
export interface Work {
  business_checks?: BusinessCheck[]
  id: string; team_id: number; kind: WorkKind; status: string; model_name: string; sensitivity: string
  customer_id: number | null; elapsed_ms: number; total_tokens: number | null; edited: boolean
  error_message: string | null; created_at: string; proposal: Proposal | null
  business_result: { id?: number; status?: string; title?: string; taskCount?: number } | null; completed_tasks: number[]; source_text?: string
}
export interface WorkStats {
  total: number; applied: number; ready: number; failed: number; elapsed_ms: number; total_tokens: number; edited: number
  by_kind?: Record<string, { total: number; applied: number; failed: number; edited: number }>
}

export async function getWorkflows(teamId: number) {
  return (await request.get<WorkflowCatalog>('/enterprise/automation/workflows', { params: { team_id: teamId } })).data
}
export async function listWork(teamId: number, offset = 0) {
  return (await request.get<{ items: Work[]; stats: WorkStats }>('/enterprise/automation', { params: { team_id: teamId, offset } })).data
}
export async function getWork(id: string) { return (await request.get<Work>(`/enterprise/automation/${id}`)).data }
export async function generateWork(data: {
  team_id: number; request_key: string; kind: WorkKind; model_name: string
  source_text: string; customer_id: number | null; sensitivity: string
}) { return (await request.post<Work>('/enterprise/automation', data, { timeout: 120000 })).data }
export interface ImportedText {
  text: string; file_name: string; file_type: string; label: string; chars: number; pages: number | null
  ocr: boolean; warnings: string[]; attachments: string[]
}
/** 把 PDF / Word / Excel / 邮件 / 图片 / 文本文件提取成文字（服务端不保存文件）。 */
export async function importFile(teamId: number, file: File, sensitivity: string) {
  const form = new FormData()
  form.append('team_id', String(teamId))
  form.append('sensitivity', sensitivity)
  form.append('file', file)
  return (await request.post<ImportedText>('/enterprise/automation/import', form, { timeout: 90000, headers: { 'Content-Type': 'multipart/form-data' } })).data
}
export interface BatchItem extends Work { batch_name: string | null; batch_index: number | null }
export interface Batch {
  batch_id: string; kind: string; team_id: number; model_name: string; sensitivity: string; created_at: string
  total: number; done: number; finished: boolean
  counts: Record<'queued' | 'processing' | 'ready' | 'applying' | 'retry' | 'applied' | 'failed', number>
  items: BatchItem[]
}
export interface BatchBrief { batch_id: string; kind: string; created_at: string; total: number; active: number; failed: number; finished: boolean }
/** 批量整理：立即返回（材料排队中），整理在后台进行；batch_id 由客户端生成，重复提交不会重复整理。 */
export async function createBatch(data: {
  batch_id: string; team_id: number; kind: string; model_name: string; sensitivity: string; items: { name: string; text: string }[]
}) { return (await request.post<Batch>('/enterprise/automation/batches', data, { timeout: 30000 })).data }
export async function getBatch(id: string) { return (await request.get<Batch>(`/enterprise/automation/batches/${id}`, { skipErrorToast: true })).data }
export async function listBatches(teamId: number) {
  return (await request.get<BatchBrief[]>('/enterprise/automation/batches', { params: { team_id: teamId }, skipErrorToast: true })).data
}
export async function retryBatchItem(batchId: string, workId: string) {
  return (await request.post<Batch>(`/enterprise/automation/batches/${batchId}/items/${workId}/retry`)).data
}
export async function applyWork(id: string, proposal: Proposal) {
  return (await request.post<Work>(`/enterprise/automation/${id}/apply`, { proposal }, { timeout: 60000 })).data
}
export async function recheckWork(id: string, proposal: Proposal) {
  return (await request.post<Work>(`/enterprise/automation/${id}/checks`, { proposal }, { timeout: 60000 })).data
}
export async function setTask(id: string, index: number, done: boolean) {
  return (await request.patch<Work>(`/enterprise/automation/${id}/tasks/${index}`, { done })).data
}

const isBlank = (v: unknown) => v === null || v === undefined || String(v).trim() === ''

/** 按工作流声明做保存前检查；服务端仍会做权威校验。返回空字符串表示可以保存。 */
export function validateDraft(form: FormField[], rules: FormRule[], draft: Record<string, any>): string {
  const missing: string[] = []
  for (const f of form) {
    if (f.type === 'list' && f.key) {
      const items: Record<string, any>[] = draft[f.key] || []
      if (items.length < (f.min || 0)) return `请至少保留一条${f.item}。`
      for (const [i, item] of items.entries()) {
        const blank = (f.fields || []).filter(s => s.required && isBlank(item[s.key!])).map(s => s.label)
        if (blank.length) return `请补全第 ${i + 1} 条${f.item}的${blank.join('、')}。`
        for (const s of f.fields || []) {
          const err = rangeError(s, item[s.key!])
          if (err) return err
        }
      }
      if (f.unique_by) {
        const values = items.map(it => String(it[f.unique_by!] ?? '').trim())
        if (new Set(values).size !== values.length) return f.unique_message || '存在重复内容。'
      }
    } else if (f.key && f.required && isBlank(draft[f.key])) {
      missing.push(f.label || f.key)
    } else if (f.key) {
      const err = rangeError(f, draft[f.key])
      if (err) return err
    }
  }
  if (missing.length) return `请补全${missing.join('、')}。`
  for (const rule of rules) {
    if (rule.type === 'date_order' && draft[rule.start] && draft[rule.end] && draft[rule.start] > draft[rule.end]) return rule.message
  }
  return ''
}

function rangeError(f: FormField, value: unknown): string {
  if (isBlank(value)) return ''
  const n = Number(value)
  if (f.type === 'integer' && (!Number.isInteger(n) || (f.min !== undefined && n < f.min) || (f.max !== undefined && n > f.max)))
    return `${f.label}需为 ${f.min ?? 1} 至 ${(f.max ?? Number.MAX_SAFE_INTEGER).toLocaleString()} 的整数。`
  if (f.type === 'money' && !(n > 0)) return `${f.label}必须大于 0。`
  return ''
}

/** 空字符串的日期/可空字段转成 null，与服务端 schema 对齐。 */
export function normalizeDraft(form: FormField[], draft: Record<string, any>): Proposal {
  const copy = JSON.parse(JSON.stringify(draft))
  const fix = (fields: FormField[], target: Record<string, any>) => {
    for (const f of fields) {
      if (f.type === 'list' && f.key) (target[f.key] || []).forEach((it: Record<string, any>) => fix(f.fields || [], it))
      else if (f.key && (f.type === 'date' || f.nullable) && isBlank(target[f.key])) target[f.key] = null
    }
  }
  fix(form, copy)
  return copy
}
