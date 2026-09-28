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

export interface OrgMember {
  user_id: number
  name: string
  role_code: string
  role_name: string
  status: string
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

export async function createOrgTeam(name: string): Promise<OrgTeam> {
  const { data } = await request.post('/admin/org/teams', { name })
  return data as OrgTeam
}

export async function updateOrgTeam(
  teamId: number,
  patch: { name?: string; status?: string },
): Promise<{ id: number; name: string; status: string }> {
  const { data } = await request.patch(`/admin/org/teams/${teamId}`, patch)
  return data
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
