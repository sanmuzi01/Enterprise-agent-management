<template>
  <div class="space-y-4" data-testid="productivity-dashboard">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="max-w-3xl text-xs leading-relaxed text-slate-500">
        三种时间分开看、不相加：<b>估算节省</b>按每类工作的手工基准时间减去助手用时和人工核对用时（最低为 0，只算被采纳 / 完成的）；
        <b>实测用时</b>是助手运行加人工核对的真实耗时；<b>员工反馈</b>是员工自己填的“这次省了多少”。
      </p>
      <label class="flex items-center gap-2 text-xs text-slate-600">统计范围
        <select v-model.number="days" class="h-8 rounded border border-slate-300 px-2" data-testid="productivity-days">
          <option :value="7">近 7 天</option><option :value="30">近 30 天</option><option :value="90">近 90 天</option>
        </select>
      </label>
    </div>

    <p v-if="error" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>
    <div v-if="loading && !data" class="py-12 text-center text-sm text-slate-400">加载中…</div>

    <template v-if="data">
      <section class="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid="productivity-time">
        <div v-for="t in timeCells" :key="t.label" class="rounded-lg border border-slate-200 bg-white p-3">
          <p class="text-xs text-slate-500">{{ t.label }}</p>
          <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900">{{ t.value }}</p>
          <p class="mt-0.5 text-[11px] text-slate-400">{{ t.hint }}</p>
        </div>
      </section>
      <section class="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid="productivity-rates">
        <div v-for="r in rateCells" :key="r.label" class="rounded-lg border border-slate-200 bg-white p-3">
          <p class="text-xs text-slate-500">{{ r.label }}</p>
          <p class="mt-1 text-xl font-semibold tabular-nums" :class="r.tone">{{ r.value }}</p>
          <p class="mt-0.5 text-[11px] text-slate-400">{{ r.hint }}</p>
        </div>
      </section>

      <section class="rounded-lg border border-slate-200 bg-white p-4">
        <h3 class="mb-2 text-sm font-semibold text-slate-900">每天的处理量和估算节省</h3>
        <div v-if="data.trend.length" class="overflow-x-auto">
          <svg :viewBox="`0 0 ${chartWidth} 170`" class="h-44 w-full" preserveAspectRatio="xMinYMid meet" role="img" aria-label="每日处理量与估算节省时间">
            <g v-for="(d, i) in data.trend" :key="d.day">
              <rect :x="40 + i * step + 2" :y="140 - barHeight(d.items, maxItems)" :width="Math.max(4, step / 2 - 3)"
                :height="barHeight(d.items, maxItems)" class="fill-indigo-400" />
              <rect :x="40 + i * step + step / 2" :y="140 - barHeight(d.saved_minutes, maxSaved)" :width="Math.max(4, step / 2 - 3)"
                :height="barHeight(d.saved_minutes, maxSaved)" class="fill-emerald-400" />
              <text v-if="i % labelEvery === 0" :x="40 + i * step + step / 2" y="158" text-anchor="middle" class="fill-slate-500 text-[10px]">{{ d.day.slice(5) }}</text>
            </g>
            <line x1="40" y1="140" :x2="chartWidth" y2="140" class="stroke-slate-200" />
            <text x="0" y="16" class="fill-slate-500 text-[10px]">{{ maxItems }} 件</text>
            <text x="0" y="30" class="fill-slate-500 text-[10px]">{{ maxSaved }} 分</text>
          </svg>
          <p class="mt-1 flex gap-4 text-[11px] text-slate-500">
            <span><span class="mr-1 inline-block h-2 w-2 bg-indigo-400"></span>处理量</span>
            <span><span class="mr-1 inline-block h-2 w-2 bg-emerald-400"></span>估算节省（分钟）</span>
          </p>
        </div>
        <p v-else class="py-6 text-center text-xs text-slate-400">这段时间还没有数据</p>
      </section>

      <div class="grid gap-4 lg:grid-cols-2">
        <section v-for="table in rankTables" :key="table.title" class="rounded-lg border border-slate-200 bg-white p-4">
          <h3 class="mb-2 text-sm font-semibold text-slate-900">{{ table.title }}</h3>
          <div class="overflow-x-auto">
            <table class="w-full min-w-[420px] text-left text-xs">
              <thead class="text-slate-400"><tr>
                <th class="py-1 pr-2 font-medium">名称</th><th class="pr-2 font-medium">处理量</th>
                <th class="pr-2 font-medium">估算节省</th><th class="pr-2 font-medium">采纳率</th><th class="font-medium">失败率</th>
              </tr></thead>
              <tbody class="tabular-nums text-slate-700">
                <tr v-for="r in table.rows" :key="r.id" class="border-t border-slate-100">
                  <td class="py-1 pr-2">{{ r.name }}</td><td class="pr-2">{{ r.items }}</td>
                  <td class="pr-2">{{ minutes(r.saved_minutes) }}</td><td class="pr-2">{{ pct(r.adoption_rate) }}</td>
                  <td>{{ pct(r.failure_rate) }}</td>
                </tr>
                <tr v-if="!table.rows.length"><td colspan="5" class="py-4 text-center text-slate-400">暂无数据</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="rounded-lg border border-slate-200 bg-white p-4">
          <h3 class="mb-2 text-sm font-semibold text-slate-900">失败原因</h3>
          <ul class="space-y-1 text-xs">
            <li v-for="f in data.failure_reasons" :key="f.code" class="flex items-center gap-2">
              <span class="w-36 shrink-0 text-slate-600">{{ failureLabel(f.code) }}</span>
              <span class="h-2 rounded bg-red-300" :style="{ width: `${(f.count / maxFailure) * 60}%` }"></span>
              <span class="tabular-nums text-slate-500">{{ f.count }}</span>
            </li>
            <li v-if="!data.failure_reasons.length" class="text-slate-400">没有失败</li>
          </ul>
        </section>
        <section class="rounded-lg border border-slate-200 bg-white p-4">
          <h3 class="mb-2 text-sm font-semibold text-slate-900">人工改得最多的字段</h3>
          <p class="mb-1 text-[11px] text-slate-400">员工确认前改过的字段：改得多说明这一项识别 / 整理不准，值得优先改进</p>
          <ul class="space-y-1 text-xs">
            <li v-for="f in data.changed_fields" :key="f.field" class="flex items-center gap-2">
              <span class="w-36 shrink-0 truncate text-slate-600">{{ fieldLabel(f.field) }}</span>
              <span class="h-2 rounded bg-amber-300" :style="{ width: `${(f.count / maxField) * 60}%` }"></span>
              <span class="tabular-nums text-slate-500">{{ f.count }}</span>
            </li>
            <li v-if="!data.changed_fields.length" class="text-slate-400">还没有人工修改记录</li>
          </ul>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { ProductivityDashboard } from '../api/productivity'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ load: (days: number) => Promise<ProductivityDashboard> }>()

const days = ref(30)
const data = ref<ProductivityDashboard | null>(null)
const loading = ref(false)
const error = ref('')

const FAILURES: Record<string, string> = {
  MODEL_TIMEOUT: '模型响应超时', MODEL_UNAVAILABLE: '模型不可用', MODEL_NOT_CONFIGURED: '没有配置模型',
  JAVA_SERVICE_UNAVAILABLE: '业务系统不可用', DATABASE_ERROR: '数据库异常', INTERNAL_ERROR: '系统内部错误',
  MAX_ITERATIONS: '步骤太多没有完成', AUTOMATION_FAILED: '材料整理失败', TOOL_EXECUTION_FAILED: '业务操作执行失败',
  REOPENED: '自助解决后又报修', UNKNOWN: '原因未知',
}
const FIELDS: Record<string, string> = {
  invoice_number: '发票号码', invoice_code: '发票代码', issued_at: '开票日期', total_amount: '价税合计',
  amount_without_tax: '金额（不含税）', tax_amount: '税额', seller_name: '销售方名称', seller_tax_id: '销售方税号',
  buyer_name: '购买方名称', buyer_tax_id: '购买方税号', invoice_type: '发票类型', lines: '费用明细', amount: '金额',
  reason: '事由', start_date: '开始日期', end_date: '结束日期', leave_type: '假期类型', title: '标题',
  description: '描述', category: '类别', priority: '优先级', items: '采购明细', tasks: '后续待办', content: '内容',
}
const failureLabel = (code: string) => FAILURES[code] || '其他原因'
const fieldLabel = (field: string) => FIELDS[field] || '其他字段'
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${Math.round(v * 1000) / 10}%`)
const minutes = (m: number) => (m >= 120 ? `${Math.round(m / 6) / 10} 小时` : `${Math.round(m)} 分钟`)

const timeCells = computed(() => {
  const t = data.value!.totals
  return [
    { label: '总处理量', value: t.items, hint: `活跃用户 ${t.active_users} 人` },
    { label: '估算节省', value: minutes(t.estimated_saved_minutes), hint: '按基准时间估算' },
    { label: '实测用时', value: minutes(t.measured_minutes), hint: '助手运行 + 人工核对的真实耗时' },
    { label: '员工反馈节省', value: t.reported_count ? minutes(t.reported_saved_minutes) : '—', hint: `${t.reported_count} 次反馈` },
  ]
})
const rateCells = computed(() => {
  const r = data.value!.rates
  const t = data.value!.totals
  return [
    { label: '草稿采纳率', value: pct(r.adoption), hint: `${t.adopted} / ${t.drafts} 份草稿被确认`, tone: 'text-slate-900' },
    { label: '原样采纳率', value: pct(r.as_is), hint: `${t.adopted_as_is} 份没改字段直接确认`, tone: 'text-slate-900' },
    { label: '业务完成率', value: pct(r.completion), hint: `${t.completed} / ${t.initiated} 件正式业务完成`, tone: 'text-slate-900' },
    { label: '助手失败率', value: pct(r.failure), hint: `${t.failed_runs} / ${t.runs} 次运行失败`,
      tone: (r.failure ?? 0) > 0.1 ? 'text-red-600' : 'text-slate-900' },
  ]
})
const rankTables = computed(() => [
  { title: '各部门', rows: data.value!.by_team },
  { title: '各助手', rows: data.value!.by_agent },
  { title: '各类工作', rows: data.value!.by_source },
])
const step = computed(() => Math.max(14, Math.min(40, 640 / Math.max(1, data.value?.trend.length || 1))))
const chartWidth = computed(() => 40 + step.value * (data.value?.trend.length || 1))
const labelEvery = computed(() => Math.max(1, Math.ceil((data.value?.trend.length || 1) / 10)))
const maxItems = computed(() => Math.max(1, ...(data.value?.trend.map((d) => d.items) || [1])))
const maxSaved = computed(() => Math.max(1, Math.ceil(Math.max(...(data.value?.trend.map((d) => d.saved_minutes) || [1])))))
const maxFailure = computed(() => Math.max(1, ...(data.value?.failure_reasons.map((f) => f.count) || [1])))
const maxField = computed(() => Math.max(1, ...(data.value?.changed_fields.map((f) => f.count) || [1])))
const barHeight = (value: number, max: number) => (value <= 0 ? 0 : Math.max(2, (value / max) * 120))

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    data.value = await props.load(days.value)
  } catch (e) {
    error.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

watch(days, refresh)
onMounted(refresh)
</script>
