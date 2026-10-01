import request from '../utils/request'

export interface WorkspaceOrganization {
  id: number
  name: string
  role_name: string
}

export interface WorkspaceDepartment {
  id: number
  name: string
  role_name: string
  department_code: string | null
}

export interface WorkspaceAgent {
  id: number
  name: string
  agent_type: 'central' | 'department'
  department_code: string | null
  team_id: number | null
  team_name: string | null
  model_name: string
  model_configured: boolean
  description: string
  examples: string[]
  skill_count: number
}

export interface Workspace {
  organizations: WorkspaceOrganization[]
  departments: WorkspaceDepartment[]
  agents: WorkspaceAgent[]
}

/** GET /enterprise/workspace ——真实的组织/部门归属 + 当前用户能用的已发布 Agent 列表，
 * 后端早就有这个接口（`service/enterprise_workspace_service.py`），部门工作台是它
 * 第一个前端消费者。 */
export async function getWorkspace(): Promise<Workspace> {
  const { data } = await request.get('/enterprise/workspace')
  return data as Workspace
}
