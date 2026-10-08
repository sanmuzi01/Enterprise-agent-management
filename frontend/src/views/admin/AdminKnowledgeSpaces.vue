<template>
  <div class="p-6">
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <p class="text-xs text-slate-500">按部门查看和管理知识库空间：部门成员自动可读，绝密空间需要单独加人。</p>
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
      <!-- 部门清单 -->
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
                <th class="px-3 py-2 text-left">所属部门</th>
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
                  <span v-if="s.scope_type === 'department' && s.team_name" class="rounded bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">{{ s.team_name }}</span>
                  <span v-else class="text-xs text-slate-400">个人空间</span>
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
                    <button @click="openMove(s)" data-testid="space-move"
                      class="rounded border border-indigo-200 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50">调整归属</button>
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

    <!-- 新建 / 调整归属 -->
    <div v-if="dialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="dialog.visible = false">
      <div class="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl bg-white p-5 shadow-xl" data-testid="space-dialog">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ dialog.target ? `调整归属：${dialog.target.name}` : '新建知识库' }}</h3>
        <div class="space-y-3">
          <div v-if="!dialog.target">
            <label for="space-name" class="mb-1 block text-xs text-slate-500">名称</label>
            <input id="space-name" v-model="dialog.name" maxlength="120" placeholder="例如：销售部报价资料"
              class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label for="space-team" class="mb-1 block text-xs text-slate-500">所属部门</label>
            <select id="space-team" v-model="dialog.teamId" data-testid="space-team-select"
              class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
              <option :value="null">个人空间（只有所有者和被加入的人能看到）</option>
              <option v-for="d in departments" :key="d.id" :value="d.id">
                {{ multipleOrganizations ? `${d.organization_name} · ${d.name}` : d.name }}（部门成员自动可读）
              </option>
            </select>
          </div>
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
          <button @click="submitDialog" :disabled="dialog.saving || (!dialog.target && !dialog.name.trim())" data-testid="space-dialog-save"
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
  type AdminKnowledgeSpace, type AdminSpaceDepartment,
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

// 筛选：all = 全部；personal = 个人空间；team:<编号> = 某个部门
const scope = ref('all')
const departments = ref<AdminSpaceDepartment[]>([])
const personalCount = ref(0)
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
  entries.push({ key: 'personal', label: '个人空间', count: personalCount.value, group: multipleOrganizations.value ? '其他' : undefined })
  return entries
})
const selectedTeamId = computed(() => (scope.value.startsWith('team:') ? Number(scope.value.slice(5)) : null))
const emptyText = computed(() => {
  if (selectedTeamId.value) return '这个部门还没有知识库空间。'
  return scope.value === 'personal' ? '还没有个人空间。' : '暂无知识库空间'
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
    personalCount.value = page.personal_count
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

// ---------- 新建 / 调整归属 ----------
const dialog = ref({
  visible: false,
  target: null as AdminKnowledgeSpace | null,
  name: '',
  description: '',
  teamId: null as number | null,
  sensitivity: 'internal',
  saving: false,
  error: '',
})

const sensitivityHint = computed(() => {
  switch (dialog.value.sensitivity) {
    case 'public': return '公开：可以发给任何模型。'
    case 'confidential': return '机密：只会发给企业批准的模型，不会发给外部云模型和外部智能体服务。'
    case 'restricted': return '绝密：不会发给任何模型；发布在部门里时，部门成员也不会自动获得访问权限。'
    default: return '内部：默认级别，可以发给已配置的模型。'
  }
})

const openCreate = (teamId: number | null = selectedTeamId.value) => {
  dialog.value = { visible: true, target: null, name: '', description: '', teamId, sensitivity: 'internal', saving: false, error: '' }
}

const openMove = (s: AdminKnowledgeSpace) => {
  dialog.value = {
    visible: true, target: s, name: s.name, description: '',
    teamId: s.scope_type === 'department' ? s.team_id : null, sensitivity: s.sensitivity || 'internal', saving: false, error: '',
  }
}

const submitDialog = async () => {
  const d = dialog.value
  d.saving = true
  d.error = ''
  try {
    if (d.target) await updateAdminSpace(d.target.id, { team_id: d.teamId, sensitivity: d.sensitivity })
    else await createAdminSpace({ name: d.name.trim(), description: d.description.trim() || undefined, team_id: d.teamId, sensitivity: d.sensitivity })
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
