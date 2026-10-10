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
