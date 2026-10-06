<template>
  <section class="rounded-lg border border-indigo-200 bg-white" data-testid="resp-task-detail">
    <div class="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
      <div class="min-w-0">
        <p class="text-xs text-slate-400">{{ detail?.task.planTitle }} · 第 {{ detail?.task.seq }} 项</p>
        <h3 class="mt-0.5 text-sm font-semibold text-slate-900">{{ detail?.task.title }}</h3>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <span v-if="detail" class="rounded-full px-2 py-0.5 text-xs" :class="statusClass(detail.task.status)" data-testid="resp-task-status">{{ detail.task.statusLabel }}</span>
        <button class="text-xs text-slate-400 hover:text-slate-700" @click="$emit('close')">关闭</button>
      </div>
    </div>

    <p v-if="error" class="mx-4 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{{ error }}</p>
    <div v-if="loading && !detail" class="px-4 py-8 text-center text-sm text-slate-400">加载中…</div>

    <div v-if="detail" class="space-y-4 px-4 py-4 text-sm">
      <dl class="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <div><dt class="text-xs text-slate-400">主责员工</dt><dd data-testid="resp-responsible">{{ t.responsibleName || '—' }}</dd></div>
        <div><dt class="text-xs text-slate-400">验收人</dt><dd>{{ t.reviewerName || '—' }}</dd></div>
        <div><dt class="text-xs text-slate-400">协办</dt><dd>{{ t.collaboratorNames.join('、') || '—' }}</dd></div>
        <div><dt class="text-xs text-slate-400">指派人</dt><dd>{{ t.assignedByName || '尚未正式指派' }}</dd></div>
        <div><dt class="text-xs text-slate-400">截止日期</dt>
          <dd :class="t.overdue ? 'text-red-600' : ''">{{ t.dueDate || '—' }}<span v-if="t.overdue">（已逾期）</span>
            <span v-if="t.pendingDueDate" class="ml-1 text-amber-600">申请延期到 {{ t.pendingDueDate }}</span></dd></div>
        <div><dt class="text-xs text-slate-400">优先级</dt><dd>{{ t.priorityLabel }}</dd></div>
        <div class="sm:col-span-2"><dt class="text-xs text-slate-400">交付物</dt><dd>{{ t.deliverable || '—' }}</dd></div>
        <div class="sm:col-span-2"><dt class="text-xs text-slate-400">验收标准</dt><dd data-testid="resp-criteria">{{ t.acceptanceCriteria || '—' }}</dd></div>
      </dl>

      <blockquote v-if="t.sourceEvidence" class="rounded border-l-2 border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <span class="mr-1 text-slate-400">原文依据：</span>{{ t.sourceEvidence }}
      </blockquote>
      <details v-if="detail.sourceText" class="text-xs text-slate-500">
        <summary class="cursor-pointer">查看完整原文</summary>
        <pre class="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans">{{ detail.sourceText }}</pre>
      </details>

      <ul v-if="t.issues.length" class="space-y-1 text-xs" data-testid="resp-issues">
        <li v-for="i in t.issues" :key="i.code" class="rounded px-2 py-1" :class="i.level === 'BLOCK' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800'">
          <span class="mr-1 font-medium">{{ i.level === 'BLOCK' ? '发布前必须补充' : '提示' }}</span>{{ i.message }}
        </li>
      </ul>

      <div v-if="t.status === 'BLOCKED'" class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="resp-blocked">
        受阻：{{ t.blockedReason }}<span v-if="t.waitingOnName">（等待 {{ t.waitingOnName }}）</span>
      </div>
      <div v-if="t.status === 'NEGOTIATING'" class="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800" data-testid="resp-objection">
        员工提出异议：{{ t.objectionNote }}
      </div>
      <p v-if="t.transferNote" class="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">申请转交：{{ t.transferNote }}</p>
      <p v-if="t.extensionReason" class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">延期原因：{{ t.extensionReason }}</p>
      <p v-if="t.lastProgress" class="text-xs text-slate-500">最近进度：{{ t.lastProgress }}<span v-if="t.progressPercent !== null">（{{ t.progressPercent }}%）</span></p>

      <!-- 此刻你能做的动作：由业务系统按你的身份给出，后端每个动作仍会再校验 -->
      <div v-if="actions.length" class="space-y-2 border-t border-slate-100 pt-3" data-testid="resp-actions">
        <div class="flex flex-wrap gap-2">
          <button v-for="a in actions" :key="a" @click="startAction(a)" :data-testid="`resp-action-${a}`"
            class="rounded border px-2.5 py-1 text-xs" :class="active === a ? 'border-indigo-600 bg-indigo-600 text-white' : buttonTone(a)">{{ FORMS[a].label }}</button>
        </div>
        <form v-if="active" class="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs" @submit.prevent="submit" :data-testid="`resp-form-${active}`">
          <p v-if="FORMS[active].hint" class="text-slate-500">{{ FORMS[active].hint }}</p>
          <label v-for="f in FORMS[active].fields" :key="f.key" class="block space-y-1">
            <span class="text-slate-500">{{ f.label }}<span v-if="f.required" class="text-red-500"> *</span></span>
            <textarea v-if="f.kind === 'textarea'" v-model="values[f.key]" rows="2" maxlength="500" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`" />
            <input v-else-if="f.kind === 'date'" v-model="values[f.key]" type="date" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`" />
            <input v-else-if="f.kind === 'number'" v-model.number="values[f.key]" type="number" min="0" max="100" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`" />
            <select v-else-if="f.kind === 'member'" v-model="values[f.key]" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`">
              <option :value="''">{{ f.placeholder || '不变' }}</option>
              <option v-for="m in candidates.members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option>
            </select>
            <select v-else-if="f.kind === 'reviewer'" v-model="values[f.key]" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`">
              <option :value="''">{{ f.placeholder || '不变' }}</option>
              <option v-for="m in candidates.reviewers" :key="m.user_id" :value="m.user_id">{{ m.name }}</option>
            </select>
            <select v-else-if="f.kind === 'priority'" v-model="values[f.key]" class="w-full rounded border border-slate-200 px-2 py-1.5">
              <option :value="''">不变</option>
              <option v-for="(l, k) in PRIORITY" :key="k" :value="k">{{ l }}</option>
            </select>
            <select v-else-if="f.kind === 'choice'" v-model="values[f.key]" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`">
              <option v-for="o in f.options" :key="String(o.value)" :value="o.value">{{ o.label }}</option>
            </select>
            <input v-else v-model="values[f.key]" maxlength="500" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-field-${f.key}`" />
          </label>
          <div class="flex items-center gap-2">
            <button type="submit" :disabled="busy || !formReady" class="rounded px-3 py-1.5 text-white disabled:opacity-50"
              :class="active === 'cancel' ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'" data-testid="resp-action-submit">{{ busy ? '处理中…' : FORMS[active].confirm }}</button>
            <button type="button" class="text-slate-500 hover:text-slate-800" @click="active = ''">取消</button>
          </div>
        </form>
      </div>

      <div v-if="detail.deliverables.length" class="space-y-1" data-testid="resp-deliverables">
        <h4 class="text-xs font-medium text-slate-500">提交的成果</h4>
        <ul class="space-y-1 text-xs text-slate-600">
          <li v-for="d in detail.deliverables" :key="d.id" class="rounded bg-slate-50 px-2 py-1.5">
            第 {{ d.submissionNo }} 次 · {{ d.submittedByName }} · {{ time(d.submittedAt) }}<br />{{ d.summary }}
            <a v-if="d.link" :href="d.link" target="_blank" rel="noopener noreferrer" class="ml-1 text-indigo-600 hover:underline">查看成果</a>
          </li>
        </ul>
      </div>

      <div class="space-y-1" data-testid="resp-timeline">
        <h4 class="text-xs font-medium text-slate-500">履责记录（只增不改）</h4>
        <ol class="space-y-1.5 border-l border-slate-200 pl-3 text-xs">
          <li v-for="e in detail.events" :key="e.id" class="relative">
            <span class="absolute -left-[17px] top-1.5 h-1.5 w-1.5 rounded-full bg-slate-300" />
            <span class="font-medium text-slate-700">{{ e.typeLabel }}</span>
            <span class="text-slate-400"> · {{ e.actorName }} · {{ time(e.createdAt) }}</span>
            <p v-if="e.note" class="text-slate-600">{{ e.note }}</p>
            <ul v-if="e.detailData?.changes" class="text-slate-500">
              <li v-for="c in e.detailData.changes" :key="c.field">{{ c.fieldLabel }}：{{ c.fromText || '空' }} → {{ c.toText || '空' }}</li>
            </ul>
          </li>
        </ol>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import * as api from '../api/responsibility'
import type { Candidates, RespTaskDetail } from '../api/responsibility'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ teamId: number; taskId: number; candidates: Candidates }>()
const emit = defineEmits<{ close: []; changed: [] }>()

type Kind = 'text' | 'textarea' | 'date' | 'number' | 'member' | 'reviewer' | 'priority' | 'choice'
interface Field { key: string; label: string; kind: Kind; required?: boolean; placeholder?: string; options?: { value: string | boolean; label: string }[] }
const PRIORITY: Record<string, string> = { LOW: '低', NORMAL: '普通', HIGH: '高', URGENT: '紧急' }
const APPROVE = [{ value: true, label: '同意' }, { value: false, label: '不同意' }]
const FORMS: Record<string, { label: string; confirm: string; hint?: string; fields: Field[] }> = {
  accept: { label: '接受责任', confirm: '确认接受', hint: '接受后你就是这项责任的主责人，需要在截止日期前提交交付物；接受不能由别人代替。', fields: [] },
  object: { label: '提出异议', confirm: '提交异议', fields: [{ key: 'reason', label: '哪里不合适（责任人、期限或验收标准）', kind: 'textarea', required: true }] },
  progress: { label: '报告进度', confirm: '提交进度', fields: [{ key: 'note', label: '当前进度', kind: 'textarea', required: true }, { key: 'percent', label: '完成百分比（可选）', kind: 'number' }] },
  block: { label: '报告受阻', confirm: '提交受阻', fields: [{ key: 'reason', label: '受阻原因（缺少资料、等待他人、资源不足…）', kind: 'textarea', required: true }, { key: 'waiting_on_user_id', label: '在等谁（可选）', kind: 'reviewer', placeholder: '不指定' }] },
  unblock: { label: '解除受阻', confirm: '确认解除', fields: [{ key: 'note', label: '说明（可选）', kind: 'textarea' }] },
  submit: { label: '提交成果', confirm: '提交验收', hint: '提交后由验收人对照验收标准验收；验收人可以退回。', fields: [{ key: 'summary', label: '成果说明（做了什么、成果在哪里）', kind: 'textarea', required: true }, { key: 'link', label: '成果链接（可选，http/https）', kind: 'text' }] },
  verify: { label: '验收通过', confirm: '确认验收通过', hint: '验收通过后这项责任完成，不能再修改。', fields: [{ key: 'note', label: '验收说明（可选）', kind: 'textarea' }] },
  rework: { label: '退回修改', confirm: '退回', fields: [{ key: 'reason', label: '哪里没有达到验收标准', kind: 'textarea', required: true }] },
  'request-extension': { label: '申请延期', confirm: '提交申请', fields: [{ key: 'proposed_date', label: '希望延期到', kind: 'date', required: true }, { key: 'reason', label: '延期原因', kind: 'textarea', required: true }] },
  'decide-extension': { label: '处理延期申请', confirm: '提交决定', fields: [{ key: 'approve', label: '决定', kind: 'choice', required: true, options: APPROVE }, { key: 'note', label: '说明（不同意时必填）', kind: 'textarea' }] },
  'request-transfer': { label: '申请转交', confirm: '提交申请', fields: [{ key: 'reason', label: '转交原因和建议的接手人', kind: 'textarea', required: true }] },
  'decide-transfer': { label: '处理转交申请', confirm: '提交决定', fields: [{ key: 'approve', label: '决定', kind: 'choice', required: true, options: APPROVE }, { key: 'responsible_user_id', label: '新的主责员工（同意时必选）', kind: 'member', placeholder: '请选择' }, { key: 'note', label: '说明（不同意时必填）', kind: 'textarea' }] },
  revise: { label: '变更责任', confirm: '确认变更', hint: '变更会留下记录；换了主责人需要新主责人重新接受。', fields: [
    { key: 'responsible_user_id', label: '主责员工', kind: 'member' }, { key: 'reviewer_user_id', label: '验收人', kind: 'reviewer' },
    { key: 'due_date', label: '截止日期', kind: 'date' }, { key: 'acceptance_criteria', label: '验收标准', kind: 'text' },
    { key: 'deliverable', label: '交付物', kind: 'text' }, { key: 'priority', label: '优先级', kind: 'priority' },
    { key: 'reason', label: '变更原因', kind: 'textarea', required: true }] },
  cancel: { label: '取消责任', confirm: '确认取消', fields: [{ key: 'reason', label: '取消原因', kind: 'textarea', required: true }] },
}

const detail = ref<RespTaskDetail | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const active = ref('')
const values = reactive<Record<string, any>>({})

const t = computed(() => detail.value!.task)
const actions = computed(() => (detail.value?.task.myActions || []).filter((a) => a in FORMS))
const formReady = computed(() => {
  if (!active.value) return false
  const form = FORMS[active.value]
  if (form.fields.some((f) => f.required && (values[f.key] === '' || values[f.key] === undefined || values[f.key] === null))) return false
  if (active.value === 'decide-transfer' && values.approve === true && !values.responsible_user_id) return false
  if (active.value.startsWith('decide-') && values.approve === false && !String(values.note || '').trim()) return false
  if (active.value === 'revise') return form.fields.some((f) => f.key !== 'reason' && values[f.key] !== '' && values[f.key] !== undefined)
  return true
})

const STATUS_CLASS: Record<string, string> = {
  DRAFT: 'bg-slate-100 text-slate-600', PENDING_ACCEPT: 'bg-amber-100 text-amber-800', NEGOTIATING: 'bg-sky-100 text-sky-800',
  IN_PROGRESS: 'bg-indigo-100 text-indigo-800', BLOCKED: 'bg-red-100 text-red-700', PENDING_REVIEW: 'bg-violet-100 text-violet-800',
  DONE: 'bg-emerald-100 text-emerald-800', CANCELLED: 'bg-slate-100 text-slate-400',
}
const statusClass = (s: string) => STATUS_CLASS[s] || 'bg-slate-100 text-slate-600'
const buttonTone = (a: string) => (a === 'cancel' ? 'border-red-200 text-red-600 hover:bg-red-50' : 'border-slate-200 text-slate-700 hover:bg-white')
const time = (iso: string) => new Date(iso).toLocaleString()

function startAction(action: string) {
  active.value = active.value === action ? '' : action
  for (const key of Object.keys(values)) delete values[key]
  for (const f of FORMS[action]?.fields || []) values[f.key] = f.kind === 'choice' ? (f.options?.[0].value ?? '') : ''
  error.value = ''
}

async function load() {
  loading.value = true
  try {
    detail.value = await api.getTask(props.teamId, props.taskId)
  } catch (e) {
    error.value = getErrorMessage(e, '加载责任详情失败')
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!active.value || !formReady.value) return
  busy.value = true
  error.value = ''
  const body: Record<string, unknown> = {}
  for (const f of FORMS[active.value].fields) {
    const v = values[f.key]
    if (v !== '' && v !== undefined && v !== null) body[f.key] = v
  }
  try {
    detail.value = await api.actOnTask(props.teamId, props.taskId, active.value, body)
    active.value = ''
    emit('changed')
  } catch (e) {
    error.value = getErrorMessage(e, '操作失败，请刷新后重试')
    await load()
  } finally {
    busy.value = false
  }
}

watch(() => props.taskId, () => { active.value = ''; detail.value = null; void load() }, { immediate: true })
</script>
