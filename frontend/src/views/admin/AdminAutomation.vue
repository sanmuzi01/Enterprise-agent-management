<template>
  <div class="p-6">
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-sm font-semibold text-slate-900">AI 工作成果效果</p>
        <p class="mt-1 text-xs text-slate-500">材料整理的通过率、修改比例、办结耗时、拦截与重复草稿、模型成本，以及相对手工办理的估算节省。</p>
      </div>
      <div class="flex items-center gap-2">
        <select v-model.number="days" aria-label="统计范围" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs" @change="load">
          <option :value="7">近 7 天</option><option :value="30">近 30 天</option><option :value="90">近 90 天</option>
        </select>
        <button class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50" :disabled="loading" @click="load">
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />刷新
        </button>
      </div>
    </div>
    <p v-if="error" role="alert" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ error }}</p>

    <template v-if="data">
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="metric-tiles">
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-xs text-slate-500">处理材料 / 保存为业务草稿</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ data.overall.total }} / {{ data.overall.applied }}</p>
          <p class="mt-1 text-xs text-slate-400">{{ data.overall.users }} 人使用 · 失败 {{ data.overall.failed }} 份</p>
        </article>
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-xs text-slate-500">一次通过率（未修改且一次保存成功）</p>
          <p class="mt-1 text-2xl font-semibold" :class="passClass">{{ pct(data.overall.first_pass_rate) }}</p>
          <p class="mt-1 text-xs text-slate-400">试点目标 ≥ {{ pct(data.targets.first_pass_rate) }}</p>
        </article>
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-xs text-slate-500">业务草稿重复创建</p>
          <p class="mt-1 text-2xl font-semibold" :class="data.overall.duplicate_drafts ? 'text-red-600' : 'text-emerald-600'">{{ data.overall.duplicate_drafts }}</p>
          <p class="mt-1 text-xs text-slate-400">试点目标 0 · 业务系统核对已拦截 {{ data.overall.business_blocked }} 份</p>
        </article>
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-xs text-slate-500">估算节省人工时间</p>
          <p class="mt-1 text-2xl font-semibold text-slate-900">{{ hours(data.overall.estimated_saved_minutes) }}</p>
          <p class="mt-1 text-xs text-slate-400">= (手工基准 − 实际办结中位数) × 已保存份数，基准为管理员设定的估算值</p>
        </article>
      </section>

      <section class="mt-5 rounded-lg border border-slate-200 bg-white">
        <div class="border-b border-slate-100 px-4 py-3">
          <p class="text-sm font-semibold text-slate-900">按工作流</p>
          <p class="mt-0.5 text-xs text-slate-400">办结耗时从提交材料算到保存业务草稿，包含员工核对修改的时间；整理耗时仅为模型处理时间。</p>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[960px] text-left text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500">
              <tr>
                <th class="px-4 py-2 font-medium">工作流</th><th class="px-3 py-2 font-medium">材料 / 已保存</th>
                <th class="px-3 py-2 font-medium">保存率</th><th class="px-3 py-2 font-medium">一次通过率</th>
                <th class="px-3 py-2 font-medium">平均修改比例</th><th class="px-3 py-2 font-medium">重试率</th>
                <th class="px-3 py-2 font-medium">整理耗时</th><th class="px-3 py-2 font-medium">办结耗时</th>
                <th class="px-3 py-2 font-medium">手工基准</th><th class="px-3 py-2 font-medium">估算节省</th>
                <th class="px-3 py-2 font-medium">Token / 份</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="r in data.workflows" :key="r.kind">
                <td class="px-4 py-2.5 font-medium text-slate-800">{{ r.name }}</td>
                <td class="px-3 py-2.5">{{ r.total }} / {{ r.applied }}</td>
                <td class="px-3 py-2.5">{{ pct(r.apply_rate) }}</td>
                <td class="px-3 py-2.5">{{ pct(r.first_pass_rate) }}</td>
                <td class="px-3 py-2.5">{{ pct(r.avg_edit_ratio) }}</td>
                <td class="px-3 py-2.5">{{ pct(r.retry_rate) }}</td>
                <td class="px-3 py-2.5">{{ secs(r.median_generate_seconds) }}</td>
                <td class="px-3 py-2.5">
                  {{ secs(r.median_apply_seconds) }}
                  <span v-if="target(r.kind)" class="block text-[11px] text-slate-400">目标 ≤ {{ secs(target(r.kind)) }}</span>
                </td>
                <td class="px-3 py-2.5">
                  <input :aria-label="`${r.name}手工基准（分钟）`" type="number" min="0.5" max="600" step="0.5" class="h-7 w-16 rounded border border-slate-200 px-1.5 text-xs"
                    :value="r.baseline_minutes" @change="saveBaseline(r.kind, ($event.target as HTMLInputElement).value)" /> 分
                  <span class="ml-1 block text-[11px]" :class="r.baseline_source === 'measured' ? 'text-emerald-700' : 'text-slate-400'" data-testid="baseline-source">{{ r.baseline_source === 'measured' ? `实测（${r.baseline_samples} 个样本）` : r.baseline_source === 'admin' ? '管理员设定' : '默认值，未实测' }}</span>
                </td>
                <td class="px-3 py-2.5">{{ hours(r.estimated_saved_minutes) }}</td>
                <td class="px-3 py-2.5">{{ r.tokens_per_applied ?? '—' }}<span v-if="r.cost_per_applied !== null" class="block text-[11px] text-slate-400">≈ ¥{{ r.cost_per_applied }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <p class="mt-3 text-xs text-slate-400">
        样本太少时比例仅供参考。模型成本：{{ data.token_price_configured ? '按已配置的 Token 单价折算为人民币' : '还没有配置 Token 单价，仅显示 Token 数，不估算金额' }}。
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { getAutomationMetrics, setWorkflowBaseline, type AutomationMetrics } from '../../api/workCenter'
import { getErrorMessage } from '../../utils/request'

const days = ref(30), loading = ref(false), error = ref('')
const data = ref<AutomationMetrics | null>(null)

const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 1000) / 10}%`)
const secs = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v >= 60 ? `${(v / 60).toFixed(1)} 分钟` : `${v.toFixed(1)} 秒`)
const hours = (minutes: number | null) => (minutes === null ? '—' : minutes >= 60 ? `${(minutes / 60).toFixed(1)} 小时` : `${minutes.toFixed(0)} 分钟`)
const target = (kind: string) => data.value?.targets.median_apply_seconds[kind]
const passClass = computed(() => {
  const rate = data.value?.overall.first_pass_rate
  if (rate === null || rate === undefined) return 'text-slate-900'
  return rate >= (data.value?.targets.first_pass_rate ?? 0.8) ? 'text-emerald-600' : 'text-amber-600'
})

async function load() {
  loading.value = true; error.value = ''
  try { data.value = await getAutomationMetrics(days.value) } catch (e) { error.value = getErrorMessage(e, '效果指标加载失败') } finally { loading.value = false }
}
async function saveBaseline(kind: string, value: string) {
  try { await setWorkflowBaseline(kind, Number(value)) } catch (e) { error.value = getErrorMessage(e, '保存手工基准失败') }
  await load()
}
onMounted(load)
</script>
