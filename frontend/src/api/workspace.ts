import request from '../utils/request'

export interface WorkspaceWidget {
  id: string
  type: string
  title: string
  enabled: boolean
  size: 'wide' | 'small' | string
  settings: Record<string, any>
}

export interface UserWorkspaceConfig {
  modules: string[]
  widgets: WorkspaceWidget[]
  layout: Record<string, any>
  updated_at?: string | null
}

export async function getWorkspaceConfig(): Promise<UserWorkspaceConfig> {
  const { data } = await request.get<UserWorkspaceConfig>('/user/workspace')
  return data
}

export async function saveWorkspaceConfig(payload: UserWorkspaceConfig): Promise<UserWorkspaceConfig> {
  const { data } = await request.put<UserWorkspaceConfig>('/user/workspace', payload)
  return data
}

export async function commandWorkspace(prompt: string): Promise<{
  message: string
  actions: string[]
  workspace: UserWorkspaceConfig
}> {
  const { data } = await request.post('/user/workspace/command', { prompt })
  return data
}
