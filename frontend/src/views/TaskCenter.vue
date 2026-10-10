<template>
  <div class="h-screen flex flex-col bg-transparent">
    <header class="min-h-16 border-b border-sky-200/70 ui-glass px-5 py-3 backdrop-blur-xl flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex items-center gap-3">
        <button @click="$router.push('/agents')" class="inline-flex h-8 items-center rounded border border-sky-200 bg-white/80 px-3 text-sm text-slate-600 hover:bg-sky-50">
          返回工作台
        </button>
        <div>
          <h1 class="text-base font-semibold text-slate-900">处理进度</h1>
          <p class="text-xs text-slate-500">查看资料入库、重建索引等后台任务。</p>
        </div>
        <span v-if="isAdmin && showAll" class="text-xs px-2 py-0.5 bg-red-50 text-red-600 rounded">
          管理员·全局
        </span>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="isAdmin"
          @click="toggleScope"
          class="px-3 py-1.5 text-xs border rounded transition-colors"
          :class="showAll
            ? 'border-red-200 text-red-600 bg-red-50'
            : 'border-slate-200 text-slate-600 hover:bg-slate-50'"
        >
          {{ showAll ? '全局任务' : '我的任务' }}
        </button>
        <label class="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer">
          <input type="checkbox" v-model="autoRefresh" class="rounded" />
          自动刷新
        </label>
        <button
          @click="reload()"
          class="px-3 py-1.5 text-sm text-blue-600 border border-blue-200 rounded hover:bg-blue-50 transition-colors flex items-center gap-1"
        >
          <RefreshCw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </header>

    <!-- 筛选栏 -->
    <div class="border-b border-sky-100 bg-white/58 px-5 py-3 backdrop-blur flex flex-wrap items-center gap-4">
      <div class="flex items-center gap-2">
        <span class="text-xs text-slate-500">状态</span>
        <div class="flex gap-1">
          <button
            v-for="opt in statusOptions"
            :key="opt.value"
            @click="filterStatus = opt.value"
            :class="[
              'px-2.5 py-1 text-xs rounded-full transition-colors',
              filterStatus === opt.value
                ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-200'
                : 'bg-white/80 text-slate-600 hover:bg-sky-50'
            ]"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs text-slate-500">类型</span>
        <select
          v-model="filterType"
          class="text-xs border border-slate-200 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="">全部</option>
          <option v-for="t in typeOptions" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>
      <div class="ml-auto text-xs text-slate-400">
        共 {{ tasks.length }} 条
      </div>
    </div>

    <!-- 任务列表 -->
    <main class="flex-1 overflow-y-auto p-6">
      <div class="max-w-5xl mx-auto">
        <div v-if="!loading && tasks.length === 0" class="rounded-lg border border-dashed border-sky-300/70 bg-white/62 py-16 text-center text-sm text-slate-500 backdrop-blur">
          暂无处理任务。上传资料、抓取网页或重建索引后会显示在这里。
        </div>

        <div v-else class="space-y-3">
          <div
            v-for="task in tasks"
            :key="task.id"
            class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] ui-card rounded-lg p-4 transition"
            :class="statusBorder(task.status)"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <!-- 标题行 -->
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-medium text-slate-800 truncate">{{ task.title }}</span>
                  <span
                    class="text-xs px-2 py-0.5 rounded-full font-medium flex items-center gap-1"
                    :class="statusBadge(task.status)"
                  >
                    <component :is="statusIcon(task.status)" :size="11" />
                    {{ statusLabel(task.status) }}
                  </span>
                  <span class="text-xs text-slate-400 font-mono">#{{ task.id }}</span>
                  <span v-if="task.retry_count > 0" class="text-xs px-1.5 py-0.5 bg-amber-50 text-amber-600 rounded">
                    重试 {{ task.retry_count }} 次
                  </span>
                </div>
                <!-- 元数据行 -->
                <div class="text-xs text-slate-500 flex items-center gap-3 flex-wrap">
                  <span>{{ taskTypeLabel(task.task_type) }}</span>
                  <span v-if="showAll">用户 #{{ task.user_id }}</span>
                  <span v-if="task.agent_id">助手 #{{ task.agent_id }}</span>
                  <span v-if="task.target_type">对象 #{{ task.target_id }}</span>
                  <span>创建: {{ task.created_at }}</span>
                  <span v-if="task.next_run_at">下次重试: {{ task.next_run_at }}</span>
                  <span v-if="task.finished_at">完成: {{ task.finished_at }}</span>
                </div>
                <!-- 进度条（running 时显示） -->
                <div v-if="task.status === 'running' || task.progress > 0" class="mt-2">
                  <div class="flex items-center gap-2">
                    <div class="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        class="h-full bg-blue-500 rounded-full transition-all duration-300"
                        :style="{ width: task.progress + '%' }"
                      ></div>
                    </div>
                    <span class="text-xs text-slate-500 w-9 text-right">{{ task.progress }}%</span>
                  </div>
                </div>
                <!-- 错误信息（failed 时显示） -->
                <div v-if="task.error_msg" class="mt-2">
                  <div
                    class="text-xs rounded p-2 font-mono whitespace-pre-wrap break-all"
                    :class="task.status === 'queued' && task.next_run_at
                      ? 'bg-amber-50 border border-amber-100 text-amber-800'
                      : 'bg-red-50 border border-red-100 text-red-700'"
                  >
                    {{ task.error_msg }}
                  </div>
                </div>
                <!-- 结果（finished 时显示，可折叠） -->
                <details v-if="task.status === 'finished' && task.result" class="mt-2">
                  <summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-700">查看结果</summary>
                  <pre class="text-xs bg-slate-50 rounded p-2 mt-1 overflow-x-auto text-slate-700">{{ JSON.stringify(task.result, null, 2) }}</pre>
                </details>
              </div>
              <!-- 操作按钮 -->
              <div class="flex flex-col gap-1 shrink-0">
                <button
                  v-if="canRetry(task)"
                  @click="handleRetry(task)"
                  :disabled="actingId === task.id"
                  class="px-2.5 py-1 text-xs border border-amber-200 text-amber-700 rounded hover:bg-amber-50 transition-colors flex items-center gap-1 disabled:opacity-50"
                >
                  <RotateCcw :size="12" />
                  重试
                </button>
                <button
                  v-if="canCancel(task)"
                  @click="handleCancel(task)"
                  :disabled="actingId === task.id"
                  class="px-2.5 py-1 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-50 transition-colors flex items-center gap-1 disabled:opacity-50"
                >
                  <X :size="12" />
                  取消
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { taskTypeLabel } from '../utils/displayNames'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { RefreshCw, RotateCcw, X, Clock, Loader, CheckCircle2, AlertCircle, Ban } from 'lucide-vue-next'
import { useUserStore } from '../stores/user'
import * as taskApi from '../api/task'
import type { Task, TaskStatus } from '../api/task'

const userStore = useUserStore()
const isAdmin = computed(() => !!userStore.user?.is_admin)

// ===== 数据 =====
const tasks = ref<Task[]>([])
const loading = ref(false)
const actingId = ref<number | null>(null)

// ===== 筛选 =====
const filterStatus = ref<TaskStatus | ''>('')
const filterType = ref('')
const statusOptions = [
  { value: '' as const, label: '全部' },
  { value: 'queued' as const, label: '排队' },
  { value: 'running' as const, label: '执行中' },
  { value: 'finished' as const, label: '成功' },
  { value: 'failed' as const, label: '失败' },
  { value: 'cancelled' as const, label: '已取消' },
]
const typeOptions = computed(() => {
  const set = new Set(tasks.value.map(t => t.task_type))
  return [...set].sort()
})

// ===== 管理员模式 =====
const showAll = ref(false)
const toggleScope = () => {
  showAll.value = !showAll.value
  reload()
}

// ===== 自动刷新 =====
const autoRefresh = ref(true)
let timer: number | null = null
const hasRunning = computed(() => tasks.value.some(t => t.status === 'queued' || t.status === 'running'))

watch([hasRunning, autoRefresh], ([running, ar]) => {
  // 有排队/执行中 且 开启自动刷新 → 5 秒轮询
  if (running && ar && !timer) {
    timer = window.setInterval(() => reload(true), 5000)
  } else if ((!running || !ar) && timer) {
    clearInterval(timer)
    timer = null
  }
}, { immediate: true })

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

// ===== 加载 =====
const reload = async (silent = false) => {
  loading.value = !silent
  try {
    const params = {
      status: filterStatus.value || undefined,
      task_type: filterType.value || undefined,
      limit: 100,
    }
    if (showAll.value && isAdmin.value) {
      tasks.value = (await taskApi.listAllTasks(params)).items
    } else {
      tasks.value = await taskApi.listTasks(params)
    }
  } catch (e) {
    if (!silent) console.error('加载任务失败:', e)
  } finally {
    loading.value = false
  }
}

watch([filterStatus, filterType], () => reload())

// ===== 操作 =====
const canRetry = (t: Task) => t.status === 'failed' || t.status === 'cancelled'
const canCancel = (t: Task) => t.status === 'queued'

const handleRetry = async (t: Task) => {
  if (!confirm(`确认重试任务「${t.title}」？将创建新任务并重新执行。`)) return
  actingId.value = t.id
  try {
    await taskApi.retryTask(t.id)
    await reload(true)
  } catch (e: any) {
    alert(e?.response?.data?.detail || '重试失败')
  } finally {
    actingId.value = null
  }
}

const handleCancel = async (t: Task) => {
  if (!confirm(`确认取消任务「${t.title}」？`)) return
  actingId.value = t.id
  try {
    await taskApi.cancelTask(t.id)
    await reload(true)
  } catch (e: any) {
    alert(e?.response?.data?.detail || '取消失败')
  } finally {
    actingId.value = null
  }
}

// ===== 状态辅助 =====
const statusLabel = (s: TaskStatus) => ({
  queued: '等待处理', running: '处理中', finished: '已完成', failed: '失败', cancelled: '已取消',
}[s] || s)

const statusIcon = (s: TaskStatus) => ({
  queued: Clock, running: Loader, finished: CheckCircle2, failed: AlertCircle, cancelled: Ban,
}[s] || Clock)

const statusBadge = (s: TaskStatus) => ({
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-50 text-blue-600',
  finished: 'bg-emerald-50 text-emerald-600',
  failed: 'bg-red-50 text-red-600',
  cancelled: 'bg-amber-50 text-amber-600',
}[s] || 'bg-slate-100 text-slate-600')

const statusBorder = (s: TaskStatus) => ({
  queued: 'border-slate-200',
  running: 'border-blue-200',
  finished: 'border-emerald-200',
  failed: 'border-red-200',
  cancelled: 'border-amber-200',
}[s] || 'border-slate-200')

onMounted(() => reload())
</script>
