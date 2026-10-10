import request from '../utils/request'

export interface InvoiceCheck { level: 'error' | 'warning' | 'info'; code: string; text: string }

export interface InvoiceExtraction {
  id: number
  file_name: string | null
  status: 'needs_review' | 'confirmed' | 'used' | 'discarded'
  method: 'text' | 'ocr' | null
  fields: Record<string, string | null>
  confidence: Record<string, number>
  overall_confidence: number | null
  needs_review: string[]
  checks: InvoiceCheck[]
  corrected_fields: string[]
  labels: Record<string, string>
  claim_id: number | null
}

export interface PolicyCheck {
  index: number
  rule_id: number | null
  status: 'ok' | 'over_limit' | 'missing_receipt'
  limit: string | null
  over_by: string | null
  approval_level: string | null
  message: string
}

export interface PolicyRule {
  id: number
  category: string
  category_label: string
  city_level: string | null
  employee_level: string | null
  amount_limit: string | null
  receipt_required: boolean
  approval_level: string
  approval_label: string
  effective_from: string | null
  effective_to: string | null
  note: string | null
}

export interface PolicyRuleForm {
  category: string
  city_level: string | null
  employee_level: string | null
  amount_limit: string | null
  receipt_required: boolean
  approval_level: string
  effective_from: string | null
  effective_to: string | null
  note: string | null
}

export interface SelfServiceArticle { id: number; title: string; steps: string; category?: string }

export interface SelfServiceSession {
  id: number
  question: string
  classification: string | null
  classification_label: string | null
  articles: SelfServiceArticle[]
  confirmed_solved: boolean | null
  converted_ticket_id: number | null
  classification_detail?: { category: string; priority: string; categoryLabel?: string; priorityLabel?: string; reasons?: string[] }
}

export interface ArticleStat {
  id: number
  title: string | null
  recommended: number
  viewed: number
  solved: number
  reopened: number
  helpful: number
  unhelpful: number
  solve_rate: number | null
  helpful_rate: number | null
  reasons?: string[]
}

export interface SelfServiceMetrics {
  days: number
  sessions: number
  recommendations: number
  sessions_with_recommendation: number
  confirmed_solved: number
  converted_to_ticket: number
  no_feedback: number
  self_solve_rate: number | null
  reopen_rate: number | null
  articles: ArticleStat[]
  maintenance: ArticleStat[]
}

export async function extractInvoice(teamId: number, file: File): Promise<InvoiceExtraction> {
  const form = new FormData()
  form.append('team_id', String(teamId))
  form.append('file', file)
  const { data } = await request.post('/enterprise/finance/invoices/extract', form, { timeout: 120000 })
  return data
}

export async function confirmInvoice(id: number, values: Record<string, string | null>, confirmedFields: string[]): Promise<InvoiceExtraction> {
  const { data } = await request.post(`/enterprise/finance/invoices/${id}/confirm`, { values, confirmed_fields: confirmedFields })
  return data
}

export async function discardInvoice(id: number): Promise<InvoiceExtraction> {
  const { data } = await request.post(`/enterprise/finance/invoices/${id}/discard`)
  return data
}

export async function checkPolicy(teamId: number, lines: { category: string; amount: number; invoice_no?: string }[],
  cityLevel?: string | null): Promise<PolicyCheck[]> {
  const { data } = await request.post('/enterprise/finance/policy/check', { team_id: teamId, lines, city_level: cityLevel || null })
  return data
}

export async function listPolicies(): Promise<{ rules: PolicyRule[]; categories: Record<string, string>;
  city_levels: Record<string, string>; employee_levels: Record<string, string>; approval_levels: Record<string, string> }> {
  const { data } = await request.get('/admin/expense-policies')
  return data
}

export async function savePolicy(form: PolicyRuleForm, id?: number): Promise<PolicyRule> {
  const { data } = id ? await request.put(`/admin/expense-policies/${id}`, form) : await request.post('/admin/expense-policies', form)
  return data
}

export async function deletePolicy(id: number) {
  await request.delete(`/admin/expense-policies/${id}`)
}

export async function erpExport(teamId: number, voucherId: number) {
  const { data } = await request.post(`/enterprise/finance/vouchers/${voucherId}/erp-export`, { team_id: teamId })
  return data as { status: string; erp_document_id: string | null; duplicate: boolean }
}

export async function erpExportStatus(teamId: number, voucherId: number) {
  const { data } = await request.get(`/enterprise/finance/vouchers/${voucherId}/erp-export`, { params: { team_id: teamId } })
  return data as { configured: boolean; export: { status: string; erp_document_id: string | null; last_error: string | null } | null }
}

export async function startSelfService(question: string, teamId?: number): Promise<SelfServiceSession> {
  const { data } = await request.post('/enterprise/it/self-service', { question, team_id: teamId ?? null })
  return data
}

export async function viewArticle(sessionId: number, articleId: number) {
  await request.post(`/enterprise/it/self-service/${sessionId}/articles/${articleId}/view`)
}

export async function rateArticle(sessionId: number, articleId: number, helpful: boolean) {
  await request.post(`/enterprise/it/self-service/${sessionId}/articles/${articleId}/rate`, { helpful })
}

export async function markSolved(sessionId: number, articleId?: number): Promise<SelfServiceSession> {
  const { data } = await request.post(`/enterprise/it/self-service/${sessionId}/solved`, { article_id: articleId ?? null })
  return data
}

export async function convertToTicket(sessionId: number, payload: { team_id: number; category: string; priority?: string;
  title: string; description: string }) {
  const { data } = await request.post(`/enterprise/it/self-service/${sessionId}/ticket`, payload)
  return data as { session: SelfServiceSession; ticket: { id: number } }
}

export async function selfServiceMetrics(teamId: number, days = 30): Promise<SelfServiceMetrics> {
  const { data } = await request.get('/enterprise/it/self-service/metrics', { params: { team_id: teamId, days } })
  return data
}
