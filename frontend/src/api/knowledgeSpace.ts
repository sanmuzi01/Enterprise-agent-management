import request from '../utils/request'

export interface KnowledgeSpace {
  id: number
  name: string
  description: string
  purpose: string | null
  purpose_label: string
  tags: string[]
  is_enabled: boolean
  status: 'active' | 'archived'
  doc_count: number
  chunk_count: number
  bound_agent_count?: number
  health_score: number | null
  last_indexed_at: string | null
  created_at: string | null
  updated_at: string | null
  /** personal = 我自己的；shared = 别人分享给我的；department = 划分给部门的；enterprise = 划分给全企业的 */
  scope: 'personal' | 'shared' | 'department' | 'enterprise' | string
  departments: { id: number; name: string }[]
  sensitivity: 'public' | 'internal' | 'confidential' | 'restricted' | string
  sensitivity_label: string
  my_role: 'owner' | 'admin' | 'editor' | 'viewer' | string
  can_write_doc?: boolean
  can_manage?: boolean
  can_delete?: boolean
}

export interface SpaceMember {
  user_id: number
  user_name: string
  role: 'owner' | 'admin' | 'editor' | 'viewer'
  created_at?: string | null
}

export interface SpaceMemberList {
  owner: SpaceMember
  members: SpaceMember[]
  my_role: string
  assignable_roles: string[]
}

export interface SpaceAuditEntry {
  id: number
  user_id: number
  user_name: string
  action: string
  target_type: string | null
  target_id: number | null
  detail: string | null
  created_at: string | null
}

export interface SpaceListResponse {
  items: KnowledgeSpace[]
  purposes: { key: string; label: string }[]
  sensitivities: { key: string; label: string }[]
}

export interface SpaceCreatePayload {
  name: string
  description?: string
  purpose?: string | null
  tags?: string[]
  sensitivity?: string
}

export type SpaceUpdatePayload = Partial<SpaceCreatePayload> & {
  is_enabled?: boolean
  status?: 'active' | 'archived'
}

export async function listSpaces(): Promise<SpaceListResponse> {
  const { data } = await request.get<SpaceListResponse>('/knowledge-spaces')
  return data
}

export async function getSpace(id: number): Promise<KnowledgeSpace> {
  const { data } = await request.get<KnowledgeSpace>(`/knowledge-spaces/${id}`)
  return data
}

export async function createSpace(payload: SpaceCreatePayload): Promise<KnowledgeSpace> {
  const { data } = await request.post<KnowledgeSpace>('/knowledge-spaces', payload)
  return data
}

export async function updateSpace(id: number, patch: SpaceUpdatePayload): Promise<KnowledgeSpace> {
  const { data } = await request.patch<KnowledgeSpace>(`/knowledge-spaces/${id}`, patch)
  return data
}

export async function deleteSpace(id: number): Promise<{ message: string; id: number }> {
  const { data } = await request.delete(`/knowledge-spaces/${id}`)
  return data
}

export interface SpaceHealth {
  space_id: number
  health_score: number
  level: 'good' | 'fair' | 'poor'
  stale_days: number
  computed_at: string
  documents: {
    total: number
    enabled: number
    done: number
    failed: number
    pending: number
    empty_done: number
    stale: number
    failed_rate: number
    pending_rate: number
    stale_rate: number
    disabled_rate: number
    chunk_count: number
  }
  retrieval: {
    sample_count: number
    hit_rate: number | null
    refuse_rate: number | null
    citation_rate: number | null
    useful_rate: number | null
  }
}

export async function getSpaceHealth(id: number): Promise<SpaceHealth> {
  const { data } = await request.get<SpaceHealth>(`/knowledge-spaces/${id}/health`)
  return data
}

// ---------------- 成员 / 审计（阶段6） ----------------

export async function listSpaceMembers(id: number): Promise<SpaceMemberList> {
  const { data } = await request.get<SpaceMemberList>(`/knowledge-spaces/${id}/members`)
  return data
}

export async function setSpaceMember(id: number, payload: { user_name: string; role: string }): Promise<SpaceMember> {
  const { data } = await request.put<SpaceMember>(`/knowledge-spaces/${id}/members`, payload)
  return data
}

export async function removeSpaceMember(id: number, memberUserId: number): Promise<{ message: string }> {
  const { data } = await request.delete(`/knowledge-spaces/${id}/members/${memberUserId}`)
  return data
}

export async function listSpaceAudit(id: number): Promise<{ items: SpaceAuditEntry[]; total: number }> {
  const { data } = await request.get(`/knowledge-spaces/${id}/audit`)
  return data
}

// ---------------- 空间内文档 ----------------

export interface SpaceDoc {
  id: number
  file_name: string
  file_type: string
  file_size: number
  chunk_count: number
  status: string
  status_label: string
  is_enabled: number
  error_msg: string | null
  category: string | null
  tags: string[]
  version: string | null
  source_type: string
  source_url: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SpaceDocListResponse {
  items: SpaceDoc[]
  total: number
  facets: {
    categories: string[]
    tags: string[]
    statuses: { key: string; label: string }[]
  }
}

export interface DocFilter {
  category?: string
  tag?: string
  doc_status?: string
  enabled?: boolean
}

export async function listSpaceDocs(spaceId: number, filter: DocFilter = {}): Promise<SpaceDocListResponse> {
  const { data } = await request.get<SpaceDocListResponse>(`/knowledge-spaces/${spaceId}/documents`, {
    params: filter,
  })
  return data
}

export async function uploadSpaceDocsBatch(spaceId: number, files: File[]): Promise<{
  message: string
  count: number
  items: any[]
}> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  const { data } = await request.post(`/knowledge-spaces/${spaceId}/documents/batch`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function crawlSpaceDocs(spaceId: number, urls: string[]): Promise<any> {
  const { data } = await request.post(`/knowledge-spaces/${spaceId}/documents/crawl`, { urls })
  return data
}

export async function updateSpaceDoc(
  spaceId: number,
  knowledgeId: number,
  patch: { category?: string; tags?: string[]; version?: string; is_enabled?: boolean },
): Promise<any> {
  const { data } = await request.patch(`/knowledge-spaces/${spaceId}/documents/${knowledgeId}`, patch)
  return data
}

export async function reindexSpaceDoc(spaceId: number, knowledgeId: number): Promise<any> {
  const { data } = await request.post(`/knowledge-spaces/${spaceId}/documents/${knowledgeId}/reindex`)
  return data
}

export async function deleteSpaceDoc(spaceId: number, knowledgeId: number): Promise<any> {
  const { data } = await request.delete(`/knowledge-spaces/${spaceId}/documents/${knowledgeId}`)
  return data
}
