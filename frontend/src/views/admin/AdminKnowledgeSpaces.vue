<template>
  <div class="space-y-4">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold text-slate-900">企业知识库</h1>
        <p class="mt-1 text-sm text-slate-500">平台内所有知识库空间的归属、规模、成员和健康分。</p>
      </div>
      <button
        @click="load"
        :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </header>

    <p v-if="actionErr" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ actionErr }}</p>

    <div v-if="loading" class="py-12 text-center text-sm text-slate-400">加载中…</div>
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
            <th class="px-3 py-2 text-right">片段</th>
            <th class="px-3 py-2 text-right">成员</th>
            <th class="px-3 py-2 text-right">绑定 Agent</th>
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
            <td class="px-3 py-2 text-right">{{ s.chunk_count }}</td>
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
          <tr v-if="items.length === 0"><td colspan="11" class="px-3 py-8 text-center text-slate-400">暂无知识库空间</td></tr>
        </tbody>
      </table>
      <AdminPagination :total="total" :limit="limit" :offset="offset" @update:offset="onPageChange" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import AdminPagination from '../../components/admin/AdminPagination.vue'
import { listAdminKnowledgeSpaces, updateAdminSpaceStatus, type AdminKnowledgeSpace } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const loading = ref(true)
const err = ref('')
const actionErr = ref('')
const actingId = ref<number | null>(null)
const items = ref<AdminKnowledgeSpace[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)

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
    const page = await listAdminKnowledgeSpaces({ limit: limit.value, offset: offset.value })
    items.value = page.items
    total.value = page.total
  } catch (e: any) {
    err.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

const onPageChange = (nextOffset: number) => {
  offset.value = nextOffset
  load()
}

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
