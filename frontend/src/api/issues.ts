import request from '../utils/request'

export type IssueStatus = 'OPEN' | 'ACKNOWLEDGED' | 'INVESTIGATING' | 'MITIGATED' | 'RESOLVED' | 'REGRESSED'

export interface Issue {
  id: number; issue_no: string; title: string; severity: string; severity_label: string; category: string; category_label: string
  status: IssueStatus; status_label: string; service: string; operation: string | null; error_code: string; department_id: number | null
  responsible_user_id: number | null; last_trace_id: string | null; sentry_event_id: string | null; occurrence_count: number
  retryable: boolean; first_seen_at: string; last_seen_at: string; acknowledged_at: string | null; resolved_at: string | null
  root_cause: string | null; resolution: string | null; fix_version: string | null; resolved_by: number | null
  verified_by: number | null; verified_at: string | null; regress_count: number
}
export interface IssueDetail extends Issue {
  responsible_name: string | null; resolved_by_name: string | null; verified_by_name: string | null
  occurrences: { id: number; trace_id: string | null; release: string | null; http_status: number | null; message: string | null; detail?: Record<string, unknown>; occurred_at: string }[]
  events: { id: number; actor_name: string; action: string; note: string | null; created_at: string }[]
  links?: { trace: string | null; sentry: string | null; logs: string | null }
}
export interface IssueSummary {
  by_status: Record<string, number>; open_by_severity: Record<string, number>; active: number
  mtta_minutes: number | null; mttr_minutes: number | null; regressed: number
}

export const listIssues = async (params: { status?: string; severity?: string; keyword?: string }) =>
  (await request.get<Issue[]>('/admin/issues', { params })).data
export const getIssueSummary = async () => (await request.get<IssueSummary>('/admin/issues/summary')).data
export const getIssue = async (id: number) => (await request.get<IssueDetail>(`/admin/issues/${id}`)).data
export const actOnIssue = async (id: number, body: Record<string, unknown>) =>
  (await request.post<IssueDetail>(`/admin/issues/${id}/actions`, body)).data

export interface DeadLetter {
  id: number; event_id: string; consumer: string; topic: string; event_type: string; trace_id: string | null; error: string | null
  attempts: number; status: 'pending' | 'redelivered' | 'discarded'; discard_reason: string | null; created_at: string
}
export interface EventStats { unpublished: number; oldest_unpublished_seconds: number; dead_letters: number; retrying: number }
export const getEventStats = async () => (await request.get<EventStats>('/admin/events/stats')).data
export const listDeadLetters = async (status = 'pending') => (await request.get<DeadLetter[]>('/admin/events/dead-letters', { params: { status } })).data
export const redeliverDeadLetter = async (id: number) => (await request.post<DeadLetter>(`/admin/events/dead-letters/${id}/redeliver`)).data
export const discardDeadLetter = async (id: number, reason: string) => (await request.post<DeadLetter>(`/admin/events/dead-letters/${id}/discard`, { reason })).data
