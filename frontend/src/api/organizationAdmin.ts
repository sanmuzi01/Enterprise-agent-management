import request from '../utils/request'

export interface EnterpriseRoleOption {
  code: string
  name: string
  rank: number
}

export interface EnterpriseRoleCatalog {
  organization: EnterpriseRoleOption[]
  team: EnterpriseRoleOption[]
}

export interface OrgTeamLead {
  user_id: number
  name: string
}

export interface OrgTeam {
  id: number
  name: string
  status: string
  department_code: string | null
  member_count: number
  leads: OrgTeamLead[]
  created_at: string | null
}

export interface OrgTeamMember {
  user_id: number
  name: string
  role_code: string
  role_name: string
  status: string
}

export interface OrgMemberDepartment {
  id: number
  name: string
  status: string
  role_code: string
  role_name: string
  membership_status: string
}

export interface OrgMember {
  user_id: number
  name: string
  role_code: string
  role_name: string
  status: string
  departments: OrgMemberDepartment[]
}

export interface TeamPermissions {
  team: { id: number; name: string; status: string }
  members: OrgTeamMember[]
  knowledge_spaces: { id: number; name: string; status: string }[]
  agents: { id: number; name: string; department_code: string | null }[]
}

export async function getEnterpriseRoles(): Promise<EnterpriseRoleCatalog> {
  const { data } = await request.get('/admin/org/roles')
  return data as EnterpriseRoleCatalog
}

export async function listOrgTeams(): Promise<OrgTeam[]> {
  const { data } = await request.get('/admin/org/teams')
  return data as OrgTeam[]
}

export async function createOrgTeam(name: string, departmentCode?: string | null): Promise<OrgTeam> {
  const { data } = await request.post('/admin/org/teams', { name, department_code: departmentCode })
  return data as OrgTeam
}

export async function updateOrgTeam(
  teamId: number,
  patch: { name?: string; status?: string; department_code?: string | null },
): Promise<{ id: number; name: string; status: string; department_code: string | null }> {
  const { data } = await request.patch(`/admin/org/teams/${teamId}`, patch)
  return data
}

export type DepartmentAgentState = 'ready' | 'pending_publish' | 'needs_repair' | 'team_disabled'

export interface DepartmentAgentStatus {
  team_id: number
  team_name: string
  department_code: string | null
  template_id: string
  template_name: string
  state: DepartmentAgentState
  state_label: string
  issues: string[]
  agent: { id: number; name: string; lifecycle_status: LifecycleStatus; model_name: string; row_version: number } | null
}

export async function getTeamAgentStatus(teamId: number): Promise<DepartmentAgentStatus> {
  const { data } = await request.get(`/admin/org/teams/${teamId}/agent-status`)
  return data as DepartmentAgentStatus
}

export async function repairTeamAgent(teamId: number): Promise<DepartmentAgentStatus> {
  const { data } = await request.post(`/admin/org/teams/${teamId}/agent-repair`)
  return data as DepartmentAgentStatus
}

export async function publishTeamAgent(teamId: number): Promise<DepartmentAgentStatus> {
  const { data } = await request.post(`/admin/org/teams/${teamId}/agent-publish`)
  return data as DepartmentAgentStatus
}

export async function getTeamPermissions(teamId: number): Promise<TeamPermissions> {
  const { data } = await request.get(`/admin/org/teams/${teamId}/permissions`)
  return data as TeamPermissions
}

export async function listTeamMembers(teamId: number): Promise<OrgTeamMember[]> {
  const { data } = await request.get(`/admin/org/teams/${teamId}/members`)
  return data as OrgTeamMember[]
}

export async function addTeamMember(teamId: number, userId: number, roleCode = 'member') {
  const { data } = await request.post(`/admin/org/teams/${teamId}/members`, {
    user_id: userId,
    role_code: roleCode,
  })
  return data
}

export async function updateTeamMemberRole(teamId: number, userId: number, roleCode: string) {
  const { data } = await request.patch(`/admin/org/teams/${teamId}/members/${userId}`, {
    role_code: roleCode,
  })
  return data
}

export async function removeTeamMember(teamId: number, userId: number) {
  const { data } = await request.delete(`/admin/org/teams/${teamId}/members/${userId}`)
  return data
}

export async function listOrgMembers(): Promise<OrgMember[]> {
  const { data } = await request.get('/admin/org/members')
  return data as OrgMember[]
}

export async function addOrgMember(userId: number, roleCode = 'member') {
  const { data } = await request.post('/admin/org/members', { user_id: userId, role_code: roleCode })
  return data
}

export async function updateOrgMember(userId: number, patch: { role_code?: string; status?: string }) {
  const { data } = await request.patch(`/admin/org/members/${userId}`, patch)
  return data
}

export async function removeOrgMember(userId: number) {
  const { data } = await request.delete(`/admin/org/members/${userId}`)
  return data
}

export interface Enterprise {
  id: number
  name: string
  team_count: number
  member_count: number
}

export async function getEnterprise(): Promise<Enterprise> {
  const { data } = await request.get('/admin/org/enterprise')
  return data as Enterprise
}

export async function renameEnterprise(name: string): Promise<Enterprise> {
  const { data } = await request.patch('/admin/org/enterprise', { name })
  return data as Enterprise
}

export type ManagedAgentType = 'central' | 'department'
export type LifecycleStatus = 'draft' | 'reviewing' | 'published' | 'retired'

export interface ManagedAgent {
  id: number
  name: string
  agent_type: ManagedAgentType
  department_code: string | null
  team_id: number | null
  team_name: string | null
  model_name: string
  runtime_type: 'builtin' | 'external'
  /** 划分状态：全企业 / 某个部门 / 还没划分 */
  assignment: 'enterprise' | 'department' | 'unassigned'
  /** 档案：它能做什么 / 谁维护它 */
  description?: string
  maintainer?: string
  /** 绑定的知识库里，这个智能体的使用者读不到的那些（没划分给对应部门） */
  knowledge_gaps?: { id: number; name: string; reason: string }[]
  lifecycle_status: LifecycleStatus
  row_version: number
}

export interface AgentConfig {
  temperature?: number
  memory_enabled?: number
  rag_enabled?: number
  kb_top_k?: number
  kb_rerank_enabled?: number
  kb_force_citation?: number
  kb_refuse_when_empty?: number
}

export interface AgentReadinessItem {
  key: string
  level: 'ok' | 'warn' | 'error'
  label: string
  message: string
}

/** 编辑页要用的完整配置 + 发布前检查 */
export interface ManagedAgentDetail extends ManagedAgent {
  prompt: { role?: string; task?: string; constraints?: string; output?: string }
  config: Required<AgentConfig>
  space_ids: number[]
  skill_ids: number[]
  readiness: { ready: boolean; items: AgentReadinessItem[] }
}

export interface AgentOptions {
  models: string[]
  /** 管理员在「模型连接」里统一连接了 API Key 的聊天模型 */
  connected_models: string[]
  skills: { id: number; name: string; description: string; lifecycle_status: string }[]
  spaces: {
    id: number
    name: string
    scope_type: 'personal' | 'department' | 'enterprise' | string
    sensitivity: string
    doc_count: number
    departments: { id: number; name: string }[]
  }[]
  teams: { id: number; name: string; department_code: string | null }[]
}

export async function getAgentOptions(agentId?: number): Promise<AgentOptions> {
  const { data } = await request.get('/admin/org/agent-options', { params: agentId ? { agent_id: agentId } : {} })
  return data as AgentOptions
}

export async function getManagedAgentDetail(agentId: number): Promise<ManagedAgentDetail> {
  const { data } = await request.get(`/admin/org/agents/${agentId}`)
  return data as ManagedAgentDetail
}

export async function assignManagedAgent(agentId: number, payload: {
  target: 'department' | 'enterprise' | 'unassigned'
  team_id?: number
  department_code?: string
  expected_row_version?: number
}): Promise<ManagedAgent> {
  const { data } = await request.put(`/admin/org/agents/${agentId}/assignment`, payload)
  return data as ManagedAgent
}

export interface AgentRuntimeInfo {
  agent_id: number
  runtime_type: 'builtin' | 'external'
  endpoint: {
    url: string
    timeout_seconds: number
    send_knowledge: boolean
    header_names: string[]
    last_test_at: string | null
    last_test_ok: boolean | null
    last_test_message: string | null
  } | null
  /** 签名密钥，仅在刚生成或重新生成时返回这一次 */
  secret?: string
}

export async function getAgentRuntime(agentId: number): Promise<AgentRuntimeInfo> {
  const { data } = await request.get(`/admin/org/agents/${agentId}/runtime`)
  return data as AgentRuntimeInfo
}

export async function setAgentRuntime(agentId: number, payload: {
  runtime_type: 'builtin' | 'external'
  url?: string
  timeout_seconds?: number
  send_knowledge?: boolean
  headers?: Record<string, string>
  rotate_secret?: boolean
}): Promise<AgentRuntimeInfo> {
  const { data } = await request.put(`/admin/org/agents/${agentId}/runtime`, payload)
  return data as AgentRuntimeInfo
}

export async function testAgentRuntime(agentId: number): Promise<AgentRuntimeInfo & { ok: boolean; message: string }> {
  const { data } = await request.post(`/admin/org/agents/${agentId}/runtime/test`)
  return data
}

export async function listManagedAgents(): Promise<ManagedAgent[]> {
  const { data } = await request.get('/admin/org/agents')
  return data as ManagedAgent[]
}

export interface AgentTemplate {
  id: string
  name: string
  agent_type: ManagedAgentType
  department_code: string | null
  description: string
  role: string
  task: string
  tools: string[]
  examples: string[]
}

export async function getAgentTemplates(): Promise<AgentTemplate[]> {
  const { data } = await request.get('/admin/org/agent-templates')
  return data as AgentTemplate[]
}

export async function createManagedAgent(payload: {
  name: string
  agent_type: ManagedAgentType
  department_code?: string
  team_id?: number
  model_name?: string
  role?: string
  task?: string
  constraints?: string
  output?: string
  template_id?: string
  config?: AgentConfig
  space_ids?: number[]
  skill_ids?: number[]
  description?: string
  maintainer?: string
}): Promise<ManagedAgent> {
  const { data } = await request.post('/admin/org/agents', payload)
  return data as ManagedAgent
}

/** 接入工程师已经开发好的智能体服务：登记档案 + 配好地址 + 生成签名密钥，一步完成。secret 只在这一次返回。 */
export async function registerExternalAgent(payload: {
  name: string
  description?: string
  maintainer?: string
  agent_type: ManagedAgentType
  department_code?: string
  url: string
  timeout_seconds?: number
  send_knowledge?: boolean
  headers?: Record<string, string>
}): Promise<ManagedAgentDetail & { secret?: string | null }> {
  const { data } = await request.post('/admin/org/agents/external', payload)
  return data
}

export async function updateManagedAgent(agentId: number, patch: {
  name?: string
  department_code?: string
  team_id?: number
  model_name?: string
  lifecycle_status?: LifecycleStatus
  expected_row_version?: number
  role?: string
  task?: string
  constraints?: string
  output?: string
  config?: AgentConfig
  space_ids?: number[]
  skill_ids?: number[]
  description?: string
  maintainer?: string
}): Promise<ManagedAgent> {
  const { data } = await request.patch(`/admin/org/agents/${agentId}`, patch)
  return data as ManagedAgent
}
