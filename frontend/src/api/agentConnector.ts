import request from '../utils/request'

export interface ApiConnectorParam {
  name: string
  type: 'string' | 'integer' | 'number' | 'boolean'
  description?: string
  required: boolean
}

export interface ApiConnector {
  id: number
  agent_id: number
  name: string
  description: string
  url: string
  method: 'GET' | 'POST'
  has_headers: boolean
  param_schema: { type: 'object'; properties: Record<string, any>; required?: string[] }
  static_query: Record<string, any>
  is_enabled: boolean
  created_at: string | null
}

export interface CreateApiConnectorPayload {
  name: string
  description: string
  url: string
  method: 'GET' | 'POST'
  headers?: Record<string, string>
  param_schema?: { type: 'object'; properties: Record<string, any>; required?: string[] }
  static_query?: Record<string, any>
}

/** 列出这个助手配置的企业接口工具 */
export async function listApiConnectors(agentId: number): Promise<ApiConnector[]> {
  const { data } = await request.get(`/agent/${agentId}/api-connectors`)
  return data as ApiConnector[]
}

/** 新建一个企业接口工具 */
export async function createApiConnector(agentId: number, payload: CreateApiConnectorPayload): Promise<ApiConnector> {
  const { data } = await request.post(`/agent/${agentId}/api-connectors`, payload)
  return data as ApiConnector
}

/** 启用/停用一个企业接口工具 */
export async function setApiConnectorEnabled(connectorId: number, isEnabled: boolean): Promise<ApiConnector> {
  const { data } = await request.patch(`/agent/api-connectors/${connectorId}`, { is_enabled: isEnabled })
  return data as ApiConnector
}

/** 删除一个企业接口工具 */
export async function deleteApiConnector(connectorId: number): Promise<{ message: string }> {
  const { data } = await request.delete(`/agent/api-connectors/${connectorId}`)
  return data
}

// ---- 管理员：企业智能体（中央 / 部门）的接口工具，在后台「企业智能体 → 编辑 → 接口工具」里维护，写审计 ----

export async function listManagedApiConnectors(agentId: number): Promise<ApiConnector[]> {
  const { data } = await request.get(`/admin/org/agents/${agentId}/api-connectors`)
  return data as ApiConnector[]
}

export async function createManagedApiConnector(agentId: number, payload: CreateApiConnectorPayload): Promise<ApiConnector> {
  const { data } = await request.post(`/admin/org/agents/${agentId}/api-connectors`, payload)
  return data as ApiConnector
}

export async function setManagedApiConnectorEnabled(agentId: number, connectorId: number, isEnabled: boolean): Promise<ApiConnector> {
  const { data } = await request.patch(`/admin/org/agents/${agentId}/api-connectors/${connectorId}`, { is_enabled: isEnabled })
  return data as ApiConnector
}

export async function deleteManagedApiConnector(agentId: number, connectorId: number): Promise<{ message: string }> {
  const { data } = await request.delete(`/admin/org/agents/${agentId}/api-connectors/${connectorId}`)
  return data
}

/** 两套接口（用户自己的助手 / 管理员维护的企业智能体）统一成一个形状，给 ApiConnectorManager 用 */
export function connectorApi(mode: 'personal' | 'managed') {
  if (mode === 'managed') {
    return {
      list: listManagedApiConnectors,
      create: createManagedApiConnector,
      setEnabled: setManagedApiConnectorEnabled,
      remove: deleteManagedApiConnector,
    }
  }
  return {
    list: listApiConnectors,
    create: createApiConnector,
    setEnabled: (_agentId: number, connectorId: number, isEnabled: boolean) => setApiConnectorEnabled(connectorId, isEnabled),
    remove: (_agentId: number, connectorId: number) => deleteApiConnector(connectorId),
  }
}
