import request from '../utils/request'

export interface FollowupTask { title: string; due_date: string | null; evidence: string }
export interface ExpenseLine { category: string; amount: string; description: string; invoice_no: string | null; evidence: string }
export type WorkKind = 'crm' | 'expense' | 'procurement' | 'leave'
export interface PurchaseItem { sku: string | null; quantity: number | null; evidence: string }
export interface Proposal {
  content?: string; evidence?: string; tasks?: FollowupTask[]; lines?: ExpenseLine[]; warnings: string[]
  items?: PurchaseItem[]; leave_type_code?: string | null; start_date?: string | null; end_date?: string | null; reason?: string
}
export interface Work {
  id: string; team_id: number; kind: WorkKind; status: string; model_name: string; sensitivity: string
  customer_id: number | null; elapsed_ms: number; total_tokens: number | null; edited: boolean
  error_message: string | null; created_at: string; proposal: Proposal | null
  business_result: { id?: number; status?: string } | null; completed_tasks: number[]; source_text?: string
}
export interface WorkStats { total: number; applied: number; ready: number; failed: number; elapsed_ms: number; total_tokens: number; edited: number }
export async function listWork(teamId: number, offset = 0) {
  return (await request.get<{ items: Work[]; stats: WorkStats }>('/enterprise/automation', { params: { team_id: teamId, offset } })).data
}
export async function getWork(id: string) { return (await request.get<Work>(`/enterprise/automation/${id}`)).data }
export async function generateWork(data: {
  team_id: number; request_key: string; kind: WorkKind; model_name: string
  source_text: string; customer_id: number | null; sensitivity: string
}) { return (await request.post<Work>('/enterprise/automation', data, { timeout: 120000 })).data }
export async function applyWork(id: string, proposal: Proposal) {
  return (await request.post<Work>(`/enterprise/automation/${id}/apply`, { proposal }, { timeout: 60000 })).data
}
export async function setTask(id: string, index: number, done: boolean) {
  return (await request.patch<Work>(`/enterprise/automation/${id}/tasks/${index}`, { done })).data
}
