<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-5">
      <p class="text-xs text-slate-500">管理企业部门、成员分配、部门负责人和企业角色。</p>
      <button
        @click="reloadAll"
        :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <div class="mb-5 inline-flex gap-1 rounded bg-slate-100 p-1 text-sm">
      <button
        v-for="t in tabs"
        :key="t.value"
        @click="activeTab = t.value"
        :class="activeTab === t.value ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
        class="h-8 rounded px-4 transition"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- ============ 部门管理 ============ -->
    <div v-if="activeTab === 'teams'" class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr]">
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
              <p class="mb-1.5 text-xs font-medium text-slate-500">绑定的知识库空间</p>
              <ul v-if="permissions?.knowledge_spaces.length" class="space-y-1">
                <li v-for="s in permissions.knowledge_spaces" :key="s.id" class="text-sm text-slate-700">{{ s.name }}</li>
              </ul>
              <p v-else class="text-sm text-slate-400">暂无</p>
            </div>
            <div>
              <p class="mb-1.5 text-xs font-medium text-slate-500">绑定的部门 Agent</p>
              <ul v-if="permissions?.agents.length" class="space-y-1">
                <li v-for="a in permissions.agents" :key="a.id" class="text-sm text-slate-700">
                  {{ a.name }}<span class="text-xs text-slate-400"> ({{ a.department_code }})</span>
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

    <!-- ============ 企业成员 ============ -->
    <div v-else class="rounded-lg border border-slate-200 bg-white">
      <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 class="text-sm font-semibold text-slate-900">企业成员</h2>
        <button
          @click="openAddOrgMember"
          class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
        >
          <UserPlus :size="13" />
          添加企业成员
        </button>
      </div>
      <table class="w-full text-left text-sm">
        <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="px-4 py-2.5 font-medium">姓名</th>
            <th class="px-4 py-2.5 font-medium">企业角色</th>
            <th class="px-4 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in orgMembers" :key="m.user_id" class="border-b border-slate-100">
            <td class="px-4 py-2.5 text-slate-900">{{ m.name }}<span class="ml-1 text-xs text-slate-400">#{{ m.user_id }}</span></td>
            <td class="px-4 py-2.5">
              <select
                :value="m.role_code"
                @change="onChangeOrgMemberRole(m, ($event.target as HTMLSelectElement).value)"
                class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-indigo-500"
              >
                <option v-for="r in roleCatalog.organization" :key="r.code" :value="r.code">{{ r.name }}</option>
              </select>
            </td>
            <td class="px-4 py-2.5">
              <button
                @click="onToggleOrgMemberStatus(m)"
                class="rounded px-2 py-0.5 text-xs"
                :class="m.status === 'active' ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'"
              >
                {{ m.status === 'active' ? '启用中' : '已停用' }}
              </button>
            </td>
            <td class="px-4 py-2.5">
              <button @click="onRemoveOrgMember(m)" class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">
                移出企业
              </button>
            </td>
          </tr>
          <tr v-if="!orgMembers.length"><td colspan="4" class="px-4 py-8 text-center text-slate-400">还没有企业成员</td></tr>
        </tbody>
      </table>
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

    <!-- ============ 弹窗：分配部门成员 / 添加企业成员（共用搜索用户逻辑） ============ -->
    <div v-if="memberDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="memberDialog.visible = false">
      <div class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ memberDialog.mode === 'team' ? '分配部门成员' : '添加企业成员' }}</h3>
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
            <label class="mb-1 block text-xs text-slate-500">
              {{ memberDialog.mode === 'team' ? '部门角色' : '企业角色' }}
            </label>
            <select v-model="memberDialog.roleCode" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
              <option
                v-for="r in (memberDialog.mode === 'team' ? roleCatalog.team : roleCatalog.organization)"
                :key="r.code" :value="r.code"
              >{{ r.name }}</option>
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
import { onMounted, ref } from 'vue'
import { Plus, RefreshCcw, Search, UserPlus } from 'lucide-vue-next'
import * as orgApi from '../../api/organizationAdmin'
import type { OrgTeam, OrgTeamMember, OrgMember, EnterpriseRoleCatalog, TeamPermissions } from '../../api/organizationAdmin'
import { listAdminUsers, type AdminUser } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const tabs: { value: 'teams' | 'members'; label: string }[] = [
  { value: 'teams', label: '部门管理' },
  { value: 'members', label: '企业成员' },
]
const activeTab = ref<'teams' | 'members'>('teams')

const loading = ref(true)
const acting = ref(false)
const errorMsg = ref('')

const teams = ref<OrgTeam[]>([])
const orgMembers = ref<OrgMember[]>([])
const roleCatalog = ref<EnterpriseRoleCatalog>({ organization: [], team: [] })

const selectedTeam = ref<OrgTeam | null>(null)
const teamMembers = ref<OrgTeamMember[]>([])
const permissions = ref<TeamPermissions | null>(null)

const loadTeams = async () => {
  teams.value = await orgApi.listOrgTeams()
}

const loadOrgMembers = async () => {
  orgMembers.value = await orgApi.listOrgMembers()
}

const reloadAll = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    roleCatalog.value = await orgApi.getEnterpriseRoles()
    await Promise.all([loadTeams(), loadOrgMembers()])
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
    const [members, perms] = await Promise.all([
      orgApi.listTeamMembers(t.id),
      orgApi.getTeamPermissions(t.id),
    ])
    teamMembers.value = members
    permissions.value = perms
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载部门详情失败')
  }
}

// ---- 部门 ----

const teamDialog = ref<{ visible: boolean; editing: OrgTeam | null; name: string }>({
  visible: false, editing: null, name: '',
})

const openCreateTeam = () => {
  teamDialog.value = { visible: true, editing: null, name: '' }
}

const openRenameTeam = (t: OrgTeam) => {
  teamDialog.value = { visible: true, editing: t, name: t.name }
}

const submitTeamDialog = async () => {
  acting.value = true
  try {
    if (teamDialog.value.editing) {
      await orgApi.updateOrgTeam(teamDialog.value.editing.id, { name: teamDialog.value.name.trim() })
    } else {
      await orgApi.createOrgTeam(teamDialog.value.name.trim())
    }
    teamDialog.value.visible = false
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '保存失败'))
  } finally {
    acting.value = false
  }
}

const toggleTeamStatus = async (t: OrgTeam) => {
  const next = t.status === 'active' ? 'disabled' : 'active'
  if (next === 'disabled' && !confirm(`确认停用部门「${t.name}」？停用后该部门成员的部门相关操作会被拒绝。`)) return
  try {
    await orgApi.updateOrgTeam(t.id, { status: next })
    await loadTeams()
    if (selectedTeam.value?.id === t.id) selectedTeam.value.status = next
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

// ---- 企业成员 ----

const onChangeOrgMemberRole = async (m: OrgMember, roleCode: string) => {
  try {
    await orgApi.updateOrgMember(m.user_id, { role_code: roleCode })
    await loadOrgMembers()
  } catch (e: any) {
    alert(getErrorMessage(e, '修改角色失败'))
  }
}

const onToggleOrgMemberStatus = async (m: OrgMember) => {
  const next = m.status === 'active' ? 'disabled' : 'active'
  try {
    await orgApi.updateOrgMember(m.user_id, { status: next })
    await loadOrgMembers()
  } catch (e: any) {
    alert(getErrorMessage(e, '操作失败'))
  }
}

const onRemoveOrgMember = async (m: OrgMember) => {
  if (!confirm(`确认将「${m.name}」移出企业？该操作会连带清除他在所有部门里的身份。`)) return
  try {
    await orgApi.removeOrgMember(m.user_id)
    await loadOrgMembers()
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '移除失败'))
  }
}

// ---- 添加成员弹窗（部门/企业共用） ----

const memberDialog = ref<{ visible: boolean; mode: 'team' | 'org'; userId: number | null; roleCode: string }>({
  visible: false, mode: 'team', userId: null, roleCode: 'member',
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
  memberDialog.value = { visible: true, mode: 'team', userId: null, roleCode: 'member' }
  userQuery.value = ''
  userResults.value = []
}

const openAddOrgMember = () => {
  memberDialog.value = { visible: true, mode: 'org', userId: null, roleCode: 'member' }
  userQuery.value = ''
  userResults.value = []
}

const submitMemberDialog = async () => {
  if (!memberDialog.value.userId) return
  acting.value = true
  try {
    if (memberDialog.value.mode === 'team') {
      await orgApi.addTeamMember(selectedTeam.value!.id, memberDialog.value.userId, memberDialog.value.roleCode)
      await selectTeam(selectedTeam.value!)
      await loadTeams()
    } else {
      await orgApi.addOrgMember(memberDialog.value.userId, memberDialog.value.roleCode)
      await loadOrgMembers()
    }
    memberDialog.value.visible = false
  } catch (e: any) {
    alert(getErrorMessage(e, '添加失败'))
  } finally {
    acting.value = false
  }
}

onMounted(reloadAll)
</script>
