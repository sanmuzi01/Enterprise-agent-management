<template>
  <section class="rounded-xl border border-blue-200 bg-white p-5 space-y-5" data-testid="automation-panel">
    <div class="flex flex-wrap justify-between gap-3">
      <div>
        <h2 class="font-semibold text-slate-900">让 AI 帮我整理工作</h2>
        <p class="mt-1 text-sm text-slate-500">交给 AI 整理材料和提取待办，核对后保存为真实业务草稿。</p>
      </div>
      <button class="text-sm text-blue-600" :disabled="busy" @click="refresh">刷新成果</button>
    </div>
    <div v-if="stats" class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <div class="rounded-lg bg-slate-50 p-3">处理材料 <strong>{{ stats.total }}</strong> 份</div>
      <div class="rounded-lg bg-blue-50 p-3">待核对 <strong>{{ stats.ready }}</strong> 份</div>
      <div class="rounded-lg bg-emerald-50 p-3">已存业务草稿 <strong>{{ stats.applied }}</strong> 份</div>
      <div class="rounded-lg bg-slate-50 p-3">累计整理耗时 <strong>{{ (stats.elapsed_ms / 1000).toFixed(1) }}</strong> 秒</div>
    </div>
    <p v-if="stats" class="text-xs text-slate-500">统计范围：我在本部门的工作。失败 {{ stats.failed }} 次，修改后保存 {{ stats.edited }} 次。耗时为实际模型整理时间，不等同于节省的人工时间。</p>
    <p v-if="error" role="alert" class="rounded bg-red-50 p-3 text-sm text-red-700">{{ error }}</p>
    <div class="grid gap-3 sm:grid-cols-3">
      <label class="text-sm">工作类型
        <select v-model="kind" :disabled="busy" class="mt-1 w-full rounded border p-2">
          <option v-for="w in availableWorkflows" :key="w.id" :value="w.id">{{ w.title }}</option>
        </select>
      </label>
      <label class="text-sm">整理模型
        <select v-model="modelName" :disabled="busy" class="mt-1 w-full rounded border p-2">
          <option value="" disabled>选择已连接的模型</option>
          <option v-for="m in models" :key="m.id" :value="m.model_name">{{ m.model_name }}</option>
        </select>
      </label>
      <label class="text-sm">材料密级
        <select v-model="sensitivity" :disabled="busy" class="mt-1 w-full rounded border p-2">
          <option value="internal">内部</option><option value="confidential">机密（受信任模型）</option>
          <option value="restricted">受限（禁止模型处理）</option>
        </select>
      </label>
    </div>
    <p v-if="!models.length" class="text-sm text-amber-700">请先在「设置 → 模型连接」配置聊天模型。</p>
    <label v-if="current?.needs_customer" class="block text-sm">保存到哪个客户
      <select v-model="customerId" :disabled="busy" class="mt-1 w-full rounded border p-2">
        <option :value="null" disabled>选择当前部门的真实客户</option>
        <option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option>
      </select>
      <span v-if="customerError" class="text-red-600">{{ customerError }}</span>
    </label>
    <div v-if="current">
      <div class="flex flex-wrap justify-between gap-2 text-sm">
        <label for="automation-source">{{ current.source_label }}</label>
        <label class="cursor-pointer text-blue-600">导入文字文件
          <input class="sr-only" type="file" accept=".txt,.md,.csv" :disabled="busy" @change="importText" />
        </label>
      </div>
      <textarea id="automation-source" v-model="source" :disabled="busy" maxlength="15000" rows="6"
        class="mt-2 w-full rounded-lg border p-3 text-sm" :placeholder="current.example" />
      <p class="mb-2 text-xs text-blue-700">{{ current.hint }}</p>
      <p class="text-xs text-slate-500">支持 TXT / Markdown / CSV，最多 15,000 字。本入口不识别图片；费用仅支持人民币。材料发送给你选择的模型。</p>
      <button class="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40"
        :disabled="busy || !modelName || source.trim().length < 10 || (current.needs_customer && !customerId) || sensitivity === 'restricted'"
        @click="generate">{{ generating ? '正在整理，结果会自动保存…' : '开始整理' }}</button>
    </div>

    <div v-if="selected" class="rounded-xl border border-slate-200 p-4 space-y-3">
      <div class="flex justify-between gap-2">
        <h3 class="font-medium">{{ nameOf(selected.kind) }}成果 · {{ statusName(selected.status) }}</h3>
        <span class="text-xs text-slate-500">{{ (selected.elapsed_ms / 1000).toFixed(1) }} 秒</span>
      </div>
      <p v-if="selected.error_message" class="text-sm text-amber-700">{{ selected.error_message }}</p>
      <p v-if="selected.status === 'processing'" class="text-sm text-slate-500">正在处理，请刷新查看。服务中断后超过三分钟会标记为失败，可从原文重新整理。</p>
      <details class="text-sm"><summary class="cursor-pointer text-slate-600">查看原始材料</summary><pre class="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans">{{ selected.source_text }}</pre></details>
      <template v-if="draft">
        <ul v-if="draft.warnings.length" class="list-inside list-disc rounded bg-amber-50 p-3 text-sm text-amber-800">
          <li v-for="(w, i) in draft.warnings" :key="i">{{ w }}</li>
        </ul>
        <div v-if="selected.business_checks?.length || editable" class="rounded border border-slate-200 p-3 text-sm space-y-2" data-testid="business-checks">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="font-medium">业务系统核对</h4>
            <button v-if="editable" type="button" class="text-xs text-blue-600" :disabled="busy" @click="recheck">重新核对业务数据</button>
          </div>
          <p v-if="checksStale" class="text-xs text-slate-500">内容已修改，以下结论可能已过期，可重新核对。</p>
          <ul v-if="selected.business_checks?.length" class="space-y-1">
            <li v-for="(c, i) in selected.business_checks" :key="i" class="rounded px-2 py-1" :class="checkClass[c.level]">
              <span class="mr-1 font-medium">{{ checkLabel[c.level] }}</span>{{ c.text }}
            </li>
          </ul>
          <p v-else class="text-xs text-slate-500">暂无核对结论。</p>
        </div>
        <fieldset v-if="selectedWorkflow" :disabled="!editable || busy">
          <WorkflowForm :fields="selectedWorkflow.form" :model="draft" />
        </fieldset>
        <div v-if="['ready', 'retry', 'applying'].includes(selected.status)" class="space-y-2">
          <p v-if="validationMessage" role="status" class="text-sm text-amber-700">{{ validationMessage }}</p>
          <label class="flex items-start gap-2 text-sm"><input v-model="reviewed" type="checkbox" :disabled="busy" class="mt-1" />我已核对原文、金额／跟进内容与疑点，确认保存为业务草稿。</label>
          <button :disabled="busy || !reviewed || !!validationMessage" class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40" @click="apply">
            {{ saving ? '正在保存…' : selected.status === 'ready' ? '确认保存业务草稿' : '按原内容重试保存' }}
          </button>
          <p class="text-xs text-slate-500">保存后仍为草稿；正式提交、审批在对应业务模块办理。首次保存失败后内容锁定，重试沿用原操作编号。</p>
        </div>
        <div v-if="selected.status === 'applied'" class="rounded bg-emerald-50 p-3 text-sm text-emerald-800">
          已保存{{ selectedWorkflow?.draft_name }}草稿 #{{ selected.business_result?.id }}。可在下方业务模块查看和继续办理。
        </div>
        <div v-if="selected.status === 'applied' && followupItems.length" class="space-y-2">
          <h4 class="text-sm font-medium">我的后续待办（保存在本工作成果中）</h4>
          <label v-for="(task, i) in followupItems" :key="i" class="flex items-center gap-2 text-sm">
            <input type="checkbox" :checked="selected.completed_tasks.includes(i)" :disabled="busy" @change="toggleTask(i, ($event.target as HTMLInputElement).checked)" />
            <span :class="selected.completed_tasks.includes(i) ? 'line-through text-slate-400' : ''">{{ task.title }}{{ task.due ? ` · ${task.due}` : '' }}</span>
          </label>
        </div>
      </template>
      <button v-if="selected.status === 'failed' || (selected.status === 'ready' && selected.error_message)" class="text-sm text-blue-600" @click="reuse">将原文带回重新整理</button>
    </div>

    <div class="space-y-2">
      <h3 class="text-sm font-semibold">我的工作成果</h3>
      <p v-if="!items.length" class="text-sm text-slate-400">还没有成果。提交一份材料，处理记录会保留在这里。</p>
      <button v-for="work in items" :key="work.id" :disabled="busy" @click="open(work.id)"
        class="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-left text-sm hover:bg-slate-50">
        <span>{{ nameOf(work.kind) }} · {{ new Date(work.created_at).toLocaleString() }}</span>
        <span>{{ statusName(work.status) }}<span v-if="followupCount(work)"> · 待办 {{ work.completed_tasks.length }}/{{ followupCount(work) }}</span></span>
      </button>
      <div class="flex gap-4 text-sm text-blue-600">
        <button v-if="offset > 0" :disabled="busy" @click="page(-20)">上一页</button>
        <button v-if="stats && offset + items.length < stats.total" :disabled="busy" @click="page(20)">下一页</button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import * as api from '../api/automationWork'
import { listConfigs, type LlmConfig } from '../api/llmConfig'
import { getTeamCustomers, type CustomerDto } from '../api/departmentCrm'
import { getErrorMessage } from '../utils/request'
import WorkflowForm from './WorkflowForm.vue'

const props = defineProps<{ teamId: number }>()
const emit = defineEmits<{ saved: [] }>()
// 工作类型、表单、校验规则都来自后端工作流注册表（GET /enterprise/automation/workflows）。
const catalog = ref<api.WorkflowInfo[]>([])
const workflowById = computed(() => Object.fromEntries(catalog.value.map(w => [w.id, w])) as Record<string, api.WorkflowInfo>)
const availableWorkflows = computed(() => catalog.value.filter(w => w.available))
const kind = ref<api.WorkKind>('')
const current = computed(() => workflowById.value[kind.value])
const nameOf = (k: string) => workflowById.value[k]?.name || k
const models = ref<LlmConfig[]>([]), customers = ref<CustomerDto[]>([])
const modelName = ref(''), source = ref(''), sensitivity = ref('internal')
const customerId = ref<number | null>(null), customerError = ref(''), error = ref('')
const generating = ref(false), saving = ref(false), loading = ref(false), reviewed = ref(false)
const busy = computed(() => generating.value || saving.value || loading.value)
const selected = ref<api.Work | null>(null), draft = ref<api.Proposal | null>(null)
const selectedWorkflow = computed(() => (selected.value ? workflowById.value[selected.value.kind] : undefined))
const items = ref<api.Work[]>([]), stats = ref<api.WorkStats | null>(null), offset = ref(0)
const editable = computed(() => selected.value?.status === 'ready')
const validationMessage = computed(() => {
  const wf = selectedWorkflow.value
  return wf && draft.value ? api.validateDraft(wf.form, wf.rules, draft.value) : ''
})
const followupItems = computed(() => {
  const fu = selectedWorkflow.value?.followups
  if (!fu || !draft.value) return []
  return ((draft.value[fu.key] || []) as Record<string, any>[]).map(t => ({ title: t[fu.title], due: t[fu.due] }))
})
const followupCount = (work: api.Work) => {
  const fu = workflowById.value[work.kind]?.followups
  return fu ? (work.proposal?.[fu.key]?.length || 0) : 0
}
watch(draft, () => { reviewed.value = false }, { deep: true, flush: 'sync' })
const checkLabel = { blocker: '会被拒绝', warning: '需确认', info: '参考' }
const checkClass = { blocker: 'bg-red-50 text-red-700', warning: 'bg-amber-50 text-amber-800', info: 'bg-slate-50 text-slate-600' }
// 核对结论对应的内容快照；员工改动后提示结论可能过期。
const checkedSnapshot = ref('')
const checksStale = computed(() => !!draft.value && !!checkedSnapshot.value && JSON.stringify(draft.value) !== checkedSnapshot.value)
async function recheck() {
  const wf = selectedWorkflow.value
  if (!selected.value || !draft.value || !wf) return
  loading.value = true; error.value = ''
  const snapshot = JSON.stringify(draft.value)
  try {
    // 只更新核对结论，不能用服务端的原始整理结果覆盖员工正在修改的内容。
    const latest = await api.recheckWork(selected.value.id, api.normalizeDraft(wf.form, draft.value))
    selected.value = { ...selected.value, business_checks: latest.business_checks }
    checkedSnapshot.value = snapshot
  }
  catch (e) { error.value = getErrorMessage(e, '核对失败，请检查内容后重试') }
  finally { loading.value = false }
}
const statusName = (s: string) => ({ processing: '整理中', ready: '待核对', failed: '整理失败', applying: '保存中', retry: '保存待重试', applied: '已保存草稿' }[s] || s)
function select(work: api.Work) {
  selected.value = work; draft.value = work.proposal ? JSON.parse(JSON.stringify(work.proposal)) : null; reviewed.value = false
  checkedSnapshot.value = draft.value ? JSON.stringify(draft.value) : ''
}
async function refresh() {
  try { const data = await api.listWork(props.teamId, offset.value); items.value = data.items; stats.value = data.stats
    if (selected.value && ['processing', 'applying'].includes(selected.value.status)) select(await api.getWork(selected.value.id))
  } catch (e) { error.value = getErrorMessage(e, '加载工作成果失败') }
}
async function open(id: string) { loading.value = true; error.value = ''; try { select(await api.getWork(id)) } catch (e) { error.value = getErrorMessage(e) } finally { loading.value = false } }
async function page(delta: number) { offset.value += delta; await refresh() }
// Reuse the request key after a network timeout; changing input starts a different operation.
let lastInput = '', requestKey = ''
async function generate() {
  generating.value = true; error.value = ''
  const input = { team_id: props.teamId, kind: kind.value, model_name: modelName.value, source_text: source.value.trim(), customer_id: current.value?.needs_customer ? customerId.value : null, sensitivity: sensitivity.value }
  const fingerprint = JSON.stringify(input)
  if (fingerprint !== lastInput) { requestKey = crypto.randomUUID(); lastInput = fingerprint }
  try { select(await api.generateWork({ ...input, request_key: requestKey })); offset.value = 0; await refresh() }
  catch (e) { error.value = getErrorMessage(e, '整理失败；刷新成果可查看已提交任务的状态') }
  finally { generating.value = false }
}
async function apply() {
  const wf = selectedWorkflow.value
  if (!selected.value || !draft.value || !wf || !reviewed.value || validationMessage.value) return
  saving.value = true; error.value = ''
  try {
    select(await api.applyWork(selected.value.id, api.normalizeDraft(wf.form, draft.value)))
    if (selected.value.status === 'applied') emit('saved')
    await refresh()
  } catch (e) {
    error.value = getErrorMessage(e, '保存失败，请刷新成果确认状态后重试')
    try { const latest = await api.getWork(selected.value.id); if (latest.status !== 'ready') select(latest) } catch { /* Keep the original error when status cannot be fetched. */ }
  }
  finally { saving.value = false }
}
async function toggleTask(index: number, done: boolean) { if (!selected.value) return; saving.value = true; try { select(await api.setTask(selected.value.id, index, done)); await refresh() } catch (e) { error.value = getErrorMessage(e) } finally { saving.value = false } }
function reuse() { if (!selected.value) return; source.value = selected.value.source_text || ''; kind.value = selected.value.kind; customerId.value = selected.value.customer_id; modelName.value = selected.value.model_name; sensitivity.value = selected.value.sensitivity || 'internal'; lastInput = ''; selected.value = null; draft.value = null }
async function importText(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file) return
  try { if (!/\.(txt|md|csv)$/i.test(file.name) || file.size > 100000) throw new Error('请选择小于100KB的文字文件')
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
    if (text.length > 15000) throw new Error('文件超过15,000字，请拆分后导入')
    source.value = text; error.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : '文件读取失败，请使用 UTF-8 文字文件' } finally { input.value = '' }
}
// 协同办理面板开始整理某一步后，直接在这里打开对应的工作成果核对
defineExpose({ open, refresh })
onMounted(async () => {
  try { const data = await api.getWorkflows(props.teamId); catalog.value = data.workflows; kind.value = data.default_id || '' }
  catch (e) { error.value = getErrorMessage(e, '加载工作类型失败') }
  await refresh()
  try { models.value = (await listConfigs()).filter(m => m.is_active && m.kind !== 'embedding'); modelName.value = models.value[0]?.model_name || '' } catch (e) { error.value = getErrorMessage(e, '加载模型失败') }
  if (availableWorkflows.value.some(w => w.needs_customer)) {
    try { customers.value = await getTeamCustomers(props.teamId) } catch (e) { customerError.value = getErrorMessage(e, '客户列表不可用，请检查企业业务服务') }
  }
})
</script>
