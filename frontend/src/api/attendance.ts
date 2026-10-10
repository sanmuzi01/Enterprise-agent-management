import request from '../utils/request'

export type AnomalyStatus = 'open' | 'explained' | 'confirmed' | 'dismissed' | 'cleared'

export interface AttendanceMe { user_id: number; is_hr: boolean; is_head: boolean; open_mine: number; to_decide: number }

export interface Anomaly {
  id: number
  user_id: number
  user_name: string | null
  team_id: number | null
  team_name: string | null
  work_date: string
  type: string
  type_label: string
  severity: 'low' | 'medium' | 'high'
  severity_label: string
  detail: { punches?: string[]; work_start?: string; work_end?: string; minutes?: number; hours?: number; flex_minutes?: number; required_end?: string; leave?: { type?: string; from?: string; to?: string } }
  status: AnomalyStatus
  status_label: string
  explanation: string | null
  decision_note: string | null
  decided_by: number | null
  decided_by_name?: string | null
  explained_at: string | null
  decided_at: string | null
}

export interface ImportResult {
  import_id: number
  format: string
  format_label: string
  period: [string | null, string | null]
  rows: number
  matched_people: number
  matched_rows: number
  no_records: string[]
  no_records_total: number
  punches: number
  new_punches: number
  duplicate_punches: number
  unmatched: { name: string; rows: number }[]
  skipped: { row: number; reason: string }[]
  skipped_total: number
}

export interface ImportRecord { id: number; file_name: string; format: string; rows: number; punches: number; new_punches: number; unmatched: number; period: [string | null, string | null]; created_at: string }
export interface RuleItem { team_id: number | null; work_start: string; work_end: string; grace_minutes: number; flex_minutes?: number }
export interface Rules { default: RuleItem; teams: RuleItem[]; org_teams: { id: number; name: string }[] }
export interface AnalyzeResult { from: string; to: string; people: number; days: number; created: number; updated: number; cleared: number; total_anomalies: number; uncovered: string[]; uncovered_total: number }
export interface Summary { from: string; to: string; by_type: Record<string, number>; by_status: Record<string, number>; people_affected: number; unexplained_over_2_days: number; waiting_decision: number; narrative: string }

const base = '/enterprise/attendance'

export const getMe = (teamId: number) => request.get<AttendanceMe>(`${base}/me`, { params: { team_id: teamId } }).then((r) => r.data)

export const listAnomalies = (teamId: number, view: 'mine' | 'team', status?: string, start?: string, end?: string) =>
  request.get<Anomaly[]>(`${base}/anomalies`, { params: { team_id: teamId, view, status, start, end } }).then((r) => r.data)

export const explainAnomaly = (teamId: number, id: number, text: string) =>
  request.post<Anomaly>(`${base}/anomalies/${id}/explain`, { team_id: teamId, text }).then((r) => r.data)

export const decideAnomaly = (teamId: number, id: number, action: 'confirm' | 'dismiss', note: string) =>
  request.post<Anomaly>(`${base}/anomalies/${id}/decide`, { team_id: teamId, action, note }).then((r) => r.data)

export const getSummary = (teamId: number, start: string, end: string) =>
  request.get<Summary>(`${base}/summary`, { params: { team_id: teamId, start, end } }).then((r) => r.data)

export const importFile = (teamId: number, file: File, aliases: Record<string, number> = {}) => {
  const form = new FormData()
  form.append('team_id', String(teamId))
  form.append('aliases', JSON.stringify(aliases))
  form.append('file', file)
  return request.post<ImportResult>(`${base}/import`, form, { timeout: 120000, headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => r.data)
}

export const listImports = (teamId: number) => request.get<ImportRecord[]>(`${base}/imports`, { params: { team_id: teamId } }).then((r) => r.data)
export const listMembers = (teamId: number) => request.get<{ user_id: number; name: string }[]>(`${base}/members`, { params: { team_id: teamId } }).then((r) => r.data)
export const getRules = (teamId: number) => request.get<Rules>(`${base}/rules`, { params: { team_id: teamId } }).then((r) => r.data)
export const setRule = (teamId: number, forTeamId: number | null, workStart: string, workEnd: string, grace: number, flex = 0) =>
  request.post<Rules>(`${base}/rules`, { team_id: teamId, for_team_id: forTeamId, work_start: workStart, work_end: workEnd, grace_minutes: grace,
    flex_minutes: flex }).then((r) => r.data)
export const getCalendar = (teamId: number, start: string, end: string) =>
  request.get<{ day: string; kind: string; note: string | null }[]>(`${base}/calendar`, { params: { team_id: teamId, start, end } }).then((r) => r.data)
export const importCalendar = (teamId: number, text: string) => request.post<{ imported: number }>(`${base}/calendar`, { team_id: teamId, text }).then((r) => r.data)
export const analyze = (teamId: number, start: string, end: string) => request.post<AnalyzeResult>(`${base}/analyze`, { team_id: teamId, start, end }).then((r) => r.data)
