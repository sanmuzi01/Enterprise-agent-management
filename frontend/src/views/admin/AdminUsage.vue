<template>
  <div class="p-6">
    <div class="mb-5 flex items-center justify-between">
      <div>
        <p class="text-xs text-slate-500">运行质量、调用趋势、模型消耗和活跃用户排行</p>
      </div>
      <div class="flex items-center gap-2">
        <select v-model.number="days" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs outline-none focus:border-blue-500">
          <option :value="7">近 7 天</option>
          <option :value="14">近 14 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
        <button
          @click="loadUsage"
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
        >
          <Download :size="14" />
          {{ exporting ? '导出中...' : '导出 CSV' }}
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <section class="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
      <article v-for="item in summaryItems" :key="item.label" class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="text-xs text-slate-500">{{ item.label }}</p>
            <p class="mt-2 text-2xl font-semibold text-slate-900">{{ item.value }}</p>
          </div>
          <span :class="item.tone" class="flex h-9 w-9 items-center justify-center rounded">
            <component :is="item.icon" :size="17" />
          </span>
        </div>
        <p class="mt-3 text-xs text-slate-400">{{ item.hint }}</p>
      </article>
    </section>

    <div class="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(620px,1fr)_420px]">
      <section class="rounded-lg border border-slate-200 bg-white">
        <div class="flex h-12 items-center justify-between border-b border-slate-200 px-4">
          <div>
            <h2 class="text-sm font-semibold text-slate-900">调用趋势</h2>
            <p class="text-xs text-slate-400">运行次数和消息量对比</p>
          </div>
          <div class="flex items-center gap-3 text-xs text-slate-500">
            <span class="inline-flex items-center gap-1"><i class="h-2 w-2 rounded-full bg-blue-500"></i>运行</span>
            <span class="inline-flex items-center gap-1"><i class="h-2 w-2 rounded-full bg-emerald-500"></i>消息</span>
          </div>
        </div>
        <div class="p-4">
          <svg viewBox="0 0 720 260" class="h-72 w-full overflow-visible">
            <line v-for="line in gridLines" :key="line" x1="44" x2="700" :y1="line" :y2="line" style="stroke: var(--line)" stroke-width="1" />
            <polyline :points="runLinePoints" fill="none" stroke="#0071e3" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
            <polyline :points="messageLinePoints" fill="none" stroke="#34c759" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
            <g v-for="point in chartPoints" :key="point.date">
              <circle :cx="point.x" :cy="point.runY" r="4" fill="#0071e3" />
              <circle :cx="point.x" :cy="point.messageY" r="4" fill="#34c759" />
              <text :x="point.x" y="252" text-anchor="middle" class="fill-slate-400 text-[10px]">{{ shortDate(point.date) }}</text>
            </g>
            <text x="44" y="18" class="fill-slate-400 text-[10px]">{{ maxChartValue }}</text>
            <text x="44" y="230" class="fill-slate-400 text-[10px]">0</text>
          </svg>
          <div v-if="!usage?.daily.length && !loading" class="py-10 text-center text-sm text-slate-500">暂无使用数据。</div>
        </div>
      </section>

      <section class="space-y-5">
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="mb-4 flex items-center justify-between">
            <div>
              <h2 class="text-sm font-semibold text-slate-900">任务状态分布</h2>
              <p class="text-xs text-slate-400">后台任务健康度</p>
            </div>
            <PieChart :size="17" class="text-slate-400" />
          </div>
          <div class="grid grid-cols-[150px_1fr] items-center gap-5">
            <div class="relative h-36 w-36 rounded-full" :style="{ background: taskPieGradient }">
              <div class="absolute inset-7 flex flex-col items-center justify-center rounded-full bg-white">
                <span class="text-2xl font-semibold text-slate-900">{{ totalTasks }}</span>
                <span class="text-xs text-slate-400">任务</span>
              </div>
            </div>
            <div class="space-y-2">
              <div v-for="item in taskStatusItems" :key="item.key" class="flex items-center justify-between text-sm">
                <span class="inline-flex min-w-0 items-center gap-2 text-slate-600">
                  <i class="h-2.5 w-2.5 shrink-0 rounded-full" :style="{ background: item.color }"></i>
                  <span class="truncate">{{ item.label }}</span>
                </span>
                <span class="font-semibold text-slate-900">{{ item.value }}</span>
              </div>
            </div>
          </div>
        </article>

        <article class="rounded-lg border border-slate-200 bg-white">
          <div class="flex h-12 items-center justify-between border-b border-slate-200 px-4">
            <div>
              <h2 class="text-sm font-semibold text-slate-900">活跃用户排行</h2>
              <p class="text-xs text-slate-400">按运行次数排序</p>
            </div>
            <Users :size="16" class="text-slate-400" />
          </div>
          <div class="space-y-3 p-4">
            <div v-for="user in topUserRows" :key="user.user_id" class="grid grid-cols-[92px_1fr_84px] items-center gap-3 text-sm">
              <div class="min-w-0">
                <p class="truncate font-medium text-slate-900">{{ user.name }}</p>
                <p class="text-xs text-slate-400">#{{ user.user_id }}</p>
              </div>
              <div class="h-7 overflow-hidden rounded bg-slate-100">
                <div class="flex h-full min-w-7 items-center bg-sky-500 px-2 text-xs text-white" :style="{ width: `${user.width}%` }">
                  {{ user.run_count }}
                </div>
              </div>
              <span class="text-right text-xs text-slate-500">{{ formatNumber(user.tokens) }} Token</span>
            </div>
            <div v-if="!topUserRows.length && !loading" class="py-10 text-center text-sm text-slate-500">暂无活跃用户数据。</div>
          </div>
        </article>
      </section>
    </div>

    <section class="mt-5 rounded-lg border border-slate-200 bg-white">
      <div class="flex h-12 items-center justify-between border-b border-slate-200 px-4">
        <h2 class="text-sm font-semibold text-slate-900">每日明细</h2>
        <span class="text-xs text-slate-400">{{ dailyRows.length }} 天</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[680px] text-left text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th class="px-4 py-3 font-medium">日期</th>
              <th class="px-4 py-3 font-medium">运行次数</th>
              <th class="px-4 py-3 font-medium">消息数</th>
              <th class="px-4 py-3 font-medium">消耗</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            <tr v-for="item in dailyRows" :key="item.date" class="hover:bg-slate-50/60">
              <td class="px-4 py-3 text-slate-700">{{ item.date }}</td>
              <td class="px-4 py-3 font-medium text-slate-900">{{ item.runs }}</td>
              <td class="px-4 py-3 text-slate-600">{{ item.messages }}</td>
              <td class="px-4 py-3 text-slate-600">{{ formatNumber(item.tokens) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Activity, Download, ListChecks, MessageSquare, PieChart, RefreshCcw, Users, Zap } from 'lucide-vue-next'
import * as adminApi from '../../api/admin'
import type { AdminUsage } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'
import { downloadFile } from '../../utils/download'

const usage = ref<AdminUsage | null>(null)
const loading = ref(false)
const errorMsg = ref('')
const days = ref(14)
const exporting = ref(false)
const gridLines = [36, 84, 132, 180, 228]
const statusColors: Record<string, string> = {
  queued: '#9a9aa0',
  running: '#0071e3',
  finished: '#34c759',
  failed: '#ff453a',
  cancelled: '#ff9f0a',
  unknown: '#6e6e73',
}

const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN').format(value || 0)
const shortDate = (value: string) => value.slice(5)

const summaryItems = computed(() => {
  const summary = usage.value?.summary
  return [
    { label: '运行次数', value: formatNumber(summary?.total_runs || 0), hint: `${days.value} 天趋势已同步`, icon: Activity, tone: 'bg-blue-50 text-blue-700' },
    { label: '成功率', value: `${summary?.success_rate || 0}%`, hint: `${formatNumber(summary?.failed_runs || 0)} 次失败`, icon: Zap, tone: 'bg-emerald-50 text-emerald-700' },
    { label: '模型消耗', value: formatNumber(summary?.total_tokens || 0), hint: '累计模型调用消耗', icon: ListChecks, tone: 'bg-amber-50 text-amber-700' },
    { label: '消息总数', value: formatNumber(summary?.total_messages || 0), hint: '用户与助手消息合计', icon: MessageSquare, tone: 'bg-violet-50 text-violet-700' },
  ]
})

const dailyRows = computed(() => usage.value?.daily || [])
const maxChartValue = computed(() => Math.max(1, ...dailyRows.value.flatMap((item) => [item.runs, item.messages])))
const chartPoints = computed(() => {
  const rows = dailyRows.value
  const step = rows.length > 1 ? 656 / (rows.length - 1) : 0
  const y = (value: number) => 228 - (value / maxChartValue.value) * 192
  return rows.map((item, index) => ({
    ...item,
    x: 44 + step * index,
    runY: y(item.runs),
    messageY: y(item.messages),
  }))
})
const runLinePoints = computed(() => chartPoints.value.map((item) => `${item.x},${item.runY}`).join(' '))
const messageLinePoints = computed(() => chartPoints.value.map((item) => `${item.x},${item.messageY}`).join(' '))

const taskStatusItems = computed(() => {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    finished: '成功',
    failed: '失败',
    cancelled: '已取消',
  }
  const rows = Object.entries(usage.value?.task_status || {})
  if (!rows.length) return [{ key: 'empty', label: '暂无数据', value: 0, color: '#c7c7cc' }]
  return rows.map(([key, value]) => ({
    key,
    label: labels[key] || key,
    value,
    color: statusColors[key] || statusColors.unknown,
  }))
})
const totalTasks = computed(() => taskStatusItems.value.reduce((sum, item) => sum + item.value, 0))
const taskPieGradient = computed(() => {
  if (!totalTasks.value) return 'conic-gradient(#ececf0 0deg 360deg)'
  let cursor = 0
  const segments = taskStatusItems.value.map((item) => {
    const end = cursor + (item.value / totalTasks.value) * 360
    const segment = `${item.color} ${cursor}deg ${end}deg`
    cursor = end
    return segment
  })
  return `conic-gradient(${segments.join(', ')})`
})

const topUserRows = computed(() => {
  const rows = usage.value?.top_users || []
  const maxRuns = Math.max(1, ...rows.map((item) => item.run_count))
  return rows.map((item) => ({
    ...item,
    width: Math.max(8, Math.round((item.run_count / maxRuns) * 100)),
  }))
})

const loadUsage = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    usage.value = await adminApi.getAdminUsage(days.value)
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '读取使用情况失败')
  } finally {
    loading.value = false
  }
}

watch(days, loadUsage)
const exportCsv = async () => {
  exporting.value = true
  try {
    await downloadFile('/admin/usage/export', { days: days.value }, 'usage_report.csv')
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(loadUsage)
</script>
