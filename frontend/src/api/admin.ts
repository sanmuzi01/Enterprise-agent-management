import request from '../utils/request'

export interface AdminOverview {
  counts: Record<string, number>
  task_status: Record<string, number>
  knowledge_status: Record<string, number>
}

export interface AdminUser {
  id: number
  name: string
  phone?: string | null
  age?: number | null
  is_disabled?: number
  last_login_at?: string | null
  last_seen_at?: string | null
  is_online?: boolean
  selected_agent_id?: number | null
  roles: string[]
  is_admin: boolean
  agent_count: number
  skill_count: number
  knowledge_count: number
  task_count: number
  counts?: Record<string, number>
  plan_name?: string | null
}

export interface AdminTask {
  id: number
  user_id: number
  agent_id?: number | null
  task_type: string
  status: string
  title: string
  target_type?: string | null
  target_id?: number | null
  progress: number
  error_msg?: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface AdminUsageDaily {
  date: string
  runs: number
  tokens: number
  messages: number
}

export interface AdminUsageTopUser {
  user_id: number
  name: string
  run_count: number
  tokens: number
}

export interface AdminUsage {
  summary: {
    total_runs: number
    finished_runs: number
    failed_runs: number
    success_rate: number
    total_tokens: number
    total_messages: number
  }
  daily: AdminUsageDaily[]
  task_status: Record<string, number>
  top_users: AdminUsageTopUser[]
}

export interface AdminLog {
  id: number
  user_id?: number | null
  username?: string | null
  method: string
  path: string
  status_code: number
  latency_ms: number
  client_ip?: string | null
  user_agent?: string | null
  error_msg?: string | null
  created_at?: string | null
}

export async function getAdminOverview(): Promise<AdminOverview> {
  const { data } = await request.get('/admin/overview')
  return data as AdminOverview
}

export interface AdminUserPage {
  items: AdminUser[]
  total: number
  limit: number
  offset: number
}

export async function listAdminUsers(params: {
  limit?: number
  offset?: number
  search?: string
} = {}): Promise<AdminUserPage> {
  const { data } = await request.get('/admin/users', { params })
  return data as AdminUserPage
}

export async function updateUserRoles(userId: number, roles: string[]) {
  const { data } = await request.put(`/admin/users/${userId}/roles`, { roles })
  return data
}

export async function getAdminUser(userId: number): Promise<AdminUser> {
  const { data } = await request.get(`/admin/users/${userId}`)
  return data as AdminUser
}

export async function updateUserStatus(userId: number, disabled: boolean) {
  const { data } = await request.patch(`/admin/users/${userId}/status`, { disabled })
  return data
}

export async function resetUserPassword(userId: number, newPassword: string) {
  const { data } = await request.put(`/admin/users/${userId}/password`, { new_password: newPassword })
  return data
}

export async function deleteAdminUser(userId: number) {
  const { data } = await request.delete(`/admin/users/${userId}`)
  return data
}

export async function listAdminTasks(limit = 50): Promise<AdminTask[]> {
  const { data } = await request.get('/admin/tasks', { params: { limit } })
  return data as AdminTask[]
}

export async function getAdminUsage(days = 14): Promise<AdminUsage> {
  const { data } = await request.get('/admin/usage', { params: { days } })
  return data as AdminUsage
}

export interface AdminLogPage {
  items: AdminLog[]
  total: number
  limit: number
  offset: number
}

export async function listAdminLogs(params: {
  limit?: number
  offset?: number
  days?: number
  keyword?: string
  method?: string
  status_group?: string
  user_id?: number
} = {}): Promise<AdminLogPage> {
  const { data } = await request.get('/admin/logs', { params })
  return data as AdminLogPage
}

export interface AdminKnowledgeSpace {
  id: number
  name: string
  owner_user_id: number
  owner_name: string
  organization_id: number | null
  team_id: number | null
  team_name: string | null
  scope_type: string
  sensitivity: string
  sensitivity_label: string
  status: string
  is_enabled: boolean
  purpose: string | null
  doc_count: number
  chunk_count: number
  member_count: number
  bound_agent_count: number
  health_score: number | null
  created_at: string | null
  updated_at: string | null
}

export interface AdminKnowledgeSpacePage {
  items: AdminKnowledgeSpace[]
  total: number
  limit: number
  offset: number
}

export async function listAdminKnowledgeSpaces(params: {
  limit?: number
  offset?: number
} = {}): Promise<AdminKnowledgeSpacePage> {
  const { data } = await request.get('/admin/knowledge-spaces', { params })
  return data
}

/** 管理员直接改一个空间的启停/归档状态，不要求管理员是该空间成员 */
export async function updateAdminSpaceStatus(
  spaceId: number,
  patch: { is_enabled?: boolean; status?: 'active' | 'archived' },
): Promise<{ id: number; name: string; is_enabled: boolean; status: string }> {
  const { data } = await request.patch(`/admin/knowledge-spaces/${spaceId}`, patch)
  return data
}

export interface AdminPlan {
  id: number
  name: string
  display_name: string
  monthly_token_limit: number
  price_desc: string | null
  is_default: boolean
  is_enabled: boolean
  created_at: string | null
  updated_at: string | null
}

export async function listAdminPlans(): Promise<AdminPlan[]> {
  const { data } = await request.get('/admin/plans')
  return data as AdminPlan[]
}

export async function createAdminPlan(data: {
  name: string
  display_name: string
  monthly_token_limit?: number
  price_desc?: string
  is_default?: boolean
}): Promise<AdminPlan> {
  const { data: res } = await request.post('/admin/plans', data)
  return res as AdminPlan
}

export async function updateAdminPlan(planId: number, patch: {
  display_name?: string
  monthly_token_limit?: number
  price_desc?: string
  is_default?: boolean
  is_enabled?: boolean
}): Promise<AdminPlan> {
  const { data } = await request.patch(`/admin/plans/${planId}`, patch)
  return data as AdminPlan
}

export async function deleteAdminPlan(planId: number) {
  const { data } = await request.delete(`/admin/plans/${planId}`)
  return data
}

export async function assignUserPlan(userId: number, planId: number) {
  const { data } = await request.patch(`/admin/users/${userId}/plan`, { plan_id: planId })
  return data
}
