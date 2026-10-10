import request from '../utils/request'

export interface MatchCandidate { customer_id: number; name: string; confidence: number; method: string }

export interface CrmActivity {
  id: number
  team_id: number
  customer_id: number | null
  customer_name: string | null
  activity_type: string
  type_label: string
  source_provider: string
  occurred_at: string
  participants: { name: string; email: string; role: string }[]
  title: string | null
  content: string | null
  match_status: 'explicit' | 'auto' | 'manual' | 'pending' | 'unmatched' | 'ignored'
  match_confidence: number | null
  match_method: string | null
  match_candidates: MatchCandidate[]
}

export interface IngestResult { duplicate: boolean; activity: CrmActivity }

export interface TimelineItem {
  id: number | string
  kind: string
  type_label: string
  title: string | null
  content: string | null
  occurred_at: string
  status?: string
  due_date?: string | null
  participants?: { name: string; email: string }[]
}

export interface SummarySnapshot {
  id: number
  summary: string
  needs: string[]
  stakeholders: string[]
  risks: string[]
  next_actions: string[]
  generated_at: string
  model_name: string | null
}

export interface RiskFinding {
  id: number
  customer_id: number
  opportunity_id: number | null
  risk_code: string
  label: string
  level: 'high' | 'medium' | 'low'
  evidence: string
  suggested_action: string | null
  source: 'rule' | 'model'
}

export interface Suggestion {
  id: number
  customer_id: number
  title: string
  detail: string | null
  due_date: string | null
  status: string
}

export interface MailAccount {
  imap_host: string
  imap_port: number
  username: string
  folder: string
  enabled: boolean
  last_synced_at: string | null
  last_error: string | null
}

const base = '/enterprise/crm-copilot'

export async function uploadEmail(teamId: number, file: File, customerId?: number): Promise<IngestResult> {
  const form = new FormData()
  form.append('team_id', String(teamId))
  if (customerId) form.append('customer_id', String(customerId))
  form.append('file', file)
  const { data } = await request.post(`${base}/ingest/email`, form)
  return data
}

export async function uploadCalendar(teamId: number, file: File): Promise<{ created: number; duplicates: number }> {
  const form = new FormData()
  form.append('team_id', String(teamId))
  form.append('file', file)
  const { data } = await request.post(`${base}/ingest/calendar`, form)
  return data
}

export async function addActivity(teamId: number, customerId: number, activityType: string, title: string, content: string,
  occurredAt?: string): Promise<IngestResult> {
  const { data } = await request.post(`${base}/activities`, {
    team_id: teamId, customer_id: customerId, activity_type: activityType, title, content, occurred_at: occurredAt || null,
  })
  return data
}

export async function listActivities(teamId: number, status: 'pending' | 'all' = 'pending'):
  Promise<{ items: CrmActivity[]; pending_count: number }> {
  const { data } = await request.get(`${base}/activities`, { params: { team_id: teamId, status } })
  return data
}

export async function assignActivity(teamId: number, activityId: number, customerId: number) {
  const { data } = await request.post(`${base}/activities/${activityId}/assign`, { team_id: teamId, customer_id: customerId, remember: true })
  return data as { activity: CrmActivity; learned: { type: string; value: string }[]; rematched: number }
}

export async function ignoreActivity(teamId: number, activityId: number) {
  await request.post(`${base}/activities/${activityId}/ignore`, { team_id: teamId })
}

export async function getTimeline(teamId: number, customerId: number): Promise<{ items: TimelineItem[] }> {
  const { data } = await request.get(`${base}/customers/${customerId}/timeline`, { params: { team_id: teamId } })
  return data
}

export async function getSummary(teamId: number, customerId: number):
  Promise<{ snapshot: SummarySnapshot | null; has_new_activities: boolean }> {
  const { data } = await request.get(`${base}/customers/${customerId}/summary`, { params: { team_id: teamId } })
  return data
}

export async function refreshSummary(teamId: number, customerId: number, modelName: string) {
  const { data } = await request.post(`${base}/customers/${customerId}/summary/refresh`,
    { team_id: teamId, model_name: modelName }, { timeout: 120000 })
  return data as { snapshot: SummarySnapshot; unchanged: boolean }
}

export async function scanRisks(teamId: number, customerId: number, modelName?: string) {
  const { data } = await request.post(`${base}/customers/${customerId}/risks/scan`,
    { team_id: teamId, model_name: modelName || null }, { timeout: 120000 })
  return data as { risks: RiskFinding[] }
}

export async function listRisks(teamId: number, customerId?: number): Promise<RiskFinding[]> {
  const { data } = await request.get(`${base}/risks`, { params: { team_id: teamId, customer_id: customerId } })
  return data
}

export async function dismissRisk(teamId: number, findingId: number) {
  await request.post(`${base}/risks/${findingId}/dismiss`, { team_id: teamId })
}

export async function listSuggestions(teamId: number, customerId?: number): Promise<Suggestion[]> {
  const { data } = await request.get(`${base}/suggestions`, { params: { team_id: teamId, customer_id: customerId } })
  return data
}

export async function decideSuggestion(teamId: number, id: number, decision: 'create' | 'edit_create' | 'ignore' | 'snooze',
  extra: { title?: string; due_date?: string; remind_days?: number } = {}): Promise<Suggestion> {
  const { data } = await request.post(`${base}/suggestions/${id}/decide`, { team_id: teamId, decision, ...extra })
  return data
}

export async function getMailbox(teamId: number): Promise<MailAccount | null> {
  const { data } = await request.get(`${base}/mailbox`, { params: { team_id: teamId } })
  return data.account
}

export async function saveMailbox(teamId: number, form: { imap_host: string; imap_port: number; username: string;
  password?: string; folder: string; enabled: boolean }): Promise<MailAccount> {
  const { data } = await request.put(`${base}/mailbox`, { team_id: teamId, ...form })
  return data.account
}

export async function deleteMailbox(teamId: number) {
  await request.delete(`${base}/mailbox`, { params: { team_id: teamId } })
}

export async function syncMailbox(teamId: number) {
  const { data } = await request.post(`${base}/mailbox/sync`, { team_id: teamId }, { timeout: 120000 })
  return data as { fetched: number; created: number; duplicates: number; failed: number }
}
