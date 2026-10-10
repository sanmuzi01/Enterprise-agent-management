import request from '../utils/request'

export type Provider = 'feishu' | 'dingtalk'

export interface IntegrationApp {
  provider: Provider
  label: string
  configured: boolean
  enabled: boolean
  app_id?: string
  app_secret?: string
  has_verification_token?: boolean
  encrypt_key?: string
  robot_code?: string
  card_template_id?: string
  last_health_at?: string | null
  last_error?: string | null
}

export interface IntegrationHealth extends IntegrationApp {
  events_24h: Record<string, number>
  bindings: Record<string, number>
  recent_failures: { event_type: string; received_at: string; error: string | null }[]
  public_base_url?: string | null
  push?: { done_24h: number; retrying: number; dead: number }
  callback_paths: { events: string; card_actions: string }
}

export interface IntegrationBinding {
  id: number
  external_user_id: string
  external_name: string | null
  status: 'active' | 'disabled' | 'unmatched'
  local_user_id: number | null
  local_user_name: string | null
  last_synced_at: string | null
}

export interface IntegrationAppForm {
  app_id: string
  app_secret?: string
  verification_token?: string
  encrypt_key?: string
  robot_code?: string
  card_template_id?: string
  enabled: boolean
}

export interface SyncSummary {
  departments: number
  departments_unmatched: string[]
  users: number
  users_matched: number
  users_unmatched: string[]
  users_unmatched_total: number
  users_disabled: string[]
  users_disabled_total: number
}

export async function listIntegrations(): Promise<IntegrationApp[]> {
  const { data } = await request.get('/admin/integrations')
  return data
}

export async function saveIntegration(provider: Provider, form: IntegrationAppForm): Promise<IntegrationApp> {
  const { data } = await request.put(`/admin/integrations/${provider}`, form)
  return data
}

export async function testIntegration(provider: Provider): Promise<{ ok: boolean; error: string | null; checked_at: string }> {
  const { data } = await request.post(`/admin/integrations/${provider}/test`)
  return data
}

export async function integrationHealth(provider: Provider): Promise<IntegrationHealth> {
  const { data } = await request.get(`/admin/integrations/${provider}/health`)
  return data
}

export async function syncOrganization(provider: Provider): Promise<SyncSummary> {
  const { data } = await request.post(`/admin/integrations/${provider}/sync-organization`, undefined, { timeout: 120000 })
  return data
}

export async function listBindings(provider: Provider): Promise<IntegrationBinding[]> {
  const { data } = await request.get(`/admin/integrations/${provider}/bindings`)
  return data
}

export async function changeBinding(provider: Provider, externalUserId: string, localUserId: number | null) {
  const { data } = await request.put(`/admin/integrations/${provider}/bindings`, {
    external_user_id: externalUserId, local_user_id: localUserId,
  })
  return data
}

// ------------------------------------------------------------------ 员工自助绑定

export interface MyIntegration {
  provider: Provider
  label: string
  available: boolean
  status: 'unavailable' | 'unbound' | 'bound' | 'disabled'
  external_name?: string | null
  bot_link?: string | null
  oauth_available?: boolean
}

export async function myIntegrations(): Promise<MyIntegration[]> {
  const { data } = await request.get('/me/integrations')
  return data.items
}

export async function getBindCode(provider: Provider): Promise<{ code: string; expires_in: number; command: string; bot_link: string | null }> {
  const { data } = await request.post(`/me/integrations/${provider}/bind-code`)
  return data
}

export async function unbindMine(provider: Provider) {
  await request.delete(`/me/integrations/${provider}/binding`)
}

export async function oauthStart(provider: Provider): Promise<string> {
  const { data } = await request.get(`/me/integrations/${provider}/oauth/start`)
  return data.url
}

// ------------------------------------------------------------------ 管理员自检

export interface ReadinessCheck {
  key: string
  label: string
  ok: boolean
  level: 'ok' | 'error' | 'warning' | 'info'
  detail: string
  fix: string
}

export async function integrationReadiness(provider: Provider): Promise<{ ready: boolean; checks: ReadinessCheck[]; oauth_redirect_uri: string | null }> {
  const { data } = await request.get(`/admin/integrations/${provider}/readiness`)
  return data
}

export async function inviteUnbound(provider: Provider): Promise<{ sent: number; unbound: number }> {
  const { data } = await request.post(`/admin/integrations/${provider}/invite-unbound`)
  return data
}

// ------------------------------------------------------------------ 按部门的人员对应表

export interface DirectoryBinding {
  id: number
  external_user_id: string
  external_name: string | null
  status: 'active' | 'disabled' | 'unmatched'
  synced: boolean
  since?: string | null
}

export interface DirectoryMember {
  user_id: number
  name: string
  role_name: string | null
  binding: DirectoryBinding | null
}

export interface BindingDirectory {
  departments: { team_id: number | null; name: string; department_code: string | null; members: DirectoryMember[]; bound: number; total: number }[]
  unlinked: DirectoryBinding[]
  summary: { members: number; bound: number; disabled: number; unbound: number; unlinked: number }
}

export async function bindingDirectory(provider: Provider): Promise<BindingDirectory> {
  const { data } = await request.get(`/admin/integrations/${provider}/directory`)
  return data
}
