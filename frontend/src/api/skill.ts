import request from '../utils/request'

/** 发布生命周期：draft(草稿) -> reviewing(待审核) -> published(已发布) -> retired(已退役)，
 * 跟后端 service/lifecycle.py 共用同一份值。只有 published 才会出现在公开能力商店里
 * （models/skill_dao.py::list_public_skills），draft/reviewing/retired 即便 is_public=1
 * 别人也看不到、也绑不了（service/access_control.py::can_bind_skill）。 */
export type SkillLifecycleStatus = 'draft' | 'reviewing' | 'published' | 'retired'

export const SKILL_LIFECYCLE_STATUSES: { value: SkillLifecycleStatus; label: string }[] = [
  { value: 'draft', label: '草稿' },
  { value: 'reviewing', label: '待审核' },
  { value: 'published', label: '已发布' },
  { value: 'retired', label: '已退役' },
]

export interface Skill {
  id: number
  user_id: number
  name: string
  description?: string
  config_file: string
  is_public: number
  created_at?: string
  config?: SkillConfig | null
  /** 能力商店里：发布者是管理员（= 经管理员审核上架） */
  is_official?: boolean
  lifecycle_status?: SkillLifecycleStatus
  /** 乐观锁版本号，改状态/内容时回传 expected_row_version 防止并发覆盖 */
  row_version?: number
}

export interface SkillTemplate {
  filename: string
  name: string
  description?: string
  tool_names?: string[]
  system_prompt?: string
  editable?: boolean
}

export interface SkillPermissions {
  network: boolean
  file_read: string[]
  exec: boolean
}

export interface SkillResource {
  name: string
  path: string
  exists: boolean
  size: number
  allowed: boolean
}

export interface SkillTool {
  name: string
  description: string
  parameters: Record<string, any>
  requires_context: boolean
  required_permissions?: Partial<SkillPermissions>
}

export interface SkillConfig {
  name: string
  description: string
  version: string
  tools: Array<{ name: string; defaults?: Record<string, any> }>
  tool_names: string[]
  system_prompt: string
  permissions: SkillPermissions
  resources: SkillResource[]
}

export interface SkillValidation {
  ok: boolean
  errors: string[]
  warnings: string[]
  tool_names: string[]
  missing_tool_names: string[]
  system_prompt_ready: boolean
  permissions: SkillPermissions
  resources: SkillResource[]
  resource_count: number
  allowed_resource_count: number
  skill_id: number
  name: string
  config_file: string
  is_public: number
  /** 带几个 Python 脚本；0 表示纯说明型能力 */
  script_count?: number
  /** none 无脚本 / ready 全部可运行 / partial 部分 / unsupported 都不行 / unknown 老数据没检查过 */
  script_status?: 'none' | 'ready' | 'partial' | 'unsupported' | 'unknown'
  script_runnable?: number
  script_missing_packages?: string[]
  script_network?: number
  script_system?: number
  /** 服务器上脚本沙箱是否已开启 */
  sandbox_enabled?: boolean
}

/** 后端返回包装：{code, msg, data} */
interface SkillResponse<T> {
  code: number
  msg: string
  data: T
}

const encodeTemplatePath = (filename: string) => filename.split('/').map(encodeURIComponent).join('/')

// ===== 查询 =====
export async function listUserSkills(): Promise<Skill[]> {
  const { data } = await request.get<SkillResponse<Skill[]>>('/skill/')
  return data.data
}

export async function listPublicSkills(): Promise<Skill[]> {
  const { data } = await request.get<SkillResponse<Skill[]>>('/skill/public')
  return data.data
}

export async function getSkill(skillId: number): Promise<Skill> {
  const { data } = await request.get<SkillResponse<Skill>>(`/skill/${skillId}`)
  return data.data
}

export interface SkillShelfResult {
  action: 'publish' | 'unpublish'
  changed: { id: number; name: string }[]
  unchanged: number[]
  skipped: { id: number; name: string; reason: string }[]
}

/** （管理员）批量上架 / 下架：上架 = 公开 + 已发布，出现在用户的技能中心；下架 = 退回草稿 */
export async function batchShelfSkills(skillIds: number[], action: 'publish' | 'unpublish'): Promise<SkillShelfResult> {
  const { data } = await request.post<SkillResponse<SkillShelfResult>>('/skill/admin/shelf', { skill_ids: skillIds, action })
  return data.data
}

export async function validateSkill(skillId: number): Promise<SkillValidation> {
  const { data } = await request.get<SkillResponse<SkillValidation>>(`/skill/${skillId}/validate`)
  return data.data
}

export async function listTemplates(): Promise<SkillTemplate[]> {
  const { data } = await request.get<SkillResponse<Array<SkillTemplate | string>>>('/skill/templates')
  return data.data.map((item) => {
    if (typeof item === 'string') {
      return { filename: item, name: item, description: '', tool_names: [], system_prompt: '' }
    }
    return item
  })
}

export async function listTools(): Promise<SkillTool[]> {
  const { data } = await request.get<SkillResponse<SkillTool[]>>('/skill/tools')
  return data.data
}

export async function getTemplate(filename: string): Promise<SkillTemplate> {
  const { data } = await request.get<SkillResponse<SkillTemplate>>(`/skill/templates/${encodeTemplatePath(filename)}`)
  return data.data
}

export async function createTemplate(payload: {
  name: string
  description: string
  system_prompt: string
  tool_names: string[]
}): Promise<SkillTemplate> {
  const { data } = await request.post<SkillResponse<SkillTemplate>>('/skill/templates', payload)
  return data.data
}

export async function updateTemplate(filename: string, payload: {
  name: string
  description: string
  system_prompt: string
  tool_names: string[]
}): Promise<SkillTemplate> {
  const { data } = await request.put<SkillResponse<SkillTemplate>>(`/skill/templates/${encodeTemplatePath(filename)}`, payload)
  return data.data
}

export async function deleteTemplate(filename: string): Promise<void> {
  await request.delete(`/skill/templates/${encodeTemplatePath(filename)}`)
}

// ===== 创建/更新/删除 =====
export async function createSkill(payload: {
  name: string
  description: string
  template_filename?: string
  is_public: number
  system_prompt?: string
  tool_names?: string[]
  permissions?: Partial<SkillPermissions>
}): Promise<Skill> {
  const { data } = await request.post<SkillResponse<Skill>>('/skill/', payload)
  return data.data
}

export async function updateSkill(skillId: number, payload: {
  name?: string
  description?: string
  template_filename?: string
  is_public?: number
  system_prompt?: string
  tool_names?: string[]
  permissions?: Partial<SkillPermissions>
  lifecycle_status?: SkillLifecycleStatus
  /** 乐观锁：传了就必须跟数据库当前 row_version 一致，否则后端返回 409 */
  expected_row_version?: number
}): Promise<Skill> {
  const { data } = await request.put<SkillResponse<Skill>>(`/skill/${skillId}`, payload)
  return data.data
}

export async function deleteSkill(skillId: number): Promise<void> {
  await request.delete(`/skill/${skillId}`)
}

export interface ImportedSkill extends Skill {
  /** 导入时的差异说明：哪些内容被忽略、为什么 */
  notes: string[]
  resource_count: number
  prompt_resource_count: number
  /** 保存下来的 Python 脚本数（沙箱开启后助手才能运行） */
  script_count: number
}

export interface SkillImportResult {
  imported: ImportedSkill[]
  failed: { name: string; error: string }[]
}

export async function importSkill(file: File, isPublic = 0): Promise<SkillImportResult> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('is_public', String(isPublic))
  const { data } = await request.post<SkillResponse<SkillImportResult>>('/skill/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data.data
}

/** 用当前用户自己的聊天模型，把 Skill 的名称和说明翻译成中文并保存 */
export async function translateSkill(skillId: number): Promise<Skill> {
  const { data } = await request.post<SkillResponse<Skill>>(`/skill/${skillId}/translate`)
  return data.data
}

export interface SkillVersion {
  id: number
  version_no: number
  name: string
  description: string
  /** 为什么存这一版，如「编辑前自动保存」 */
  note: string
  created_at: string | null
  size: number
}

/** （管理员）技能的历史版本，最新的在前，最多保留最近 20 个 */
export async function listSkillVersions(skillId: number): Promise<SkillVersion[]> {
  const { data } = await request.get<SkillResponse<SkillVersion[]>>(`/skill/${skillId}/versions`)
  return data.data
}

/** （管理员）把技能恢复到某个历史版本；恢复前会自动保存当前状态，所以恢复本身也能撤销 */
export async function restoreSkillVersion(skillId: number, versionId: number): Promise<{ restored_to: number; name: string }> {
  const { data } = await request.post<SkillResponse<{ restored_to: number; name: string }>>(
    `/skill/${skillId}/versions/${versionId}/restore`,
  )
  return data.data
}

/** （管理员）沙箱依赖变化后，重新检查所有 Skill 的脚本兼容性 */
export async function reanalyzeSkillScripts(): Promise<{ checked: number; changed: number; failed: number }> {
  const { data } = await request.post<SkillResponse<{ checked: number; changed: number; failed: number }>>('/skill/admin/reanalyze')
  return data.data
}

export async function importSkillFromGithub(url: string, isPublic = 0): Promise<SkillImportResult> {
  const { data } = await request.post<SkillResponse<SkillImportResult>>('/skill/import/github', {
    url,
    is_public: isPublic,
  })
  return data.data
}

export async function exportSkill(skillId: number): Promise<Blob> {
  const { data } = await request.get(`/skill/${skillId}/export`, { responseType: 'blob' })
  return data as Blob
}

export async function installPublicSkill(skillId: number): Promise<Skill> {
  const { data } = await request.post<SkillResponse<Skill>>(`/skill/${skillId}/install`)
  return data.data
}
