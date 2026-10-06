<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="resp-module">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">责任协同</h2>
        <p class="mt-0.5 text-xs text-slate-400">会议纪要和工作文本 → AI 提取行动项 → 负责人核对并指派 → 员工接受、执行、提交 → 验收人验收。AI 只起草和提醒，指派、接受、验收都由人决定。</p>
      </div>
      <div class="flex flex-wrap items-center gap-1 text-xs">
        <button v-for="t in tabs" :key="t.value" @click="setTab(t.value)" class="rounded px-2.5 py-1" :data-testid="`resp-tab-${t.value}`"
          :class="tab === t.value ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50'">
          {{ t.label }}<span v-if="t.count" class="ml-1 rounded-full bg-white/80 px-1.5 text-indigo-700">{{ t.count }}</span>
        </button>
      </div>
    </div>
    <p v-if="error" class="mx-4 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{{ error }}</p>

    <!-- 我的责任 -->
    <div v-if="tab === 'mine'" class="space-y-4 px-4 py-4" data-testid="resp-mine">
      <template v-for="g in mineGroups" :key="g.key">
        <div v-if="g.items.length">
          <h3 class="mb-1 text-xs font-medium text-slate-500">{{ g.label }}（{{ g.items.length }}）</h3>
          <ul class="divide-y divide-slate-100 rounded border border-slate-200">
            <li v-for="x in g.items" :key="x.id"><TaskRow :task="x" @open="openTask" /></li>
          </ul>
        </div>
      </template>
      <div v-if="collab.length">
        <h3 class="mb-1 text-xs font-medium text-slate-500">我协办的（{{ collab.length }}）</h3>
        <ul class="divide-y divide-slate-100 rounded border border-slate-200"><li v-for="x in collab" :key="x.id"><TaskRow :task="x" @open="openTask" /></li></ul>
      </div>
      <p v-if="!mine.length && !collab.length" class="py-6 text-center text-sm text-slate-400">现在没有分给你的责任事项</p>
    </div>

    <!-- 待我验收 -->
    <ul v-else-if="tab === 'review'" class="divide-y divide-slate-100" data-testid="resp-review">
      <li v-for="x in review" :key="x.id"><TaskRow :task="x" @open="openTask" /></li>
      <li v-if="!review.length" class="px-4 py-8 text-center text-sm text-slate-400">没有等你验收的成果</li>
    </ul>

    <!-- 我指派的 -->
    <ul v-else-if="tab === 'assigned'" class="divide-y divide-slate-100" data-testid="resp-assigned">
      <li v-for="x in assigned" :key="x.id"><TaskRow :task="x" @open="openTask" /></li>
      <li v-if="!assigned.length" class="px-4 py-8 text-center text-sm text-slate-400">你还没有正式指派过责任事项</li>
    </ul>

    <!-- 部门看板（负责人） -->
    <div v-else-if="tab === 'team'" class="space-y-4 px-4 py-4" data-testid="resp-team">
      <template v-if="summary">
        <p class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="resp-narrative">{{ summary.narrative }}</p>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs">
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">本周完成率</p><p class="text-lg font-semibold">{{ summary.week.rate === null ? '—' : summary.week.rate + '%' }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">首次验收通过率</p><p class="text-lg font-semibold">{{ pct(summary.metrics.firstPassRate) }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">按时提交率</p><p class="text-lg font-semibold">{{ pct(summary.metrics.onTimeSubmitRate) }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">接受责任中位时间</p><p class="text-lg font-semibold">{{ summary.metrics.acceptMedianHours === null ? '—' : summary.metrics.acceptMedianHours + ' 小时' }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">AI 草稿一次匹配准确率</p><p class="text-lg font-semibold">{{ pct(summary.metrics.aiMatchRate) }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">发布前发现的遗漏</p><p class="text-lg font-semibold">{{ summary.metrics.missingFoundBeforePublish }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">责任变更次数</p><p class="text-lg font-semibold">{{ summary.metrics.changeCount }}</p></div>
          <div class="rounded border border-slate-200 p-2"><p class="text-slate-400">无主任务比例</p><p class="text-lg font-semibold">{{ pct(summary.metrics.ownerlessRatio) }}</p></div>
        </div>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 text-xs">
          <div v-if="summary.waitingAccept.length"><h4 class="mb-1 font-medium text-slate-500">还没有被接受</h4>
            <ul class="space-y-1"><li v-for="b in summary.waitingAccept" :key="b.id"><button class="text-left text-slate-700 hover:text-indigo-600" @click="openTask(b.id)">{{ b.title }} · {{ b.responsibleName }}（已等 {{ b.hoursWaiting }} 小时）<span v-if="b.objection" class="text-sky-700"> · 有异议</span></button></li></ul></div>
          <div v-if="summary.blocked.length"><h4 class="mb-1 font-medium text-slate-500">受阻</h4>
            <ul class="space-y-1"><li v-for="b in summary.blocked" :key="b.id"><button class="text-left text-slate-700 hover:text-indigo-600" @click="openTask(b.id)">{{ b.title }} · {{ b.responsibleName }}：{{ b.reason }}（{{ b.hours }} 小时）</button></li></ul></div>
          <div v-if="summary.overdue.length"><h4 class="mb-1 font-medium text-red-600">已逾期</h4>
            <ul class="space-y-1"><li v-for="b in summary.overdue" :key="b.id"><button class="text-left text-slate-700 hover:text-indigo-600" @click="openTask(b.id)">{{ b.title }} · {{ b.responsibleName }}（{{ b.dueDate }}）</button></li></ul></div>
          <div v-if="summary.load.length"><h4 class="mb-1 font-medium text-slate-500">在手责任</h4>
            <ul class="space-y-1"><li v-for="l in summary.load" :key="l.userId" :class="l.overloaded ? 'text-amber-700' : 'text-slate-600'">{{ l.name }}：{{ l.open }} 项（高优先级 {{ l.highPriority }}）<span v-if="l.overloaded"> · 请确认是否过载</span></li></ul></div>
        </div>
      </template>
      <div class="flex items-center gap-2 text-xs"><span class="text-slate-500">看板分组</span>
        <select v-model="groupBy" class="rounded border border-slate-200 px-2 py-1"><option value="status">按状态</option><option value="person">按员工</option><option value="plan">按项目（计划）</option><option value="due">按截止日期</option></select></div>
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-3" data-testid="resp-board">
        <div v-for="col in boardColumns" :key="col.key" class="rounded border border-slate-200">
          <h4 class="border-b border-slate-100 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600">{{ col.label }}（{{ col.items.length }}）</h4>
          <ul class="divide-y divide-slate-100"><li v-for="x in col.items" :key="x.id"><TaskRow :task="x" compact @open="openTask" /></li></ul>
        </div>
      </div>
      <p v-if="!team.length" class="py-4 text-center text-sm text-slate-400">部门里还没有已发布的责任事项</p>
    </div>

    <!-- 责任计划 -->
    <ul v-else-if="tab === 'plans'" class="divide-y divide-slate-100" data-testid="resp-plans">
      <li v-for="p in plans" :key="p.id">
        <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50" @click="openPlan(p.id)" :data-testid="`resp-plan-${p.id}`">
          <span class="min-w-0"><span class="block truncate text-sm text-slate-900">{{ p.title }}</span>
            <span class="text-xs text-slate-400">{{ p.sourceLabel }} · {{ p.createdByName }} · {{ p.taskCount }} 项<span v-if="p.status === 'DRAFT' && p.issueCount" class="text-red-600">，{{ p.issueCount }} 项待补充</span><span v-else-if="p.status !== 'DRAFT'">，完成 {{ p.doneCount }}</span></span></span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs" :class="p.status === 'DRAFT' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'">{{ p.statusLabel }}</span>
        </button>
      </li>
      <li v-if="!plans.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有责任计划。到「文本整理」粘贴一份会议纪要，让 AI 提取行动项。</li>
    </ul>

    <!-- 文本整理（AI） -->
    <div v-show="tab === 'extract'" class="p-4">
      <AutomationWorkPanel only-kind="responsibility" :team-id="teamId" @saved="onSaved" @open-result="openPlanFromWork" />
    </div>

    <div v-if="selectedTaskId !== null || selectedPlanId !== null" class="space-y-3 border-t border-slate-200 bg-slate-50 p-4" ref="detailBox">
      <ResponsibilityPlanPanel v-if="selectedPlanId !== null" :key="`plan-${selectedPlanId}`" :team-id="teamId" :plan-id="selectedPlanId" :candidates="candidates"
        @close="selectedPlanId = null" @changed="refreshAll" @open-task="openTask" />
      <ResponsibilityTaskPanel v-if="selectedTaskId !== null" :key="`task-${selectedTaskId}`" :team-id="teamId" :task-id="selectedTaskId" :candidates="candidates"
        @close="selectedTaskId = null" @changed="refreshAll" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, nextTick, onMounted, ref, watch } from 'vue'
import * as api from '../api/responsibility'
import type { Candidates, MineCounts, PlanSummary, RespTask, Summary } from '../api/responsibility'
import { getErrorMessage } from '../utils/request'
import AutomationWorkPanel from './AutomationWorkPanel.vue'
import ResponsibilityPlanPanel from './ResponsibilityPlanPanel.vue'
import ResponsibilityTaskPanel from './ResponsibilityTaskPanel.vue'

const props = defineProps<{ teamId: number; initialTab?: string }>()

type Tab = 'mine' | 'review' | 'assigned' | 'team' | 'plans' | 'extract'
const tab = ref<Tab>('mine')
const error = ref('')
const candidates = ref<Candidates>({ members: [], reviewers: [], me: { user_id: 0, is_head: false } })
const counts = ref<MineCounts>({})
const mine = ref<RespTask[]>([])
const collab = ref<RespTask[]>([])
const review = ref<RespTask[]>([])
const assigned = ref<RespTask[]>([])
const team = ref<RespTask[]>([])
const plans = ref<PlanSummary[]>([])
const summary = ref<Summary | null>(null)
const selectedTaskId = ref<number | null>(null)
const selectedPlanId = ref<number | null>(null)
const groupBy = ref<'status' | 'person' | 'plan' | 'due'>('status')
const detailBox = ref<HTMLElement | null>(null)

const isHead = computed(() => candidates.value.me.is_head)
const tabs = computed(() => {
  const list: { value: Tab; label: string; count?: number }[] = [
    { value: 'mine', label: '我的责任', count: (counts.value.pendingAccept || 0) + (counts.value.overdue || 0) },
    { value: 'review', label: '待我验收', count: counts.value.pendingReview || 0 },
    { value: 'assigned', label: '我指派的', count: counts.value.assignedNotAccepted || 0 },
  ]
  if (isHead.value) list.push({ value: 'team', label: '部门看板', count: counts.value.teamOverdue || 0 })
  list.push({ value: 'plans', label: '责任计划', count: plans.value.filter((p) => p.status === 'DRAFT').length }, { value: 'extract', label: '文本整理' })
  return list
})

// 小组件：一行责任事项
const TaskRow = defineComponent({
  props: { task: { type: Object as () => RespTask, required: true }, compact: Boolean },
  emits: ['open'],
  setup(p, { emit }) {
    const tone: Record<string, string> = { PENDING_ACCEPT: 'bg-amber-100 text-amber-800', NEGOTIATING: 'bg-sky-100 text-sky-800', IN_PROGRESS: 'bg-indigo-100 text-indigo-800',
      BLOCKED: 'bg-red-100 text-red-700', PENDING_REVIEW: 'bg-violet-100 text-violet-800', DONE: 'bg-emerald-100 text-emerald-800', CANCELLED: 'bg-slate-100 text-slate-400' }
    return () => h('button', { class: 'flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-slate-50', 'data-testid': `resp-task-${p.task.id}`,
      onClick: () => emit('open', p.task.id) }, [
      h('span', { class: 'min-w-0' }, [
        h('span', { class: 'block truncate text-sm text-slate-900' }, p.task.title),
        h('span', { class: ['text-xs', p.task.overdue ? 'text-red-600' : 'text-slate-400'] },
          `${p.compact ? '' : (p.task.planTitle || '') + ' · '}${p.task.responsibleName || '待补充'} · 截止 ${p.task.dueDate || '—'}${p.task.overdue ? '（已逾期）' : ''}`)]),
      h('span', { class: ['shrink-0 rounded-full px-2 py-0.5 text-xs', tone[p.task.status] || 'bg-slate-100 text-slate-600'] }, p.task.statusLabel)])
  },
})

const mineGroups = computed(() => {
  const by = (...s: string[]) => mine.value.filter((x) => s.includes(x.status))
  return [
    { key: 'accept', label: '待我接受', items: by('PENDING_ACCEPT') }, { key: 'nego', label: '等负责人处理我的异议', items: by('NEGOTIATING') },
    { key: 'doing', label: '执行中', items: by('IN_PROGRESS') }, { key: 'blocked', label: '受阻', items: by('BLOCKED') },
    { key: 'review', label: '已提交，待验收', items: by('PENDING_REVIEW') }, { key: 'done', label: '已完成', items: by('DONE').slice(0, 5) },
  ]
})

const STATUS_COLUMNS: [string, string][] = [['PENDING_ACCEPT', '待接受'], ['NEGOTIATING', '待重新协商'], ['IN_PROGRESS', '执行中'], ['BLOCKED', '受阻'], ['PENDING_REVIEW', '待验收'], ['DONE', '已完成']]
const boardColumns = computed(() => {
  const rows = team.value.filter((x) => x.status !== 'CANCELLED')
  if (groupBy.value === 'status') return STATUS_COLUMNS.map(([key, label]) => ({ key, label, items: rows.filter((x) => x.status === key) })).filter((c) => c.items.length)
  if (groupBy.value === 'person') {
    const names = [...new Set(rows.map((x) => x.responsibleName || '待补充'))]
    return names.map((n) => ({ key: n, label: n, items: rows.filter((x) => (x.responsibleName || '待补充') === n) }))
  }
  if (groupBy.value === 'plan') {
    const plansInBoard = [...new Set(rows.map((x) => x.planTitle || '未命名计划'))]
    return plansInBoard.map((n) => ({ key: n, label: n, items: rows.filter((x) => (x.planTitle || '未命名计划') === n) }))
  }
  const today = new Date().toISOString().slice(0, 10)
  const weekEnd = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
  const open = rows.filter((x) => x.status !== 'DONE')
  return [{ key: 'overdue', label: '已逾期', items: open.filter((x) => x.overdue) },
    { key: 'week', label: '7 天内到期', items: open.filter((x) => !x.overdue && x.dueDate && x.dueDate <= weekEnd && x.dueDate >= today) },
    { key: 'later', label: '更晚', items: open.filter((x) => !x.overdue && (!x.dueDate || x.dueDate > weekEnd)) }].filter((c) => c.items.length)
})

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v}%`)

async function safe<T>(job: () => Promise<T>, fallback: T): Promise<T> {
  try { return await job() } catch { return fallback }   // 没有权限或服务暂不可用的区块直接留空
}

async function refreshAll() {
  const id = props.teamId
  counts.value = await safe(() => api.getMine(id), {})
  mine.value = await safe(() => api.listTasks(id, 'mine'), [])
  collab.value = await safe(() => api.listTasks(id, 'collab'), [])
  review.value = await safe(() => api.listTasks(id, 'review', 'PENDING_REVIEW'), [])
  assigned.value = await safe(() => api.listTasks(id, 'assigned'), [])
  plans.value = await safe(() => api.listPlans(id), [])
  if (isHead.value) {
    team.value = await safe(() => api.listTasks(id, 'team'), [])
    summary.value = await safe(() => api.getSummary(id), null)
  }
}

async function load() {
  error.value = ''
  try {
    candidates.value = await api.getCandidates(props.teamId)
  } catch (e) {
    error.value = getErrorMessage(e, '加载责任协同失败')
    return
  }
  await refreshAll()
}

async function reveal() {
  await nextTick()
  detailBox.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function openTask(id: number) { selectedTaskId.value = id; void reveal() }
function openPlan(id: number) { selectedPlanId.value = id; void reveal() }
function setTab(value: Tab) { tab.value = value; selectedTaskId.value = null; selectedPlanId.value = null }
function onSaved() { void refreshAll() }
function openPlanFromWork(planId: number) { tab.value = 'plans'; openPlan(planId); void refreshAll() }

watch(() => props.teamId, () => { selectedTaskId.value = null; selectedPlanId.value = null; void load() })
onMounted(async () => {
  await load()
  if (props.initialTab && tabs.value.some((t) => t.value === props.initialTab)) tab.value = props.initialTab as Tab
})
defineExpose({ openPlan, openTask, setTab, refreshAll })
</script>
