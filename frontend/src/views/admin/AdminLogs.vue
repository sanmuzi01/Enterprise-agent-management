<template>
  <div class="p-6">
    <div class="mb-5 flex items-center justify-between">
      <p class="text-xs text-slate-500">查看接口访问、错误响应、慢请求和管理员操作轨迹</p>
      <div class="flex items-center gap-2">
        <button
          @click="loadLogs"
          :disabled="loading"
          class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
        <button
          @click="exportCsv"
          :disabled="exporting"
          class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
          title="按当前筛选条件导出，最多导出最近 500 条"
        >
          <Download :size="14" />
          {{ exporting ? '导出中...' : '导出 CSV' }}
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <section class="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <div class="flex flex-wrap items-center gap-3">
        <div class="relative min-w-[220px] flex-1 sm:max-w-xs">
          <Search :size="15" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索路径、用户、IP 或错误"
            class="h-8 w-full rounded border border-slate-200 bg-white pl-8 pr-2 text-xs outline-none focus:border-blue-500"
            @keyup.enter="loadLogs"
          />
        </div>
        <select v-model.number="days" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs outline-none focus:border-blue-500">
          <option :value="1">今天</option>
          <option :value="7">近 7 天</option>
          <option :value="14">近 14 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
        <select v-model.number="userId" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs outline-none focus:border-blue-500">
          <option :value="0">全部用户</option>
          <option v-for="user in users" :key="user.id" :value="user.id">{{ user.name }} #{{ user.id }}</option>
        </select>
        <select v-model="method" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs outline-none focus:border-blue-500">
          <option value="">全部方法</option>
          <option v-for="item in methods" :key="item" :value="item">{{ item }}</option>
        </select>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="item in statusFilters"
            :key="item.value"
            @click="statusGroup = item.value"
            :class="statusGroup === item.value ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'"
            class="rounded px-2.5 py-1 text-xs transition-colors"
          >
            {{ item.label }}
          </button>
        </div>
        <span class="ml-auto text-xs text-slate-400">共 {{ total }} 条</span>
      </div>
    </section>

    <section class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <div class="grid min-w-[820px] grid-cols-[150px_90px_1fr_92px_90px_120px] gap-3 border-b border-slate-100 bg-slate-50 px-4 py-3 text-xs font-medium text-slate-500">
        <span>时间</span>
        <span>用户</span>
        <span>请求</span>
        <span>状态</span>
        <span>耗时</span>
        <span>来源</span>
      </div>

      <div class="min-w-[820px]">
        <section v-for="group in groupedLogs" :key="group.key" class="border-b border-slate-100 last:border-b-0">
          <div class="flex items-center justify-between bg-slate-50/70 px-4 py-2">
            <div class="min-w-0">
              <p class="truncate text-sm font-medium text-slate-800">{{ group.label }}</p>
              <p class="text-xs text-slate-400">{{ group.count }} 条日志</p>
            </div>
            <span v-if="group.errorCount" class="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{{ group.errorCount }} 条错误</span>
          </div>

          <article v-for="log in group.items" :key="log.id" class="grid grid-cols-[150px_90px_1fr_92px_90px_120px] gap-3 px-4 py-3 text-sm hover:bg-slate-50/60">
            <span class="text-xs text-slate-500">{{ log.created_at || '-' }}</span>
            <div class="min-w-0">
              <p class="truncate text-slate-700">{{ log.username || '-' }}</p>
              <p v-if="log.user_id" class="text-xs text-slate-400">#{{ log.user_id }}</p>
            </div>
            <div class="min-w-0">
              <div class="flex min-w-0 items-center gap-2">
                <span :class="methodClass(log.method)" class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium">{{ log.method }}</span>
                <p class="truncate font-medium text-slate-900">{{ log.path }}</p>
              </div>
              <p v-if="log.error_msg" class="mt-1 line-clamp-2 rounded border border-red-100 bg-red-50 px-2 py-1 text-xs text-red-700">
                {{ humanizeLogMessage(log.error_msg) }}
              </p>
            </div>
            <div class="flex items-start">
              <span :class="statusClass(log.status_code)" class="rounded px-2 py-1 text-xs">{{ log.status_code }}</span>
            </div>
            <span :class="log.latency_ms >= 1000 ? 'text-amber-700' : 'text-slate-500'" class="text-xs">{{ log.latency_ms }} ms</span>
            <div class="min-w-0 text-xs text-slate-500">
              <p class="truncate">{{ log.client_ip || '-' }}</p>
              <p class="truncate text-slate-400" :title="log.user_agent || ''">{{ log.user_agent || '-' }}</p>
            </div>
          </article>
        </section>

        <div v-if="!logs.length && !loading" class="py-14 text-center text-sm text-slate-500">暂无操作日志。</div>
      </div>
      <AdminPagination :total="total" :limit="limit" :offset="offset" @update:offset="onPageChange" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { humanizeLogMessage } from '../../utils/displayNames'
import { computed, onMounted, ref, watch } from 'vue'
import { Download, RefreshCcw, Search } from 'lucide-vue-next'
import AdminPagination from '../../components/admin/AdminPagination.vue'
import * as adminApi from '../../api/admin'
import type { AdminLog, AdminUser } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'
import { downloadFile } from '../../utils/download'

const logs = ref<AdminLog[]>([])
const users = ref<AdminUser[]>([])
const total = ref(0)
const limit = ref(100)
const offset = ref(0)
const loading = ref(false)
const errorMsg = ref('')
const keyword = ref('')
const days = ref(7)
const userId = ref(0)
const method = ref('')
const statusGroup = ref('')
const exporting = ref(false)

const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const statusFilters = [
  { label: '全部', value: '' },
  { label: '成功', value: 'success' },
  { label: '错误', value: 'error' },
  { label: '慢请求', value: 'slow' },
]

let searchTimer: number | undefined

const groupedLogs = computed(() => {
  const groups = new Map<string, { key: string; label: string; items: AdminLog[]; count: number; errorCount: number }>()
  for (const log of logs.value) {
    const key = log.user_id ? `user-${log.user_id}` : 'anonymous'
    const label = log.user_id ? `${log.username || '未知用户'} #${log.user_id}` : '未登录/系统请求'
    if (!groups.has(key)) {
      groups.set(key, { key, label, items: [], count: 0, errorCount: 0 })
    }
    const group = groups.get(key)!
    group.items.push(log)
    group.count += 1
    if (log.status_code >= 400) group.errorCount += 1
  }
  return Array.from(groups.values())
})

const loadLogs = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const page = await adminApi.listAdminLogs({
      limit: limit.value,
      offset: offset.value,
      days: days.value,
      keyword: keyword.value.trim() || undefined,
      user_id: userId.value || undefined,
      method: method.value || undefined,
      status_group: statusGroup.value || undefined,
    })
    logs.value = page.items
    total.value = page.total
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '读取操作日志失败')
  } finally {
    loading.value = false
  }
}

const onPageChange = (nextOffset: number) => {
  offset.value = nextOffset
  loadLogs()
}

const loadUsers = async () => {
  try {
    // 只用来填"按用户筛选"下拉框；200 对目前的用户规模够用，用户量再上一个数量级
    // 需要换成专门的"用户名称查找"接口，不该复用分页列表接口硬拉大 limit。
    users.value = (await adminApi.listAdminUsers({ limit: 200 })).items
  } catch {
    users.value = []
  }
}

watch([days, userId, method, statusGroup], () => {
  offset.value = 0
  loadLogs()
})
watch(keyword, () => {
  if (searchTimer !== undefined) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    offset.value = 0
    loadLogs()
  }, 300)
})

const methodClass = (value: string) => ({
  GET: 'bg-blue-50 text-blue-700',
  POST: 'bg-emerald-50 text-emerald-700',
  PUT: 'bg-amber-50 text-amber-700',
  PATCH: 'bg-purple-50 text-purple-700',
  DELETE: 'bg-red-50 text-red-700',
}[value] || 'bg-slate-100 text-slate-600')

const statusClass = (value: number) => {
  if (value >= 500) return 'bg-red-50 text-red-700'
  if (value >= 400) return 'bg-amber-50 text-amber-700'
  if (value >= 300) return 'bg-slate-100 text-slate-600'
  return 'bg-emerald-50 text-emerald-700'
}

const exportCsv = async () => {
  exporting.value = true
  try {
    await downloadFile('/admin/logs/export', {
      days: days.value,
      keyword: keyword.value.trim() || undefined,
      method: method.value || undefined,
      status_group: statusGroup.value || undefined,
      user_id: userId.value || undefined,
    }, 'operation_logs.csv')
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  loadUsers()
  loadLogs()
})
</script>
