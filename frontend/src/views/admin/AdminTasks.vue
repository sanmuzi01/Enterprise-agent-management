<template>
  <div class="p-6">
    <div class="mb-5 flex items-center justify-between">
      <p class="text-xs text-slate-500">查看、筛选、取消和重试全局后台任务</p>
      <div class="flex items-center gap-2">
        <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-500">
          <input v-model="autoRefresh" type="checkbox" class="rounded" />
          自动刷新
        </label>
        <button
          @click="reload()"
          :disabled="loading"
          class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <section class="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <div class="flex flex-wrap items-center gap-3">
        <div class="relative min-w-[220px] flex-1 sm:max-w-xs">
          <Search :size="15" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            v-model="taskQuery"
            type="search"
            placeholder="在当前页内搜索任务、用户或助手"
            class="h-8 w-full rounded border border-slate-200 bg-white pl-8 pr-2 text-xs outline-none focus:border-blue-500"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs text-slate-500">状态</span>
          <div class="flex flex-wrap gap-1">
            <button
              v-for="opt in statusOptions"
              :key="opt.value"
              @click="filterStatus = opt.value"
              :class="filterStatus === opt.value ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'"
              class="rounded px-2.5 py-1 text-xs transition-colors"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs text-slate-500">类型</span>
          <select v-model="filterType" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs outline-none focus:border-blue-500">
            <option value="">全部</option>
            <option v-for="type in typeOptions" :key="type" :value="type">{{ taskTypeLabel(type) }}</option>
          </select>
        </div>
        <span class="ml-auto text-xs text-slate-400">{{ filteredTasks.length }} / 共 {{ total }} 条</span>
      </div>
    </section>

    <section class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <div class="grid min-w-[720px] grid-cols-[1fr_120px_110px_120px] gap-3 border-b border-slate-100 bg-slate-50 px-4 py-3 text-xs font-medium text-slate-500">
        <span>任务</span>
        <span>进度</span>
        <span>状态</span>
        <span>操作</span>
      </div>

      <div class="min-w-[720px] divide-y divide-slate-100">
        <article v-for="task in filteredTasks" :key="task.id" class="grid grid-cols-[1fr_120px_110px_120px] gap-3 px-4 py-3 text-sm hover:bg-slate-50/60">
          <div class="min-w-0">
            <div class="flex min-w-0 items-center gap-2">
              <p class="truncate font-medium text-slate-900">{{ task.title }}</p>
              <span class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">#{{ task.id }}</span>
              <span v-if="task.retry_count > 0" class="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">重试 {{ task.retry_count }}</span>
            </div>
            <p class="mt-1 flex flex-wrap gap-2 text-xs text-slate-400">
              <span>{{ taskTypeLabel(task.task_type) }}</span>
              <span>用户 #{{ task.user_id }}</span>
              <span v-if="task.agent_id">助手 #{{ task.agent_id }}</span>
              <span v-if="task.target_type">{{ targetTypeLabel(task.target_type) }} #{{ task.target_id }}</span>
              <span>{{ task.created_at || '-' }}</span>
              <span v-if="task.next_run_at">下次重试 {{ task.next_run_at }}</span>
            </p>
            <p
              v-if="task.error_msg"
              class="mt-2 line-clamp-2 rounded border px-2 py-1 text-xs"
              :class="task.status === 'queued' && task.next_run_at
                ? 'border-amber-100 bg-amber-50 text-amber-700'
                : 'border-red-100 bg-red-50 text-red-700'"
            >
              {{ task.error_msg }}
            </p>
            <details v-if="task.result" class="mt-2">
              <summary class="cursor-pointer text-xs text-slate-500 hover:text-slate-700">查看结果</summary>
              <pre class="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs text-slate-600">{{ JSON.stringify(task.result, null, 2) }}</pre>
            </details>
          </div>

          <div class="flex items-center gap-2">
            <div class="h-1.5 flex-1 rounded bg-slate-200">
              <div class="h-1.5 rounded bg-blue-500 transition-all" :style="{ width: `${task.progress || 0}%` }"></div>
            </div>
            <span class="w-9 text-right text-xs text-slate-500">{{ task.progress || 0 }}%</span>
          </div>

          <div class="flex items-center">
            <span :class="statusClass(task.status)" class="rounded px-2 py-1 text-xs">{{ statusText(task.status) }}</span>
          </div>

          <div class="flex items-center gap-1.5">
            <button
              v-if="canRetry(task)"
              @click="handleRetry(task)"
              :disabled="actingId === task.id"
              class="inline-flex h-8 w-8 items-center justify-center rounded border border-amber-200 text-amber-700 hover:bg-amber-50 disabled:opacity-50"
              title="重试"
            >
              <RotateCcw :size="14" />
            </button>
            <button
              v-if="canCancel(task)"
              @click="handleCancel(task)"
              :disabled="actingId === task.id"
              class="inline-flex h-8 w-8 items-center justify-center rounded border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              title="取消"
            >
              <X :size="14" />
            </button>
          </div>
        </article>

        <div v-if="!filteredTasks.length && !loading" class="py-14 text-center text-sm text-slate-500">
          {{ tasks.length ? '当前页没有匹配搜索词的任务。' : '暂无后台任务。' }}
        </div>
      </div>
      <AdminPagination :total="total" :limit="limit" :offset="offset" @update:offset="onPageChange" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { taskTypeLabel, targetTypeLabel } from '../../utils/displayNames'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RefreshCcw, RotateCcw, Search, X } from 'lucide-vue-next'
import AdminPagination from '../../components/admin/AdminPagination.vue'
import * as taskApi from '../../api/task'
import type { Task, TaskStatus } from '../../api/task'
import { getErrorMessage } from '../../utils/request'

const tasks = ref<Task[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const loading = ref(false)
const actingId = ref<number | null>(null)
const errorMsg = ref('')
const taskQuery = ref('')
const filterStatus = ref<TaskStatus | ''>('')
const filterType = ref('')
const autoRefresh = ref(true)
let timer: number | undefined

const statusOptions = [
  { value: '' as const, label: '全部' },
  { value: 'queued' as const, label: '排队' },
  { value: 'running' as const, label: '执行中' },
  { value: 'finished' as const, label: '成功' },
  { value: 'failed' as const, label: '失败' },
  { value: 'cancelled' as const, label: '已取消' },
]

const typeOptions = computed(() => [...new Set(tasks.value.map((task) => task.task_type))].sort())
const hasWorking = computed(() => tasks.value.some((task) => task.status === 'queued' || task.status === 'running'))
const filteredTasks = computed(() => {
  const query = taskQuery.value.trim().toLowerCase()
  if (!query) return tasks.value
  return tasks.value.filter((task) => {
    const fields = [
      task.title,
      task.task_type,
      task.status,
      task.error_msg || '',
      task.next_run_at || '',
      String(task.id),
      String(task.user_id),
      task.agent_id ? String(task.agent_id) : '',
      task.target_type || '',
      task.target_id ? String(task.target_id) : '',
    ]
    return fields.some((field) => field.toLowerCase().includes(query))
  })
})

const reload = async (silent = false) => {
  loading.value = !silent
  errorMsg.value = ''
  try {
    const page = await taskApi.listAllTasks({
      limit: limit.value,
      offset: offset.value,
      status: filterStatus.value || undefined,
      task_type: filterType.value || undefined,
    })
    tasks.value = page.items
    total.value = page.total
  } catch (e: any) {
    if (!silent) errorMsg.value = getErrorMessage(e, '加载后台任务失败')
  } finally {
    loading.value = false
  }
}

const onPageChange = (nextOffset: number) => {
  offset.value = nextOffset
  reload()
}

watch([filterStatus, filterType], () => {
  offset.value = 0
  reload()
})
watch([hasWorking, autoRefresh], ([working, enabled]) => {
  if (working && enabled && timer === undefined) {
    timer = window.setInterval(() => reload(true), 5000)
  }
  if ((!working || !enabled) && timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}, { immediate: true })

const canRetry = (task: Task) => task.status === 'failed' || task.status === 'cancelled'
const canCancel = (task: Task) => task.status === 'queued'

const handleRetry = async (task: Task) => {
  if (!confirm(`确认重试任务「${task.title}」？`)) return
  actingId.value = task.id
  try {
    await taskApi.retryTask(task.id)
    await reload(true)
  } catch (e: any) {
    alert(getErrorMessage(e, '重试失败'))
  } finally {
    actingId.value = null
  }
}

const handleCancel = async (task: Task) => {
  if (!confirm(`确认取消任务「${task.title}」？`)) return
  actingId.value = task.id
  try {
    await taskApi.cancelTask(task.id)
    await reload(true)
  } catch (e: any) {
    alert(getErrorMessage(e, '取消失败'))
  } finally {
    actingId.value = null
  }
}

const statusText = (s: string) => ({
  queued: '等待处理',
  running: '处理中',
  finished: '已完成',
  failed: '失败',
  cancelled: '已取消',
}[s] || s)

const statusClass = (s: string) => ({
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-50 text-blue-700',
  finished: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-amber-50 text-amber-700',
}[s] || 'bg-slate-100 text-slate-600')

onMounted(() => reload())
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>
