<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="hr-module">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">入转调离</h2>
        <p class="mt-0.5 text-xs text-slate-400">人事发起并预检 → 员工所在部门负责人批准 → 人事 / IT / 财务 / 负责人 / 员工各办各的 → 人事办结。</p>
      </div>
      <div class="flex flex-wrap items-center gap-1 text-xs">
        <button v-for="t in tabs" :key="t.value" @click="setTab(t.value)" class="rounded px-2.5 py-1"
          :class="tab === t.value ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50'" :data-testid="`hr-tab-${t.value}`">
          {{ t.label }}<span v-if="t.count" class="ml-1 rounded-full bg-white/80 px-1.5 text-indigo-700">{{ t.count }}</span>
        </button>
      </div>
    </div>

    <!-- 我的办理任务 -->
    <ul v-if="tab === 'tasks'" class="divide-y divide-slate-100" data-testid="hr-my-tasks">
      <li v-for="t in tasks" :key="t.taskId" class="flex items-center justify-between gap-3 px-4 py-3">
        <button class="min-w-0 text-left" @click="open(t.caseId)">
          <p class="truncate text-sm text-slate-900">{{ t.caseTypeLabel }} · {{ t.employeeName }} · {{ t.title }}</p>
          <p class="mt-0.5 text-xs" :class="t.overdue ? 'text-red-600' : 'text-slate-400'">期限 {{ t.dueDate }}<span v-if="t.overdue">，已逾期</span> · 以「{{ t.ownerLabel }}」身份办理</p>
        </button>
        <button @click="finish(t.caseId, t.taskId, 'done')" :disabled="busy" class="shrink-0 rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50" :data-testid="`hr-task-done-${t.taskId}`">办好了</button>
      </li>
      <li v-if="!tasks.length" class="px-4 py-8 text-center text-sm text-slate-400">没有分给你的办理任务</li>
    </ul>

    <!-- 事项列表（待我批准 / 人事台 / 与我有关） -->
    <template v-else>
      <div v-if="tab === 'hr'" class="space-y-3 border-b border-slate-100 px-4 py-3">
        <p v-if="narrative" class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="hr-narrative">{{ narrative }}</p>
        <button @click="toggleForm" class="rounded border border-indigo-200 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-50" data-testid="hr-new">发起事项</button>
        <div v-if="form.visible" class="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs" data-testid="hr-form">
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <label class="space-y-1"><span class="text-slate-500">事项类型</span>
              <select v-model="form.caseType" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="hr-type">
                <option v-for="(l, k) in TYPE_LABELS" :key="k" :value="k">{{ l }}</option>
              </select></label>
            <label class="space-y-1"><span class="text-slate-500">员工所在部门</span>
              <select v-model.number="form.teamId" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="hr-team">
                <option :value="0">请选择</option><option v-for="t in options.teams" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select></label>
            <label class="space-y-1"><span class="text-slate-500">员工</span>
              <select v-model.number="form.employee" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="hr-employee">
                <option :value="0">请选择</option>
                <option v-for="m in teamMembers" :key="m.user_id" :value="m.user_id">{{ m.name }}{{ m.is_head ? '（负责人）' : '' }}</option>
              </select></label>
            <label v-if="form.caseType === 'TRANSFER'" class="space-y-1"><span class="text-slate-500">调往部门</span>
              <select v-model.number="form.target" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="hr-target">
                <option :value="0">请选择</option><option v-for="t in options.teams.filter((x) => x.id !== form.teamId)" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select></label>
            <label class="space-y-1"><span class="text-slate-500">生效日期</span>
              <input v-model="form.date" type="date" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="hr-date" /></label>
            <label class="space-y-1"><span class="text-slate-500">岗位</span>
              <input v-model="form.position" maxlength="80" class="w-full rounded border border-slate-200 px-2 py-1.5" /></label>
          </div>
          <input v-model="form.reason" maxlength="500" placeholder="原因 / 备注（可选）" class="w-full rounded border border-slate-200 px-2 py-1.5" />
          <div class="flex flex-wrap items-center gap-2">
            <button @click="runPrecheck" :disabled="busy || !formReady" class="rounded border border-slate-300 px-3 py-1.5 hover:bg-white disabled:opacity-50" data-testid="hr-precheck">先检查</button>
            <button @click="submit" :disabled="busy || !formReady || !pre || !pre.canSubmit" class="rounded bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-700 disabled:opacity-50" data-testid="hr-submit">提交负责人批准</button>
            <span v-if="!pre" class="text-slate-400">提交前需要先检查</span>
          </div>
          <CheckList v-if="pre" :checks="pre.checks" empty="检查没有发现问题" />
        </div>
      </div>

      <ul class="divide-y divide-slate-100" data-testid="hr-cases">
        <li v-for="c in cases" :key="c.id">
          <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50" :class="selected?.id === c.id ? 'bg-indigo-50/50' : ''"
            @click="open(c.id)" :data-testid="`hr-row-${c.id}`">
            <div class="min-w-0">
              <p class="truncate text-sm text-slate-900">#{{ c.id }} · {{ c.caseTypeLabel }} · {{ c.employeeName }}<span v-if="c.targetTeamName"> → {{ c.targetTeamName }}</span></p>
              <p class="mt-0.5 text-xs text-slate-400">{{ c.teamName }} · 生效 {{ c.effectiveDate }}<span v-if="c.totalTasks"> · 清单 {{ c.totalTasks - c.openTasks }}/{{ c.totalTasks }}</span></p>
            </div>
            <div class="flex shrink-0 items-center gap-1.5 text-xs">
              <span v-if="c.effectPending" class="rounded bg-amber-50 px-2 py-0.5 text-amber-700">系统变更待落实</span>
              <span class="rounded px-2 py-0.5" :class="STATUS_CLASSES[c.status]">{{ c.statusLabel }}</span>
            </div>
          </button>
        </li>
        <li v-if="!cases.length" class="px-4 py-8 text-center text-sm text-slate-400">没有事项</li>
      </ul>
    </template>

    <!-- 事项详情 -->
    <div v-if="selected" class="space-y-3 border-t border-slate-200 bg-slate-50/60 px-4 py-4" data-testid="hr-detail">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-sm font-medium text-slate-900">#{{ selected.id }} {{ selected.caseTypeLabel }} · {{ selected.employeeName }}
          <span class="text-xs font-normal text-slate-500">{{ selected.teamName }}<span v-if="selected.targetTeamName"> → {{ selected.targetTeamName }}</span> · 生效 {{ selected.effectiveDate }}</span></p>
        <span class="flex items-center gap-2">
          <button v-if="askAgent" data-testid="hr-ask-agent" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            @click="askAgent(`分析人事事项 #${selected.id}（${selected.caseTypeLabel} · ${selected.employeeName}，生效 ${selected.effectiveDate}）：哪些办理任务还没完成、有没有逾期或风险，下一步该催谁？`)"
          >让助手分析</button>
          <span class="rounded px-2 py-0.5 text-xs" :class="STATUS_CLASSES[selected.status]">{{ selected.statusLabel }}</span>
        </span>
      </div>
      <p class="text-xs text-slate-500">发起人 {{ selected.initiatorName }}<span v-if="selected.approverName"> · 审批人 {{ selected.approverName }}<span v-if="selected.decisionNote">：{{ selected.decisionNote }}</span></span><span v-if="selected.reason"> · {{ selected.reason }}</span></p>
      <CheckList :checks="selected.checks" empty="规则检查没有发现问题" />

      <table v-if="selected.tasks.length" class="w-full text-left text-xs" data-testid="hr-tasks">
        <thead class="text-slate-400"><tr><th class="py-1">办理事项</th><th class="py-1">办理方</th><th class="py-1">期限</th><th class="py-1">状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="t in selected.tasks" :key="t.id" class="border-t border-slate-200">
            <td class="py-1.5 pr-2">{{ t.title }}<span v-if="!t.required" class="ml-1 text-slate-400">（可选）</span></td>
            <td class="py-1.5">{{ t.ownerLabel }}</td>
            <td class="py-1.5" :class="t.overdue ? 'text-red-600' : ''">{{ t.dueDate }}</td>
            <td class="py-1.5">{{ TASK_LABELS[t.status] }}<span v-if="t.doneByName" class="text-slate-400"> · {{ t.doneByName }}</span><span v-if="t.note" class="text-slate-400"> · {{ t.note }}</span></td>
            <td class="py-1.5 text-right">
              <template v-if="t.status === 'OPEN' && selected.status === 'IN_PROGRESS' && selected.myRoles.includes(t.owner)">
                <button @click="finish(selected.id, t.id, 'done')" :disabled="busy" class="rounded bg-emerald-600 px-2 py-0.5 text-white">办好了</button>
                <button v-if="!t.required" @click="skip(t)" :disabled="busy" class="ml-1 rounded border border-slate-200 px-2 py-0.5">不需要</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="flex flex-wrap items-center gap-2 text-xs">
        <template v-if="selected.status === 'PENDING_APPROVAL' && selected.myRoles.includes('MANAGER')">
          <input v-model="note" placeholder="审批意见（可选）" class="min-w-[160px] flex-1 rounded border border-slate-200 px-2 py-1.5" />
          <button @click="doAct('approve')" :disabled="busy" class="rounded bg-emerald-600 px-3 py-1.5 text-white" data-testid="hr-approve">批准并生成办理清单</button>
          <button @click="doAct('reject')" :disabled="busy" class="rounded border border-red-200 px-3 py-1.5 text-red-700">不批准</button>
        </template>
        <template v-if="selected.myRoles.includes('HR') && ['PENDING_APPROVAL', 'IN_PROGRESS'].includes(selected.status)">
          <button @click="doAct('recheck')" :disabled="busy" class="rounded border border-slate-200 px-3 py-1.5 hover:bg-white">重新检查</button>
          <button v-if="selected.status === 'IN_PROGRESS'" @click="doAct('complete')" :disabled="busy" class="rounded bg-indigo-600 px-3 py-1.5 text-white" data-testid="hr-complete">办结</button>
          <button @click="doAct('cancel')" :disabled="busy" class="rounded border border-red-200 px-3 py-1.5 text-red-700">撤销</button>
        </template>
        <template v-if="selected.effectPending">
          <span class="text-amber-700">{{ selected.caseType === 'TRANSFER' ? '还需把员工的部门归属改到新部门' : '还需停用员工的企业账号' }}</span>
          <button v-if="me?.org_admin" @click="doAct('apply-effect')" :disabled="busy" class="rounded bg-amber-600 px-3 py-1.5 text-white" data-testid="hr-apply-effect">立即落实</button>
          <span v-else class="text-slate-400">（需企业管理员操作）</span>
        </template>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import * as api from '../api/hrCases'
import type { CaseStatus, CaseType, HrCase, HrCaseSummary, HrCheck, HrMe, HrTask, MyHrTask } from '../api/hrCases'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

/** 规则检查列表：阻断/需核对/提示，逐条说明。 */
const CheckList = defineComponent({
  props: { checks: { type: Array as () => HrCheck[], required: true }, empty: { type: String, default: '' } },
  setup(p) {
    const cls = { BLOCK: 'border-red-200 bg-red-50 text-red-700', WARN: 'border-amber-200 bg-amber-50 text-amber-800', INFO: 'border-sky-200 bg-sky-50 text-sky-700' }
    const label = { BLOCK: '必须先处理', WARN: '需核对', INFO: '提示' }
    return () => p.checks.length
      ? h('ul', { class: 'space-y-1', 'data-testid': 'hr-checks' }, p.checks.map((c) =>
          h('li', { class: `flex gap-2 rounded border px-2.5 py-1.5 text-xs ${cls[c.level]}` }, [h('span', { class: 'font-medium' }, label[c.level]), h('span', c.message)])))
      : (p.empty ? h('p', { class: 'rounded border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-700' }, p.empty) : null)
  },
})

const TYPE_LABELS: Record<CaseType, string> = { ONBOARDING: '入职', PROBATION: '转正', TRANSFER: '调岗', OFFBOARDING: '离职' }
const TASK_LABELS = { OPEN: '待办', DONE: '已办', SKIPPED: '不需要' } as const
const STATUS_CLASSES: Record<CaseStatus, string> = {
  PENDING_APPROVAL: 'bg-amber-50 text-amber-700', IN_PROGRESS: 'bg-sky-50 text-sky-700', COMPLETED: 'bg-emerald-50 text-emerald-700',
  REJECTED: 'bg-red-50 text-red-700', CANCELLED: 'bg-slate-100 text-slate-400',
}

type Tab = 'tasks' | 'approval' | 'hr' | 'mine'
const me = ref<HrMe | null>(null)
const tab = ref<Tab>('tasks')
const tasks = ref<MyHrTask[]>([])
const cases = ref<HrCaseSummary[]>([])
const approvalCount = ref(0)
const selected = ref<HrCase | null>(null)
const narrative = ref('')
const busy = ref(false)
const note = ref('')
const options = reactive({ teams: [] as { id: number; name: string }[], members: [] as { team_id: number; user_id: number; name: string; is_head: boolean }[] })
const form = reactive({ visible: false, caseType: 'ONBOARDING' as CaseType, teamId: 0, employee: 0, target: 0, date: '', position: '', reason: '' })
const pre = ref<{ checks: HrCheck[]; riskLevel: string; canSubmit: boolean } | null>(null)

const tabs = computed(() => {
  const list: { value: Tab; label: string; count?: number }[] = [{ value: 'tasks', label: '我的办理任务', count: tasks.value.length }]
  if (me.value?.is_head || me.value?.org_admin) list.push({ value: 'approval', label: '待我批准', count: approvalCount.value })
  if (me.value?.is_hr) list.push({ value: 'hr', label: '人事台' })
  list.push({ value: 'mine', label: '与我有关' })
  return list
})
const teamMembers = computed(() => options.members.filter((m) => m.team_id === form.teamId))
const formReady = computed(() => form.teamId && form.employee && form.date && (form.caseType !== 'TRANSFER' || form.target))

watch(() => [form.caseType, form.teamId, form.employee, form.target, form.date], () => { pre.value = null })
watch(() => form.teamId, () => { form.employee = 0 })

function input(): api.CaseInput {
  return { case_type: form.caseType, employee_user_id: form.employee, employee_team_id: form.teamId,
    target_team_id: form.caseType === 'TRANSFER' ? form.target : null, effective_date: form.date,
    position: form.position || undefined, reason: form.reason || undefined }
}

async function guarded(action: () => Promise<void>, failure: string) {
  busy.value = true
  try {
    await action()
  } catch (e) {
    toastError(getErrorMessage(e, failure))
  } finally {
    busy.value = false
  }
}

async function loadTasks() {
  tasks.value = await api.myTasks(props.teamId)
}

async function loadCases() {
  if (tab.value === 'tasks') return
  cases.value = await api.listCases(props.teamId, tab.value)
  if (tab.value === 'approval') approvalCount.value = cases.value.length
  if (tab.value === 'hr') narrative.value = (await api.summary(props.teamId)).narrative
}

async function setTab(value: Tab) {
  tab.value = value
  selected.value = null
  await guarded(async () => { await (value === 'tasks' ? loadTasks() : loadCases()) }, '加载失败')
}

async function open(id: number) {
  await guarded(async () => {
    selected.value = await api.getCase(props.teamId, id)
    note.value = ''
  }, '加载事项失败')
}

async function refresh(detail: HrCase) {
  selected.value = detail
  await Promise.all([loadTasks(), loadCases()])
}

async function finish(caseId: number, taskId: number, result: 'done' | 'skip', taskNote?: string) {
  await guarded(async () => {
    const detail = await api.finishTask(props.teamId, caseId, taskId, result, taskNote)
    toastSuccess(result === 'done' ? '已标记办好' : '已标记不需要')
    await refresh(detail)
  }, '提交失败')
}

function skip(t: HrTask) {
  const reason = window.prompt(`「${t.title}」为什么不需要办？`)
  if (reason && reason.trim().length >= 2) finish(selected.value!.id, t.id, 'skip', reason.trim())
}

const ACT_MESSAGES = { approve: '已批准，办理清单已分给各方', reject: '已退回', cancel: '已撤销', complete: '已办结', recheck: '已重新检查', 'apply-effect': '已落实系统变更' }
async function doAct(action: keyof typeof ACT_MESSAGES) {
  if (!selected.value) return
  await guarded(async () => {
    const detail = await api.act(props.teamId, selected.value!.id, action, note.value || undefined)
    toastSuccess(ACT_MESSAGES[action])
    await refresh(detail)
  }, '操作失败')
}

async function toggleForm() {
  form.visible = !form.visible
  if (form.visible && !options.teams.length) {
    await guarded(async () => { Object.assign(options, await api.candidates(props.teamId)) }, '加载部门和员工失败')
  }
}

async function runPrecheck() {
  await guarded(async () => { pre.value = await api.precheck(props.teamId, input()) }, '检查失败')
}

async function submit() {
  await guarded(async () => {
    const created = await api.createCase(props.teamId, input())
    toastSuccess(`${created.caseTypeLabel}事项 #${created.id} 已提交，等待 ${created.teamName} 负责人批准`)
    Object.assign(form, { visible: false, teamId: 0, employee: 0, target: 0, date: '', position: '', reason: '' })
    pre.value = null
    await loadCases()
    selected.value = created
  }, '提交失败')
}

async function init() {
  selected.value = null
  try {
    me.value = await api.getMe(props.teamId)
  } catch {
    me.value = null
    return
  }
  await guarded(async () => {
    await loadTasks()
    if (me.value?.is_head || me.value?.org_admin) approvalCount.value = (await api.listCases(props.teamId, 'approval')).length
    if (tab.value !== 'tasks') await loadCases()
  }, '加载入转调离失败')
}

watch(() => props.teamId, () => { tab.value = 'tasks'; init() })
onMounted(init)

// 助手回复里的卡片 → 打开这条记录
useFocusRecord(['hr_case'], (id) => {
  void open(id)
  void flashRecord(`[data-testid="hr-row-${id}"]`)
})
</script>
