import request from '../utils/request'

export interface RankRow {
  id: number | string
  name: string
  items: number
  saved_minutes: number
  adopted: number
  drafts: number
  runs: number
  failed_runs: number
  adoption_rate: number | null
  failure_rate: number | null
}

export interface ProductivityDashboard {
  days: number
  totals: {
    items: number
    active_users: number
    estimated_saved_minutes: number
    measured_minutes: number
    reported_saved_minutes: number
    reported_count: number
    drafts: number
    adopted: number
    adopted_as_is: number
    initiated: number
    completed: number
    runs: number
    failed_runs: number
  }
  rates: { adoption: number | null; as_is: number | null; completion: number | null; failure: number | null }
  by_team: RankRow[]
  by_agent: RankRow[]
  by_source: RankRow[]
  trend: { day: string; items: number; saved_minutes: number; runs: number; failed_runs: number }[]
  failure_reasons: { code: string; count: number }[]
  changed_fields: { field: string; count: number }[]
}

export async function adminProductivity(days: number): Promise<ProductivityDashboard> {
  const { data } = await request.get('/admin/productivity', { params: { days } })
  return data
}

export async function departmentProductivity(teamId: number, days: number): Promise<ProductivityDashboard> {
  const { data } = await request.get('/department/productivity', { params: { team_id: teamId, days } })
  return data
}

export async function reportSaved(sourceKey: string, minutes: number) {
  const { data } = await request.post('/productivity/feedback', { source_key: sourceKey, minutes })
  return data as { reported_saved_minutes: number }
}
