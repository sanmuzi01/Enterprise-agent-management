<template>
  <div class="space-y-2 text-xs" data-testid="self-service-metrics">
    <p class="text-slate-400">员工自助（近 {{ metrics?.days ?? 30 }} 天）· 只有员工明确点了“已解决”才算自助解决，看过文章不算</p>
    <div v-if="metrics" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div v-for="cell in cells" :key="cell.label" class="rounded border border-slate-200 px-2 py-2">
        <p class="text-slate-400">{{ cell.label }}</p>
        <p class="text-base font-semibold tabular-nums text-slate-900">{{ cell.value }}</p>
      </div>
    </div>
    <div v-if="metrics?.maintenance.length" class="rounded border border-amber-200 bg-amber-50 px-3 py-2">
      <p class="font-medium text-amber-900">需要更新的知识文章</p>
      <ul class="mt-1 space-y-0.5 text-amber-900">
        <li v-for="a in metrics.maintenance" :key="a.id">「{{ a.title || a.id }}」：{{ a.reasons?.join('；') }}</li>
      </ul>
    </div>
    <div v-if="metrics?.articles.length" class="overflow-x-auto">
      <table class="w-full min-w-[480px] text-left">
        <thead class="text-slate-400">
          <tr><th class="py-1 pr-2 font-medium">文章</th><th class="pr-2 font-medium">推荐</th><th class="pr-2 font-medium">看过</th>
            <th class="pr-2 font-medium">解决</th><th class="pr-2 font-medium">解决率</th><th class="font-medium">有用评价</th></tr>
        </thead>
        <tbody class="tabular-nums text-slate-700">
          <tr v-for="a in metrics.articles.slice(0, 10)" :key="a.id" class="border-t border-slate-100">
            <td class="py-1 pr-2">{{ a.title || a.id }}</td><td class="pr-2">{{ a.recommended }}</td><td class="pr-2">{{ a.viewed }}</td>
            <td class="pr-2">{{ a.solved }}<span v-if="a.reopened" class="text-red-600">（{{ a.reopened }} 次又报修）</span></td>
            <td class="pr-2">{{ pct(a.solve_rate) }}</td><td>{{ a.helpful }} / {{ a.helpful + a.unhelpful }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { selfServiceMetrics, type SelfServiceMetrics } from '../../api/financeExtras'

const props = defineProps<{ teamId: number }>()
const metrics = ref<SelfServiceMetrics | null>(null)
const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
const cells = computed(() => metrics.value ? [
  { label: '推荐过知识的问题', value: metrics.value.sessions_with_recommendation },
  { label: '明确确认解决', value: metrics.value.confirmed_solved },
  { label: '转成工单', value: metrics.value.converted_to_ticket },
  { label: '自助解决率', value: pct(metrics.value.self_solve_rate) },
  { label: '重新打开率', value: pct(metrics.value.reopen_rate) },
  { label: '推荐文章次数', value: metrics.value.recommendations },
  { label: '没有反馈', value: metrics.value.no_feedback },
  { label: '待维护文章', value: metrics.value.maintenance.length },
] : [])

async function load() {
  try {
    metrics.value = await selfServiceMetrics(props.teamId)
  } catch {
    metrics.value = null
  }
}

watch(() => props.teamId, load)
onMounted(load)
</script>
