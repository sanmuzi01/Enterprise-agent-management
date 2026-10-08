import request from '../utils/request'

/** 企业统一的模型连接：按服务商配置一次 API Key，全公司共用（密钥只写不读） */
export interface LlmConnection {
  provider: string
  label: string
  connected: boolean
  is_active: boolean
  /** 只给末四位提示，完整密钥不会再返回 */
  key_hint: string | null
  updated_at: string | null
  chat_models: string[]
  embedding_models: string[]
  default_chat: string | null
  default_embedding: string | null
}

export interface LlmProbe {
  ok: boolean
  message?: string
  error?: string
  elapsed_ms?: number
}

export interface LlmConnectionTest {
  provider: string
  ok: boolean
  results: { chat?: LlmProbe; embedding?: LlmProbe }
}

export async function listLlmConnections(): Promise<LlmConnection[]> {
  const { data } = await request.get('/admin/llm-connections')
  return data as LlmConnection[]
}

export async function connectLlm(provider: string, apiKey: string): Promise<LlmConnection> {
  const { data } = await request.put(`/admin/llm-connections/${provider}`, { api_key: apiKey })
  return data as LlmConnection
}

export async function toggleLlm(provider: string, isActive: boolean): Promise<LlmConnection> {
  const { data } = await request.patch(`/admin/llm-connections/${provider}`, { is_active: isActive })
  return data as LlmConnection
}

export async function removeLlm(provider: string): Promise<void> {
  await request.delete(`/admin/llm-connections/${provider}`)
}

export async function testLlm(provider: string): Promise<LlmConnectionTest> {
  const { data } = await request.post(`/admin/llm-connections/${provider}/test`)
  return data as LlmConnectionTest
}

/** 员工侧：管理员已经统一连接了哪些服务商（不含任何密钥信息） */
export async function listEnterpriseProviders(): Promise<{ provider: string; label: string }[]> {
  const { data } = await request.get('/llm_config/enterprise_connections')
  return data as { provider: string; label: string }[]
}
