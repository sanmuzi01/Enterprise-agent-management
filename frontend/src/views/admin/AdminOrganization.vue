<template>
  <div class="p-6">
    <section class="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3" data-testid="enterprise-header">
      <div class="min-w-0">
        <p class="text-xs text-slate-500">企业名称</p>
        <p class="truncate text-base font-semibold text-slate-900">{{ enterprise?.name || '—' }}</p>
        <p class="mt-0.5 text-xs text-slate-400">本平台只服务这一家企业，注册的成员自动属于它。这里划分部门和部门负责人；个人账号与企业角色在「用户管理」，智能体在「企业智能体」。</p>
      </div>
      <div class="flex shrink-0 gap-2">
        <button
          @click="openRenameEnterprise"
          data-testid="enterprise-rename"
          class="rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          改名
        </button>
        <button
          @click="reloadAll"
          :disabled="loading"
          class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </section>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <!-- ============ 部门 ============ -->
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr]">
      <section class="rounded-lg border border-slate-200 bg-white">
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-sm font-semibold text-slate-900">部门列表</h2>
          <button
            @click="openCreateTeam"
            class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
          >
            <Plus :size="13" />
            新建部门
          </button>
        </div>
        <div v-if="loading" class="py-10 text-center text-sm text-slate-400">加载中…</div>
        <ul v-else class="divide-y divide-slate-100">
          <li
            v-for="t in teams"
            :key="t.id"
            @click="selectTeam(t)"
            :class="selectedTeam?.id === t.id ? 'bg-indigo-50/70' : 'hover:bg-slate-50'"
            class="cursor-pointer px-4 py-3"
          >
            <div class="flex items-center justify-between">
              <p class="font-medium text-slate-900">{{ t.name }}</p>
              <span
                class="rounded px-2 py-0.5 text-xs"
                :class="t.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'"
              >{{ t.status === 'active' ? '启用中' : '已停用' }}</span>
            </div>
            <p class="mt-1 text-xs text-slate-400">
              {{ t.member_count }} 名成员
              <span v-if="t.leads.length"> · 负责人 {{ t.leads.map(l => l.name).join('、') }}</span>
              <span v-else class="text-amber-600"> · 未设负责人</span>
              <span v-if="t.department_code"> · {{ DEPARTMENT_CODE_OPTIONS.find(o => o.value === t.department_code)?.label }}</span>
            </p>
          </li>
          <li v-if="!teams.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有创建任何部门</li>
        </ul>
      </section>

      <section v-if="selectedTeam" class="space-y-4">
        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold text-slate-900">{{ selectedTeam.name }}</h3>
            <div class="flex gap-1.5">
              <button @click="openRenameTeam(selectedTeam)" class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50">
                重命名
              </button>
              <button
                @click="toggleTeamStatus(selectedTeam)"
                class="rounded border px-2.5 py-1 text-xs"
                :class="selectedTeam.status === 'active' ? 'border-red-200 text-red-700 hover:bg-red-50' : 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'"
              >
                {{ selectedTeam.status === 'active' ? '停用部门' : '启用部门' }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="agentStatus" class="rounded-lg border border-slate-200 bg-white p-4" data-testid="dept-agent-status">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 class="text-sm font-semibold text-slate-900">部门专业 Agent</h4>
              <p class="mt-0.5 text-xs text-slate-500">按业务类型自动配置：{{ agentStatus.template_name }}</p>
            </div>
            <span class="rounded px-2 py-0.5 text-xs" :class="agentStateClass(agentStatus.state)">{{ agentStatus.state_label }}</span>
          </div>
          <p v-if="agentStatus.agent" class="mt-2 text-sm text-slate-700">
            {{ agentStatus.agent.name }}<span class="ml-1 text-xs text-slate-400">#{{ agentStatus.agent.id }} · {{ modelDisplayName(agentStatus.agent.model_name) }}</span>
          </p>
          <ul v-if="agentStatus.issues.length" class="mt-2 list-inside list-disc text-xs text-amber-700">
            <li v-for="(issue, i) in agentStatus.issues" :key="i">{{ issue }}</li>
          </ul>
          <div v-if="agentStatus.state !== 'team_disabled'" class="mt-3 flex gap-2">
            <button v-if="agentStatus.state === 'needs_repair'" @click="onRepairAgent" :disabled="acting"
              class="rounded bg-amber-600 px-2.5 py-1 text-xs text-white hover:bg-amber-700 disabled:opacity-50">一键修复</button>
            <button v-if="agentStatus.state === 'pending_publish'" @click="onPublishAgent" :disabled="acting"
              class="rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50">发布给部门员工</button>
          </div>
        </div>

        <div class="rounded-lg border border-slate-200 bg-white">
          <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h4 class="text-sm font-semibold text-slate-900">部门成员</h4>
            <button
              @click="openAddMember"
              class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
            >
              <UserPlus :size="13" />
              分配成员
            </button>
          </div>
          <table class="w-full text-left text-sm">
            <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
              <tr>
                <th class="px-4 py-2.5 font-medium">姓名</th>
                <th class="px-4 py-2.5 font-medium">部门角色</th>
                <th class="px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in teamMembers" :key="m.user_id" class="border-b border-slate-100">
                <td class="px-4 py-2.5 text-slate-900">{{ m.name }}<span class="ml-1 text-xs text-slate-400">#{{ m.user_id }}</span></td>
                <td class="px-4 py-2.5">
                  <select
                    :value="m.role_code"
                    @change="onChangeMemberRole(m, ($event.target as HTMLSelectElement).value)"
                    class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-indigo-500"
                  >
                    <option v-for="r in roleCatalog.team" :key="r.code" :value="r.code">{{ r.name }}</option>
                  </select>
                </td>
                <td class="px-4 py-2.5">
                  <button @click="onRemoveMember(m)" class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">
                    移出部门
                  </button>
                </td>
              </tr>
              <tr v-if="!teamMembers.length"><td colspan="3" class="px-4 py-8 text-center text-slate-400">这个部门还没有成员</td></tr>
            </tbody>
          </table>
        </div>

        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <h4 class="mb-3 text-sm font-semibold text-slate-900">权限关系</h4>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p class="mb-1.5 text-xs font-medium text-slate-500">划分给本部门的知识库</p>
              <ul v-if="permissions?.knowledge_spaces.length" class="space-y-1">
                <li v-for="s in permissions.knowledge_spaces" :key="s.id" class="text-sm text-slate-700">{{ s.name }}</li>
              </ul>
              <p v-else class="text-sm text-slate-400">暂无</p>
            </div>
            <div>
              <p class="mb-1.5 text-xs font-medium text-slate-500">划分给本部门的智能体</p>
              <ul v-if="permissions?.agents.length" class="space-y-1">
                <li v-for="a in permissions.agents" :key="a.id" class="text-sm text-slate-700">
                  {{ a.name }}<span class="text-xs text-slate-400">（{{ departmentCodeLabel(a.department_code) }}）</span>
                </li>
              </ul>
              <p v-else class="text-sm text-slate-400">暂无</p>
            </div>
          </div>
        </div>
      </section>
      <section v-else class="flex items-center justify-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-400">
        选择左侧一个部门查看详情
      </section>
    </div>

    <!-- ============ 弹窗：新建/重命名部门 ============ -->
    <div v-if="teamDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="teamDialog.visible = false">
      <div class="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ teamDialog.editing ? '重命名部门' : '新建部门' }}</h3>
        <input
          v-model="teamDialog.name"
          type="text"
          placeholder="部门名称"
          class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500"
          @keyup.enter="submitTeamDialog"
        />
        <label class="mt-3 block text-xs font-medium text-slate-600">业务类型</label>
        <p class="mb-1 text-[11px] text-slate-400">决定部门工作台的业务模块；保存后会按业务类型自动配置部门专业 Agent（草稿，发布后员工可用）</p>
        <select
          v-model="teamDialog.departmentCode"
          class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500"
        >
          <option v-for="o in DEPARTMENT_CODE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="teamDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button
            @click="submitTeamDialog"
            :disabled="acting || !teamDialog.name.trim()"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：企业改名 ============ -->
    <div v-if="enterpriseDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="enterpriseDialog.visible = false">
      <div class="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">企业改名</h3>
        <input
          v-model="enterpriseDialog.name"
          type="text"
          placeholder="企业名称"
          class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500"
          @keyup.enter="submitEnterpriseName"
        />
        <p v-if="enterpriseDialog.error" class="mt-2 text-xs text-red-600">{{ enterpriseDialog.error }}</p>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="enterpriseDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button
            @click="submitEnterpriseName"
            :disabled="acting || !enterpriseDialog.name.trim()"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：分配部门成员 ============ -->
    <div v-if="memberDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="memberDialog.visible = false">
      <div class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">分配部门成员</h3>
        <div class="space-y-3">
          <div class="relative">
            <Search class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" :size="14" />
            <input
              v-model="userQuery"
              @input="searchUsers"
              type="text"
              placeholder="搜索用户名"
              class="h-9 w-full rounded border border-slate-300 pl-8 pr-3 text-sm outline-none focus:border-indigo-500"
            />
          </div>
          <ul v-if="userResults.length" class="max-h-40 overflow-y-auto rounded border border-slate-100">
            <li
              v-for="u in userResults"
              :key="u.id"
              @click="memberDialog.userId = u.id"
              :class="memberDialog.userId === u.id ? 'bg-indigo-50' : 'hover:bg-slate-50'"
              class="cursor-pointer px-3 py-2 text-sm text-slate-700"
            >
              {{ u.name }} <span class="text-xs text-slate-400">#{{ u.id }}</span>
            </li>
          </ul>
          <div v-if="memberDialog.userId">
            <label class="mb-1 block text-xs text-slate-500">部门角色</label>
            <select v-model="memberDialog.roleCode" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
              <option v-for="r in roleCatalog.team" :key="r.code" :value="r.code">{{ r.name }}</option>
            </select>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="memberDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button
            @click="submitMemberDialog"
            :disabled="acting || !memberDialog.userId"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            确认添加
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { modelDisplayName, departmentCodeLabel } from '../../utils/displayNames'
import { onMounted, ref } from 'vue'
import { Plus, RefreshCcw, Search, UserPlus } from 'lucide-vue-next'
import * as orgApi from '../../api/organizationAdmin'
import type {
  OrgTeam, OrgTeamMember, EnterpriseRoleCatalog, TeamPermissions, Enterprise,
} from '../../api/organizationAdmin'
import { listAdminUsers, type AdminUser } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const loading = ref(true)
const acting = ref(false)
const errorMsg = ref('')

const enterprise = ref<Enterprise | null>(null)
const teams = ref<OrgTeam[]>([])
const roleCatalog = ref<EnterpriseRoleCatalog>({ organization: [], team: [] })

const selectedTeam = ref<OrgTeam | null>(null)
const teamMembers = ref<OrgTeamMember[]>([])
const permissions = ref<TeamPermissions | null>(null)

const loadTeams = async () => {
  teams.value = await orgApi.listOrgTeams()
}

const reloadAll = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const [roles, ent] = await Promise.all([orgApi.getEnterpriseRoles(), orgApi.getEnterprise()])
    roleCatalog.value = roles
    enterprise.value = ent
    await loadTeams()
    if (selectedTeam.value) {
      const stillThere = teams.value.find(t => t.id === selectedTeam.value!.id)
      if (stillThere) await selectTeam(stillThere)
      else selectedTeam.value = null
    }
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载组织架构失败')
  } finally {
    loading.value = false
  }
}

const selectTeam = async (t: OrgTeam) => {
  selectedTeam.value = t
  try {
    const [members, perms, status] = await Promise.all([
      orgApi.listTeamMembers(t.id),
      orgApi.getTeamPermissions(t.id),
      orgApi.getTeamAgentStatus(t.id),
    ])
    teamMembers.value = members
    permissions.value = perms
    agentStatus.value = status
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载部门详情失败')
  }
}

// ---- 企业名称 ----

const enterpriseDialog = ref({ visible: false, name: '', error: '' })

const openRenameEnterprise = () => {
  enterpriseDialog.value = { visible: true, name: enterprise.value?.name || '', error: '' }
}

const submitEnterpriseName = async () => {
  acting.value = true
  enterpriseDialog.value.error = ''
  try {
    enterprise.value = await orgApi.renameEnterprise(enterpriseDialog.value.name.trim())
    enterpriseDialog.value.visible = false
  } catch (e: any) {
    enterpriseDialog.value.error = getErrorMessage(e, '改名失败')
  } finally {
    acting.value = false
  }
}

// ---- 部门专业 Agent ----

const agentStatus = ref<orgApi.DepartmentAgentStatus | null>(null)

const agentStateClass = (state: orgApi.DepartmentAgentState) => ({
  ready: 'bg-emerald-50 text-emerald-700',
  pending_publish: 'bg-sky-50 text-sky-700',
  needs_repair: 'bg-amber-50 text-amber-700',
  team_disabled: 'bg-slate-100 text-slate-500',
}[state])

const runAgentAction = async (action: (teamId: number) => Promise<orgApi.DepartmentAgentStatus>, fallback: string) => {
  if (!selectedTeam.value) return
  acting.value = true
  try {
    agentStatus.value = await action(selectedTeam.value.id)
    await Promise.all([loadTeams(), selectTeam(selectedTeam.value)])
  } catch (e: any) {
    alert(getErrorMessage(e, fallback))
  } finally {
    acting.value = false
  }
}

const onRepairAgent = () => runAgentAction(orgApi.repairTeamAgent, '修复失败')
const onPublishAgent = () => runAgentAction(orgApi.publishTeamAgent, '发布失败')

// ---- 部门 ----

const DEPARTMENT_CODE_OPTIONS = [
  { value: '', label: '未分配业务类型' },
  { value: 'hr', label: '人事' },
  { value: 'procurement', label: '采购' },
  { value: 'sales', label: '销售' },
  { value: 'finance', label: '财务' },
  { value: 'it', label: 'IT' },
]

const teamDialog = ref<{ visible: boolean; editing: OrgTeam | null; name: string; departmentCode: string }>({
  visible: false, editing: null, name: '', departmentCode: '',
})

const openCreateTeam = () => {
  teamDialog.value = { visible: true, editing: null, name: '', departmentCode: '' }
}

const openRenameTeam = (t: OrgTeam) => {
  teamDialog.value = { visible: true, editing: t, name: t.name, departmentCode: t.department_code || '' }
}

const submitTeamDialog = async () => {
  acting.value = true
  try {
    const departmentCode = teamDialog.value.departmentCode || null
    let teamId: number
    if (teamDialog.value.editing) {
      teamId = teamDialog.value.editing.id
      await orgApi.updateOrgTeam(teamId, {
        name: teamDialog.value.name.trim(), department_code: departmentCode,
      })
    } else {
      teamId = (await orgApi.createOrgTeam(teamDialog.value.name.trim(), departmentCode)).id
    }
    teamDialog.value.visible = false
    await loadTeams()
    const saved = teams.value.find(t => t.id === teamId)
    if (saved) await selectTeam(saved)
  } catch (e: any) {
    alert(getErrorMessage(e, '保存失败'))
  } finally {
    acting.value = false
  }
}

const toggleTeamStatus = async (t: OrgTeam) => {
  const next = t.status === 'active' ? 'disabled' : 'active'
  if (next === 'disabled' && !confirm(`确认停用部门「${t.name}」？停用后该部门成员的部门相关操作会被拒绝，部门已发布的 Agent 会同步退役。`)) return
  try {
    await orgApi.updateOrgTeam(t.id, { status: next })
    await loadTeams()
    const saved = teams.value.find(x => x.id === t.id)
    if (saved && selectedTeam.value?.id === t.id) await selectTeam(saved)
  } catch (e: any) {
    alert(getErrorMessage(e, '操作失败'))
  }
}

const onChangeMemberRole = async (m: OrgTeamMember, roleCode: string) => {
  try {
    await orgApi.updateTeamMemberRole(selectedTeam.value!.id, m.user_id, roleCode)
    await selectTeam(selectedTeam.value!)
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '修改角色失败'))
  }
}

const onRemoveMember = async (m: OrgTeamMember) => {
  if (!confirm(`确认将「${m.name}」移出部门？`)) return
  try {
    await orgApi.removeTeamMember(selectedTeam.value!.id, m.user_id)
    await selectTeam(selectedTeam.value!)
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '移除失败'))
  }
}

// ---- 分配部门成员 ----

const memberDialog = ref<{ visible: boolean; userId: number | null; roleCode: string }>({
  visible: false, userId: null, roleCode: 'member',
})
const userQuery = ref('')
const userResults = ref<AdminUser[]>([])

const searchUsers = async () => {
  if (!userQuery.value.trim()) {
    userResults.value = []
    return
  }
  try {
    const page = await listAdminUsers({ search: userQuery.value.trim(), limit: 10 })
    userResults.value = page.items
  } catch {
    userResults.value = []
  }
}

const openAddMember = () => {
  memberDialog.value = { visible: true, userId: null, roleCode: 'member' }
  userQuery.value = ''
  userResults.value = []
}

const submitMemberDialog = async () => {
  if (!memberDialog.value.userId) return
  acting.value = true
  try {
    await orgApi.addTeamMember(selectedTeam.value!.id, memberDialog.value.userId, memberDialog.value.roleCode)
    await selectTeam(selectedTeam.value!)
    await loadTeams()
    memberDialog.value.visible = false
  } catch (e: any) {
    alert(getErrorMessage(e, '添加失败'))
  } finally {
    acting.value = false
  }
}

onMounted(reloadAll)
</script>
