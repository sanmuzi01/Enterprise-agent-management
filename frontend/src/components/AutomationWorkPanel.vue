<template>
  <section class="rounded-xl border border-blue-200 bg-white p-5 space-y-5">
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
          <option value="expense">费用材料 → 报销草稿</option>
          <option value="leave">请假描述 → OA 请假草稿</option>
          <option v-if="departmentCode === 'procurement'" value="procurement">采购需求 → 采购申请草稿</option>
          <option v-if="departmentCode === 'sales'" value="crm">沟通记录 → CRM 跟进与待办</option>
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
    <label v-if="kind === 'crm'" class="block text-sm">保存到哪个客户
      <select v-model="customerId" :disabled="busy" class="mt-1 w-full rounded border p-2">
        <option :value="null" disabled>选择当前部门的真实客户</option>
        <option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option>
      </select>
      <span v-if="customerError" class="text-red-600">{{ customerError }}</span>
    </label>
    <div>
      <div class="flex flex-wrap justify-between gap-2 text-sm">
        <label for="automation-source">{{ workflow[kind].sourceLabel }}</label>
        <label class="cursor-pointer text-blue-600">导入文字文件
          <input class="sr-only" type="file" accept=".txt,.md,.csv" :disabled="busy" @change="importText" />
        </label>
      </div>
      <textarea id="automation-source" v-model="source" :disabled="busy" maxlength="15000" rows="6"
        class="mt-2 w-full rounded-lg border p-3 text-sm" :placeholder="workflow[kind].example" />
      <p class="mb-2 text-xs text-blue-700">{{ workflow[kind].hint }}</p>
      <p class="text-xs text-slate-500">支持 TXT / Markdown / CSV，最多 15,000 字。本入口不识别图片；费用仅支持人民币。材料发送给你选择的模型。</p>
      <button class="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40"
        :disabled="busy || !modelName || source.trim().length < 10 || (kind === 'crm' && !customerId) || sensitivity === 'restricted'"
        @click="generate">{{ generating ? '正在整理，结果会自动保存…' : '开始整理' }}</button>
    </div>

    <div v-if="selected" class="rounded-xl border border-slate-200 p-4 space-y-3">
      <div class="flex justify-between gap-2">
        <h3 class="font-medium">{{ workflow[selected.kind].name }}成果 · {{ statusName(selected.status) }}</h3>
        <span class="text-xs text-slate-500">{{ (selected.elapsed_ms / 1000).toFixed(1) }} 秒</span>
      </div>
      <p v-if="selected.error_message" class="text-sm text-amber-700">{{ selected.error_message }}</p>
      <p v-if="selected.status === 'processing'" class="text-sm text-slate-500">正在处理，请刷新查看。服务中断后超过三分钟会标记为失败，可从原文重新整理。</p>
      <details class="text-sm"><summary class="cursor-pointer text-slate-600">查看原始材料</summary><pre class="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans">{{ selected.source_text }}</pre></details>
      <template v-if="draft">
        <ul v-if="draft.warnings.length" class="list-inside list-disc rounded bg-amber-50 p-3 text-sm text-amber-800">
          <li v-for="(w, i) in draft.warnings" :key="i">{{ w }}</li>
        </ul>
        <fieldset :disabled="!editable || busy" class="space-y-3">
          <template v-if="selected.kind === 'crm'">
            <label class="block text-sm">跟进内容<textarea v-model="draft.content" rows="4" maxlength="1000" class="mt-1 w-full rounded border p-2" /></label>
            <p class="text-xs text-slate-500">原文依据：{{ draft.evidence }}</p>
            <div v-for="(task, i) in draft.tasks" :key="i" class="rounded bg-slate-50 p-3 space-y-2">
              <label class="block text-sm">后续待办<input v-model="task.title" maxlength="200" class="mt-1 w-full rounded border p-2" /></label>
              <label class="block text-sm">截止日期<input v-model="task.due_date" type="date" class="ml-2 rounded border p-1" /></label>
              <p class="text-xs text-slate-500">依据：{{ task.evidence }}</p>
              <button type="button" class="text-xs text-red-600" @click="draft?.tasks?.splice(i, 1)">删除这条待办</button>
            </div>
          </template>
          <template v-else-if="selected.kind === 'procurement'">
            <div v-for="(item, i) in draft.items" :key="i" class="rounded bg-slate-50 p-3 space-y-2">
              <div class="grid gap-2 sm:grid-cols-2">
                <label class="text-sm">产品 SKU<input v-model="item.sku" maxlength="40" placeholder="填写业务系统中的真实 SKU" class="mt-1 w-full rounded border p-2" /></label>
                <label class="text-sm">采购数量<input v-model.number="item.quantity" type="number" min="1" max="1000000" step="1" class="mt-1 w-full rounded border p-2" /></label>
              </div>
              <p class="text-xs text-slate-500">原文依据：{{ item.evidence }}</p>
              <button type="button" class="text-xs text-red-600" @click="draft?.items?.splice(i, 1)">删除这条采购需求</button>
            </div>
            <p class="text-xs text-slate-500">采购价格由业务系统的产品目录计算；预算和审批以业务系统为准。</p>
          </template>
          <template v-else-if="selected.kind === 'leave'">
            <div class="grid gap-2 sm:grid-cols-3">
              <label class="text-sm">假期类型<select v-model="draft.leave_type_code" class="mt-1 w-full rounded border p-2"><option :value="null" disabled>请补全假期类型</option><option value="annual">年假</option><option value="sick">病假</option><option value="personal">事假</option></select></label>
              <label class="text-sm">开始日期<input v-model="draft.start_date" type="date" class="mt-1 w-full rounded border p-2" /></label>
              <label class="text-sm">结束日期<input v-model="draft.end_date" type="date" class="mt-1 w-full rounded border p-2" /></label>
            </div>
            <label class="block text-sm">请假原因<textarea v-model="draft.reason" maxlength="500" rows="3" class="mt-1 w-full rounded border p-2" /></label>
            <p class="text-xs text-slate-500">原文依据：{{ draft.evidence }}。请假天数与余额以 OA 系统为准。</p>
          </template>
          <template v-else>
            <div v-for="(line, i) in draft.lines" :key="i" class="rounded bg-slate-50 p-3 space-y-2">
              <div class="grid gap-2 sm:grid-cols-3">
                <label class="text-sm">类别<select v-model="line.category" class="mt-1 w-full rounded border p-2"><option v-for="(label, code) in categories" :key="code" :value="code">{{ label }}</option></select></label>
                <label class="text-sm">金额（元）<input v-model="line.amount" type="number" min="0.01" step="0.01" class="mt-1 w-full rounded border p-2" /></label>
                <label class="text-sm">发票号<input v-model="line.invoice_no" maxlength="80" class="mt-1 w-full rounded border p-2" /></label>
              </div>
              <label class="block text-sm">说明<input v-model="line.description" maxlength="300" class="mt-1 w-full rounded border p-2" /></label>
              <p class="text-xs text-slate-500">依据：{{ line.evidence }}</p>
              <button type="button" class="text-xs text-red-600" @click="draft?.lines?.splice(i, 1)">删除这条费用</button>
            </div>
            <p class="text-sm font-medium">合计 ¥{{ totalAmount }}</p>
          </template>
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
          已保存{{ workflow[selected.kind].draftName }}草稿 #{{ selected.business_result?.id }}。可在下方业务模块查看和继续办理。
        </div>
        <div v-if="selected.status === 'applied' && draft.tasks?.length" class="space-y-2">
          <h4 class="text-sm font-medium">我的后续待办（保存在本工作成果中）</h4>
          <label v-for="(task, i) in draft.tasks" :key="i" class="flex items-center gap-2 text-sm">
            <input type="checkbox" :checked="selected.completed_tasks.includes(i)" :disabled="busy" @change="toggleTask(i, ($event.target as HTMLInputElement).checked)" />
            <span :class="selected.completed_tasks.includes(i) ? 'line-through text-slate-400' : ''">{{ task.title }}{{ task.due_date ? ` · ${task.due_date}` : '' }}</span>
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
        <span>{{ workflow[work.kind].name }} · {{ new Date(work.created_at).toLocaleString() }}</span>
        <span>{{ statusName(work.status) }}<span v-if="work.proposal?.tasks?.length"> · 待办 {{ work.completed_tasks.length }}/{{ work.proposal.tasks.length }}</span></span>
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

const props = defineProps<{ teamId: number; departmentCode?: string | null }>()
const emit = defineEmits<{ saved: [] }>()
const kind = ref<api.WorkKind>(props.departmentCode === 'sales' ? 'crm' : props.departmentCode === 'procurement' ? 'procurement' : props.departmentCode === 'hr' ? 'leave' : 'expense')
const workflow = {
  crm: { name: '沟通记录整理', draftName: '跟进', sourceLabel: '粘贴会议纪要、沟通记录', example: '客户希望试用，约定2026年10月8日发送方案；预算尚未确定。', hint: '提取跟进内容与后续待办，保存到你选择的客户。' },
  expense: { name: '费用材料整理', draftName: '报销', sourceLabel: '粘贴费用明细、票据文字或导出的流水', example: '10月1日出差高铁票260元，发票号G001；出租车48元，暂无发票。', hint: '提取费用分类、金额与发票号，核对后生成报销草稿。' },
  procurement: { name: '采购需求整理', draftName: '采购', sourceLabel: '粘贴采购需求、补货清单', example: '办公室需要采购产品 SKU PAPER-A4 共10件，用于打印合同。', hint: '提取产品 SKU 与数量；缺失信息会留空，由你补全后保存。' },
  leave: { name: '请假申请整理', draftName: '请假', sourceLabel: '描述假期类型、起止日期与原因', example: '我要申请年假，2026年10月12日至2026年10月14日，原因是家庭事务。', hint: '提取假期类型与日期；未明确的年份、日期不会自行推算。' },
}
const models = ref<LlmConfig[]>([]), customers = ref<CustomerDto[]>([])
const modelName = ref(''), source = ref(''), sensitivity = ref('internal')
const customerId = ref<number | null>(null), customerError = ref(''), error = ref('')
const generating = ref(false), saving = ref(false), loading = ref(false), reviewed = ref(false)
const busy = computed(() => generating.value || saving.value || loading.value)
const selected = ref<api.Work | null>(null), draft = ref<api.Proposal | null>(null)
const items = ref<api.Work[]>([]), stats = ref<api.WorkStats | null>(null), offset = ref(0)
const editable = computed(() => selected.value?.status === 'ready')
const categories = { TRAVEL: '差旅', MEAL: '餐饮', OFFICE_SUPPLY: '办公用品', TRANSPORT: '交通', OTHER: '其他' }
const totalAmount = computed(() => (draft.value?.lines || []).reduce((sum, l) => sum + Number(l.amount || 0), 0).toFixed(2))
const validationMessage = computed(() => {
  const d = draft.value
  if (!d) return ''
  if (selected.value?.kind === 'leave') {
    if (!d.leave_type_code || !d.start_date || !d.end_date) return '请补全假期类型、开始日期和结束日期。'
    if (d.start_date > d.end_date) return '结束日期不能早于开始日期。'
  }
  if (selected.value?.kind === 'procurement') {
    if (!d.items?.length) return '请至少保留一条采购需求。'
    if (d.items.some(i => !i.sku?.trim() || !Number.isInteger(i.quantity) || Number(i.quantity) <= 0 || Number(i.quantity) > 1000000)) return '请填写真实 SKU 和 1 至 1,000,000 的整数数量。'
    if (new Set(d.items.map(i => i.sku?.trim())).size !== d.items.length) return '存在重复 SKU，请合并数量后删除重复行。'
  }
  if (selected.value?.kind === 'expense' && !d.lines?.length) return '请至少保留一条费用。'
  return ''
})
watch(draft, () => { reviewed.value = false }, { deep: true, flush: 'sync' })
const statusName = (s: string) => ({ processing: '整理中', ready: '待核对', failed: '整理失败', applying: '保存中', retry: '保存待重试', applied: '已保存草稿' }[s] || s)
function select(work: api.Work) { selected.value = work; draft.value = work.proposal ? JSON.parse(JSON.stringify(work.proposal)) : null; reviewed.value = false }
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
  const input = { team_id: props.teamId, kind: kind.value, model_name: modelName.value, source_text: source.value.trim(), customer_id: kind.value === 'crm' ? customerId.value : null, sensitivity: sensitivity.value }
  const fingerprint = JSON.stringify(input)
  if (fingerprint !== lastInput) { requestKey = crypto.randomUUID(); lastInput = fingerprint }
  try { select(await api.generateWork({ ...input, request_key: requestKey })); offset.value = 0; await refresh() }
  catch (e) { error.value = getErrorMessage(e, '整理失败；刷新成果可查看已提交任务的状态') }
  finally { generating.value = false }
}
async function apply() {
  if (!selected.value || !draft.value || !reviewed.value || validationMessage.value) return
  saving.value = true; error.value = ''
  try { const copy: api.Proposal = JSON.parse(JSON.stringify(draft.value)); copy.tasks?.forEach(t => { if (!t.due_date) t.due_date = null }); copy.lines?.forEach(l => { if (!l.invoice_no) l.invoice_no = null })
    select(await api.applyWork(selected.value.id, copy)); if (selected.value.status === 'applied') emit('saved'); await refresh()
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
onMounted(async () => {
  await refresh()
  try { models.value = (await listConfigs()).filter(m => m.is_active && m.kind !== 'embedding'); modelName.value = models.value[0]?.model_name || '' } catch (e) { error.value = getErrorMessage(e, '加载模型失败') }
  if (props.departmentCode === 'sales') {
    try { customers.value = await getTeamCustomers(props.teamId) } catch (e) { customerError.value = getErrorMessage(e, '客户列表不可用，请检查企业业务服务') }
  }
})
</script>
