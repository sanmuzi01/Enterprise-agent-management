<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="orchestration">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">跨部门协同办理</h2>
        <p class="mt-0.5 text-xs text-slate-400">一段话说清要办的几件事，自动拆成各部门的步骤；每一步单独整理、核对后才写入业务系统。</p>
      </div>
      <select v-if="plans.length > 1" :value="plan?.id" @change="pick(Number(($event.target as HTMLSelectElement).value))"
        aria-label="历史协同计划" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600">
        <option v-for="p in plans" :key="p.id" :value="p.id">#{{ p.id }} {{ p.source_text.slice(0, 16) }}…</option>
      </select>
    </div>

    <div class="space-y-2 px-4 py-3">
      <label for="orchestration-text" class="block text-xs text-slate-500">要办的事情</label>
      <textarea id="orchestration-text" v-model="text" rows="3" maxlength="5000"
        placeholder="例如：下周一到周三请年假，回来报销上个月出差的高铁票 260 元；另外笔记本蓝屏了需要报修。"
        class="w-full rounded border border-slate-200 px-2 py-1.5 text-sm"></textarea>
      <div class="flex items-center gap-2">
        <button @click="create" :disabled="busy || text.trim().length < 6" class="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50" data-testid="orchestration-plan">拆分成步骤</button>
        <label v-if="models.length" class="flex items-center gap-1 text-xs text-slate-500">整理模型
          <select v-model="modelName" class="rounded border border-slate-200 px-1.5 py-1">
            <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
          </select>
        </label>
        <span v-else class="text-xs text-amber-700">先在「设置 → 模型连接」连接聊天模型，才能开始整理</span>
      </div>
    </div>

    <div v-if="plan" class="border-t border-slate-100 px-4 py-3" data-testid="orchestration-steps">
      <p class="mb-2 text-xs text-slate-500">进度：已处理 {{ plan.progress.done }}/{{ plan.progress.total }} 步（已保存或不需要）<span v-if="plan.progress.handoff">，{{ plan.progress.handoff }} 步需要其他部门办理</span></p>
      <ol class="space-y-2">
        <li v-for="s in plan.steps" :key="s.id" class="rounded border px-3 py-2" :class="s.state === 'skipped' ? 'border-slate-100 opacity-60' : 'border-slate-200'" :data-testid="`orchestration-step-${s.seq}`">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="text-sm text-slate-900">{{ s.seq }}. {{ s.kind_label }} <span class="text-xs text-slate-400">· {{ s.department_label }}</span></p>
            <span class="rounded px-2 py-0.5 text-xs" :class="statusClass(s.status)">{{ STATUS_LABELS[s.status] || s.status }}</span>
          </div>
          <p class="mt-1 text-xs text-slate-700">“{{ s.clause }}”</p>
          <p class="mt-0.5 text-xs text-slate-400">{{ s.reason }}</p>
          <p v-if="s.error_message" class="mt-0.5 text-xs text-red-600">{{ s.error_message }}</p>
          <div class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
            <template v-if="s.state === 'pending'">
              <button @click="start(s)" :disabled="busy || !modelName" class="rounded bg-indigo-600 px-2.5 py-1 text-white disabled:opacity-50" :data-testid="`orchestration-start-${s.seq}`">开始整理</button>
              <select :value="s.kind ?? 'hr_case'" @change="change(s, ($event.target as HTMLSelectElement).value as StepKind)" aria-label="改为其他类型" class="rounded border border-slate-200 px-1.5 py-1">
                <option v-for="(l, k) in KIND_LABELS" :key="k" :value="k">{{ l }}</option>
              </select>
              <button @click="skip(s, true)" :disabled="busy" class="rounded border border-slate-200 px-2.5 py-1 text-slate-600">不需要</button>
            </template>
            <template v-else-if="s.state === 'skipped'">
              <button @click="skip(s, false)" :disabled="busy" class="rounded border border-slate-200 px-2.5 py-1 text-slate-600">恢复</button>
            </template>
            <template v-else-if="s.state === 'handoff'">
              <RouterLink v-if="s.handoff_agent && s.kind" :to="`/agents/${s.handoff_agent.id}/chat`" class="rounded border border-sky-200 px-2.5 py-1 text-sky-700 hover:bg-sky-50">交给「{{ s.handoff_agent.name }}」</RouterLink>
              <span v-else-if="!s.kind" class="text-slate-500">请联系人事部门在“人事办理”里发起</span>
              <span v-else class="text-slate-500">{{ s.department_label }}，该部门助手尚未发布，可直接联系该部门</span>
              <select :value="s.kind ?? 'hr_case'" @change="change(s, ($event.target as HTMLSelectElement).value as StepKind)" aria-label="改为其他类型" class="rounded border border-slate-200 px-1.5 py-1">
                <option v-for="(l, k) in KIND_LABELS" :key="k" :value="k">{{ l }}</option>
              </select>
            </template>
            <template v-else>
              <button v-if="s.automation_work_id" @click="emit('open-work', s.automation_work_id)" class="rounded border border-indigo-200 px-2.5 py-1 text-indigo-700 hover:bg-indigo-50" :data-testid="`orchestration-open-${s.seq}`">
                {{ s.status === 'applied' ? '查看已保存的草稿' : '去核对并保存' }}
              </button>
            </template>
          </div>
        </li>
      </ol>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import * as api from '../api/orchestration'
import type { Plan, PlanStep, StepKind } from '../api/orchestration'
import { listConfigs } from '../api/llmConfig'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'

const props = defineProps<{ teamId: number; revision?: number }>()
const emit = defineEmits<{ 'open-work': [id: string] }>()

const KIND_LABELS: Record<StepKind, string> = { leave: '请假', expense: '报销', ticket: 'IT 工单', procurement: '采购申请', crm: '客户跟进', hr_case: '人事事项' }
const STATUS_LABELS: Record<string, string> = {
  pending: '待整理', handoff: '需其他部门', skipped: '不需要', processing: '整理中', ready: '待核对', applying: '保存中',
  applied: '已保存', failed: '整理失败', retry: '待重试',
}
const statusClass = (s: string) => ({
  applied: 'bg-emerald-50 text-emerald-700', ready: 'bg-amber-50 text-amber-700', failed: 'bg-red-50 text-red-700',
  retry: 'bg-red-50 text-red-700', handoff: 'bg-sky-50 text-sky-700', skipped: 'bg-slate-100 text-slate-400',
}[s] || 'bg-slate-100 text-slate-600')

const text = ref('')
const plan = ref<Plan | null>(null)
const plans = ref<Plan[]>([])
const models = ref<string[]>([])
const modelName = ref('')
const busy = ref(false)

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

async function create() {
  await guarded(async () => {
    plan.value = await api.createPlan(props.teamId, text.value.trim())
    plans.value = [plan.value, ...plans.value]
    text.value = ''
    toastSuccess(`已拆成 ${plan.value.steps.length} 步，逐步整理并核对`)
  }, '拆分失败')
}

function pick(id: number) {
  plan.value = plans.value.find((p) => p.id === id) || null
}

async function change(step: PlanStep, kind: StepKind) {
  await guarded(async () => { plan.value = await api.updateStep(plan.value!.id, step.id, { kind }) }, '修改失败')
}

async function skip(step: PlanStep, value: boolean) {
  await guarded(async () => { plan.value = await api.updateStep(plan.value!.id, step.id, { skip: value }) }, '修改失败')
}

async function start(step: PlanStep) {
  await guarded(async () => {
    plan.value = await api.startStep(plan.value!.id, step.id, modelName.value)
    const linked = plan.value.steps.find((s) => s.id === step.id)
    if (linked?.status === 'ready') {
      toastSuccess('已整理好，请在下方核对后保存')
      if (linked.automation_work_id) emit('open-work', linked.automation_work_id)
    }
  }, '整理失败')
}

async function load() {
  try {
    plans.value = await api.listPlans(props.teamId)
    plan.value = plans.value[0] || null
  } catch {
    plans.value = []
  }
}

// 某一步在下方工作成果里保存后，刷新计划进度
watch(() => props.revision, async () => {
  if (plan.value) {
    try { plan.value = await api.getPlan(plan.value.id) } catch { /* 下次打开再刷新 */ }
  }
})
watch(() => props.teamId, load)

onMounted(async () => {
  try {
    const draft = sessionStorage.getItem('orchestration_draft')
    if (draft) {
      text.value = draft
      sessionStorage.removeItem('orchestration_draft')
    }
  } catch { /* 读不到中央助手带过来的原话就让用户自己填 */ }
  await load()
  try {
    models.value = (await listConfigs()).filter((m) => m.is_active && m.kind !== 'embedding').map((m) => m.model_name)
    modelName.value = models.value[0] || ''
  } catch {
    models.value = []
  }
})
</script>
