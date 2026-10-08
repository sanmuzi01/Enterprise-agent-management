<template>
  <div class="p-6">
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <p class="text-xs text-slate-500">知识库统一在这里创建，再划分给需要的部门或全企业；被划分到的部门成员自动可读，绝密资料需要单独加人。</p>
      <div class="flex items-center gap-2">
        <button
          @click="openCreate()"
          data-testid="space-create"
          class="inline-flex items-center gap-1.5 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700"
        >
          <Plus :size="14" />
          新建知识库
        </button>
        <button
          @click="load"
          :disabled="loading"
          class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </div>

    <p v-if="actionErr" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ actionErr }}</p>

    <div class="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <!-- 按部门看 -->
      <nav class="rounded-lg border border-slate-200 bg-white p-2 lg:self-start" data-testid="space-department-nav" aria-label="按部门筛选">
        <template v-for="entry in scopeEntries" :key="entry.key">
          <p v-if="entry.group" class="px-3 pb-1 pt-3 text-[11px] font-medium text-slate-400">{{ entry.group }}</p>
          <button @click="selectScope(entry.key)"
            class="flex w-full items-center justify-between gap-2 rounded px-3 py-2 text-left text-sm transition-colors"
            :class="scope === entry.key ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-700 hover:bg-slate-50'">
            <span class="truncate">{{ entry.label }}</span>
            <span class="shrink-0 rounded-full bg-slate-100 px-2 text-xs text-slate-500">{{ entry.count }}</span>
          </button>
        </template>
      </nav>

      <section class="min-w-0">
        <div v-if="loading && !items.length" class="py-12 text-center text-sm text-slate-400">加载中…</div>
        <div v-else-if="err" class="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ err }}</div>
        <div v-else class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table class="min-w-full text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500">
              <tr>
                <th class="px-3 py-2 text-left">空间</th>
                <th class="px-3 py-2 text-left">划分给</th>
                <th class="px-3 py-2 text-left">密级</th>
                <th class="px-3 py-2 text-left">所有者</th>
                <th class="px-3 py-2 text-right">文档</th>
                <th class="px-3 py-2 text-right">成员</th>
                <th class="px-3 py-2 text-right">绑定助手</th>
                <th class="px-3 py-2 text-right">健康分</th>
                <th class="px-3 py-2 text-left">状态</th>
                <th class="px-3 py-2 text-left">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="s in items" :key="s.id" class="hover:bg-slate-50">
                <td class="px-3 py-2">
                  <div class="font-medium text-slate-800">{{ s.name }}</div>
                  <div class="text-xs text-slate-400">编号 {{ s.id }}</div>
                </td>
                <td class="px-3 py-2 text-slate-600">
                  <div v-if="s.scope_type === 'department'" class="flex flex-wrap gap-1">
                    <span v-for="d in s.departments" :key="d.id" class="rounded bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">{{ d.name }}</span>
                  </div>
                  <span v-else-if="s.scope_type === 'enterprise'" class="rounded bg-purple-50 px-2 py-0.5 text-xs text-purple-700">全企业</span>
                  <span v-else class="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">未划分</span>
                </td>
                <td class="px-3 py-2">
                  <span class="rounded px-2 py-0.5 text-xs" :class="sensitivityClass(s.sensitivity)">{{ s.sensitivity_label }}</span>
                </td>
                <td class="px-3 py-2 text-slate-600">{{ s.owner_name || ('用户 ' + s.owner_user_id) }}</td>
                <td class="px-3 py-2 text-right">{{ s.doc_count }}</td>
                <td class="px-3 py-2 text-right">{{ s.member_count }}</td>
                <td class="px-3 py-2 text-right">{{ s.bound_agent_count }}</td>
                <td class="px-3 py-2 text-right">
                  <span :class="healthColor(s.health_score)">{{ s.health_score ?? '—' }}</span>
                </td>
                <td class="px-3 py-2">
                  <div class="flex flex-wrap gap-1">
                    <span class="rounded px-2 py-0.5 text-xs"
                      :class="s.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'">
                      {{ s.status === 'active' ? '启用中' : '已归档' }}
                    </span>
                    <span v-if="!s.is_enabled" class="rounded bg-red-50 px-2 py-0.5 text-xs text-red-600">已停用</span>
                  </div>
                </td>
                <td class="px-3 py-2">
                  <div class="flex flex-wrap gap-1.5">
                    <button @click="openAssign(s)" data-testid="space-assign"
                      class="rounded border border-indigo-200 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50">划分</button>
                    <button
                      @click="toggleEnabled(s)"
                      :disabled="actingId === s.id"
                      class="rounded border px-2 py-1 text-xs transition-colors disabled:opacity-50"
                      :class="s.is_enabled
                        ? 'border-red-200 text-red-700 hover:bg-red-50'
                        : 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'"
                    >{{ s.is_enabled ? '停用' : '启用' }}</button>
                    <button
                      @click="toggleArchived(s)"
                      :disabled="actingId === s.id"
                      class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >{{ s.status === 'active' ? '归档' : '恢复' }}</button>
                  </div>
                </td>
              </tr>
              <tr v-if="items.length === 0">
                <td colspan="10" class="px-3 py-10 text-center text-slate-400">
                  {{ emptyText }}
                  <button v-if="selectedTeamId" @click="openCreate(selectedTeamId)" class="ml-1 text-indigo-600 hover:underline">为它新建一个</button>
                </td>
              </tr>
            </tbody>
          </table>
          <AdminPagination :total="total" :limit="limit" :offset="offset" @update:offset="onPageChange" />
        </div>
      </section>
    </div>

    <!-- 新建 / 划分 -->
    <div v-if="dialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="dialog.visible = false">
      <div class="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-5 shadow-xl" data-testid="space-dialog">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ dialog.target ? `划分：${dialog.target.name}` : '新建知识库' }}</h3>
        <div class="space-y-4">
          <div v-if="!dialog.target">
            <label for="space-name" class="mb-1 block text-xs text-slate-500">名称</label>
            <input id="space-name" v-model="dialog.name" maxlength="120" placeholder="例如：销售部报价资料"
              class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
          </div>

          <fieldset>
            <legend class="mb-1 block text-xs text-slate-500">划分给谁</legend>
            <div class="space-y-2 text-sm">
              <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
                <input v-model="dialog.scope" type="radio" value="unassigned" class="mt-0.5" />
                <span><span class="block font-medium text-slate-800">暂不划分</span>
                  <span class="block text-xs text-slate-500">只有所有者和被加入的成员能看到。</span></span>
              </label>
              <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
                <input v-model="dialog.scope" type="radio" value="departments" class="mt-0.5" data-testid="space-scope-departments" />
                <span class="min-w-0 flex-1"><span class="block font-medium text-slate-800">指定部门（可多选）</span>
                  <span class="block text-xs text-slate-500">这些部门的成员自动可读，部门负责人还能维护文档。</span>
                  <div v-if="dialog.scope === 'departments'" class="mt-2 max-h-48 space-y-2 overflow-y-auto rounded border border-slate-200 bg-white p-2" data-testid="space-team-list">
                    <div v-for="group in departmentGroups" :key="group.name">
                      <p v-if="multipleOrganizations" class="px-1 pb-1 text-[11px] font-medium text-slate-400">{{ group.name }}</p>
                      <label v-for="d in group.items" :key="d.id" class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-slate-50">
                        <input type="checkbox" :value="d.id" v-model="dialog.teamIds" />
                        <span class="truncate">{{ d.name }}</span>
                      </label>
                    </div>
                    <p v-if="!departmentGroups.length" class="px-1 text-xs text-slate-400">还没有可选的部门。</p>
                  </div>
                </span>
              </label>
              <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
                <input v-model="dialog.scope" type="radio" value="enterprise" class="mt-0.5" />
                <span><span class="block font-medium text-slate-800">全企业</span>
                  <span class="block text-xs text-slate-500">所有在职成员自动可读，例如公司制度。</span></span>
              </label>
            </div>
          </fieldset>

          <div>
            <label for="space-sensitivity" class="mb-1 block text-xs text-slate-500">密级</label>
            <select id="space-sensitivity" v-model="dialog.sensitivity"
              class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
              <option value="public">公开</option>
              <option value="internal">内部</option>
              <option value="confidential">机密</option>
              <option value="restricted">绝密</option>
            </select>
            <p class="mt-1 text-[11px] text-slate-400">{{ sensitivityHint }}</p>
          </div>
          <div v-if="!dialog.target">
            <label for="space-desc" class="mb-1 block text-xs text-slate-500">描述（可选）</label>
            <textarea id="space-desc" v-model="dialog.description" rows="2" maxlength="500"
              class="w-full resize-none rounded border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"></textarea>
          </div>
          <p v-if="dialog.error" class="text-xs text-red-600">{{ dialog.error }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="dialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button @click="submitDialog" :disabled="!canSubmit" data-testid="space-dialog-save"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">
            {{ dialog.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, RefreshCcw } from 'lucide-vue-next'
import AdminPagination from '../../components/admin/AdminPagination.vue'
import {
  createAdminSpace, listAdminKnowledgeSpaces, updateAdminSpace, updateAdminSpaceStatus,
  type AdminKnowledgeSpace, type AdminSpaceDepartment, type SpaceAssignScope,
} from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const loading = ref(true)
const err = ref('')
const actionErr = ref('')
const actingId = ref<number | null>(null)
const items = ref<AdminKnowledgeSpace[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)

// 筛选：all = 全部；team:<编号> = 某个部门；enterprise = 全企业；unassigned = 还没划分
const scope = ref('all')
const departments = ref<AdminSpaceDepartment[]>([])
const enterpriseCount = ref(0)
const unassignedCount = ref(0)
const allCount = ref(0)

const multipleOrganizations = computed(() => new Set(departments.value.map((d) => d.organization_id)).size > 1)

const scopeEntries = computed(() => {
  const entries: { key: string; label: string; count: number; group?: string }[] = [
    { key: 'all', label: '全部知识库', count: allCount.value },
  ]
  let lastOrganization: number | null = null
  for (const d of departments.value) {
    const startsGroup = multipleOrganizations.value && d.organization_id !== lastOrganization
    lastOrganization = d.organization_id
    entries.push({ key: `team:${d.id}`, label: d.name, count: d.space_count, group: startsGroup ? d.organization_name : undefined })
  }
  entries.push({ key: 'enterprise', label: '全企业', count: enterpriseCount.value, group: '按范围' })
  entries.push({ key: 'unassigned', label: '未划分', count: unassignedCount.value })
  return entries
})

// 对话框里的部门多选，按企业分组
const departmentGroups = computed(() => {
  const groups = new Map<string, AdminSpaceDepartment[]>()
  for (const d of departments.value) {
    const list = groups.get(d.organization_name) || []
    list.push(d)
    groups.set(d.organization_name, list)
  }
  return [...groups].map(([name, list]) => ({ name, items: list }))
})

const selectedTeamId = computed(() => (scope.value.startsWith('team:') ? Number(scope.value.slice(5)) : null))
const emptyText = computed(() => {
  if (selectedTeamId.value) return '这个部门还没有被划分知识库。'
  if (scope.value === 'enterprise') return '还没有划分给全企业的知识库。'
  if (scope.value === 'unassigned') return '没有未划分的知识库。'
  return '暂无知识库空间'
})

const sensitivityClass = (level: string) =>
  level === 'restricted' ? 'bg-red-50 text-red-700'
    : level === 'confidential' ? 'bg-amber-50 text-amber-700'
    : level === 'public' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'

const healthColor = (n: number | null) =>
  n == null ? 'text-slate-300'
    : n >= 80 ? 'text-emerald-600 font-semibold'
    : n >= 55 ? 'text-amber-600 font-semibold' : 'text-red-600 font-semibold'

const load = async () => {
  loading.value = true
  err.value = ''
  try {
    const page = await listAdminKnowledgeSpaces({ limit: limit.value, offset: offset.value, scope: scope.value })
    items.value = page.items
    total.value = page.total
    departments.value = page.departments
    enterpriseCount.value = page.enterprise_count
    unassignedCount.value = page.unassigned_count
    allCount.value = page.all_count
  } catch (e: any) {
    err.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

const selectScope = (key: string) => {
  scope.value = key
  offset.value = 0
  void load()
}

const onPageChange = (nextOffset: number) => {
  offset.value = nextOffset
  load()
}

// ---------- 新建 / 划分 ----------
const dialog = ref({
  visible: false,
  target: null as AdminKnowledgeSpace | null,
  name: '',
  description: '',
  scope: 'unassigned' as SpaceAssignScope,
  teamIds: [] as number[],
  sensitivity: 'internal',
  saving: false,
  error: '',
})

const canSubmit = computed(() => {
  const d = dialog.value
  if (d.saving) return false
  if (!d.target && !d.name.trim()) return false
  return d.scope !== 'departments' || d.teamIds.length > 0
})

const sensitivityHint = computed(() => {
  switch (dialog.value.sensitivity) {
    case 'public': return '公开：可以发给任何模型。'
    case 'confidential': return '机密：只会发给企业批准的模型，不会发给外部云模型和外部智能体服务。'
    case 'restricted': return '绝密：不会发给任何模型；即使划分给部门，成员也不会自动获得访问权限。'
    default: return '内部：默认级别，可以发给已配置的模型。'
  }
})

const openCreate = (teamId: number | null = selectedTeamId.value) => {
  const fromScope: SpaceAssignScope = teamId ? 'departments' : scope.value === 'enterprise' ? 'enterprise' : 'unassigned'
  dialog.value = {
    visible: true, target: null, name: '', description: '', scope: fromScope, teamIds: teamId ? [teamId] : [],
    sensitivity: 'internal', saving: false, error: '',
  }
}

const openAssign = (s: AdminKnowledgeSpace) => {
  dialog.value = {
    visible: true, target: s, name: s.name, description: '',
    scope: s.scope_type === 'department' ? 'departments' : s.scope_type === 'enterprise' ? 'enterprise' : 'unassigned',
    teamIds: s.departments.map((d) => d.id), sensitivity: s.sensitivity || 'internal', saving: false, error: '',
  }
}

const submitDialog = async () => {
  const d = dialog.value
  d.saving = true
  d.error = ''
  const teamIds = d.scope === 'departments' ? d.teamIds : []
  try {
    if (d.target) await updateAdminSpace(d.target.id, { scope: d.scope, team_ids: teamIds, sensitivity: d.sensitivity })
    else await createAdminSpace({ name: d.name.trim(), description: d.description.trim() || undefined, scope: d.scope, team_ids: teamIds, sensitivity: d.sensitivity })
    d.visible = false
    await load()
  } catch (e: any) {
    d.error = getErrorMessage(e, '保存失败')
  } finally {
    d.saving = false
  }
}

// ---------- 启停 / 归档 ----------
const toggleEnabled = async (s: AdminKnowledgeSpace) => {
  const next = !s.is_enabled
  if (!confirm(`确认${next ? '启用' : '停用'}空间「${s.name}」？${next ? '' : '停用后该空间的知识检索将不再生效。'}`)) return
  actingId.value = s.id
  actionErr.value = ''
  try {
    await updateAdminSpaceStatus(s.id, { is_enabled: next })
    s.is_enabled = next
  } catch (e: any) {
    actionErr.value = getErrorMessage(e, '操作失败')
  } finally {
    actingId.value = null
  }
}

const toggleArchived = async (s: AdminKnowledgeSpace) => {
  const nextStatus = s.status === 'active' ? 'archived' : 'active'
  if (!confirm(`确认${nextStatus === 'archived' ? '归档' : '恢复'}空间「${s.name}」？`)) return
  actingId.value = s.id
  actionErr.value = ''
  try {
    await updateAdminSpaceStatus(s.id, { status: nextStatus })
    s.status = nextStatus
  } catch (e: any) {
    actionErr.value = getErrorMessage(e, '操作失败')
  } finally {
    actingId.value = null
  }
}

onMounted(load)
</script>
