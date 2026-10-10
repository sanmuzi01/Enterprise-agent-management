<template>
  <section class="rounded-lg border border-slate-200 bg-white">
    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">我的报销</h2>
      <button
        @click="draftForm.visible = true"
        class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
      >
        <Plus :size="13" />
        新建申请
      </button>
    </div>

    <div v-if="draftForm.visible" class="border-b border-slate-100 bg-slate-50 px-4 py-3 space-y-2">
      <div class="flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <label for="expense-city">出差城市</label>
        <select id="expense-city" v-model="draftForm.cityLevel" class="rounded border border-slate-200 px-2 py-1">
          <option value="">不涉及</option>
          <option value="tier1">一线城市</option>
          <option value="tier2">二线城市</option>
          <option value="other">其他城市</option>
        </select>
        <span class="text-slate-400">按公司费用标准检查：超标准要写说明，规定要发票的必须有发票。</span>
      </div>
      <template v-for="(line, idx) in draftForm.lines" :key="idx">
      <div class="grid grid-cols-[110px_100px_1fr_1fr_auto_auto] gap-2 items-center">
        <select v-model="line.category" class="rounded border border-slate-200 px-2 py-1.5 text-sm">
          <option v-for="c in CATEGORY_OPTIONS" :key="c" :value="c">{{ CATEGORY_LABELS[c] }}</option>
        </select>
        <input v-model.number="line.amount" type="number" min="0" placeholder="金额" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="line.description" placeholder="说明（可选）" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="line.invoiceNo" :disabled="!!line.invoiceExtractionId" placeholder="发票号（可选）" class="rounded border border-slate-200 px-2 py-1.5 text-sm disabled:bg-slate-100" />
        <button @click="recognizing = recognizing === idx ? null : idx" :data-testid="`expense-recognize-${idx}`"
          class="rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-600 hover:bg-white">{{ line.invoiceExtractionId ? '已关联发票' : '识别发票' }}</button>
        <button
          v-if="draftForm.lines.length > 1"
          @click="draftForm.lines.splice(idx, 1)"
          class="rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-500 hover:bg-white"
        ><Trash2 :size="13" /></button>
      </div>
      <InvoiceRecognizer v-if="recognizing === idx" :team-id="teamId" @use="(inv) => useInvoice(idx, inv)" />
      <div v-if="line.policyMessage" class="space-y-1 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
        <p>{{ line.policyMessage }}</p>
        <input v-if="line.needsReason" v-model="line.overReason" :aria-label="`第 ${idx + 1} 条超标准说明`" maxlength="200"
          placeholder="超标准说明（例：接待客户 6 人）" class="w-full rounded border border-amber-300 bg-white px-2 py-1" />
      </div>
      </template>
      <button
        @click="draftForm.lines.push(newLine())"
        class="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-700"
      >
        <Plus :size="12" /> 加一行
      </button>
      <div class="flex gap-2 pt-1">
        <button
          @click="submitDraft"
          :disabled="draftForm.submitting"
          class="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
        >提交申请</button>
        <button @click="draftForm.visible = false" class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-white">取消</button>
      </div>
    </div>

    <ul class="divide-y divide-slate-100">
      <li v-for="r in myRequests" :key="r.id" :data-record="`expense-${r.id}`" class="flex items-center justify-between rounded px-4 py-3">
        <div>
          <p class="text-sm text-slate-900">
            {{ lineSummary(r.lines) }} · 合计 ¥{{ r.totalAmount.toFixed(2) }}
          </p>
          <p class="mt-0.5 text-xs text-slate-400">
            <span v-if="r.decisionNote">处理意见：{{ r.decisionNote }}</span>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span class="rounded px-2 py-0.5 text-xs" :class="statusBadgeClass(r.status)">{{ statusLabel(r.status) }}</span>
          <button v-if="askAgent"
            @click="askAgent(`分析报销单 #${r.id}（${lineSummary(r.lines)}，合计 ¥${r.totalAmount.toFixed(2)}，${statusLabel(r.status)}）：材料是否齐全、预算够不够、下一步该做什么？`)"
            data-testid="expense-ask-agent"
            class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >让助手分析</button>
          <button
            v-if="r.status === 'DRAFT'"
            @click="doSubmitRequest(r.id)"
            class="rounded border border-indigo-200 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50"
          >提交</button>
        </div>
      </li>
      <li v-if="!myRequests.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有报销记录</li>
    </ul>
  </section>

  <section v-if="showPendingSection" class="rounded-lg border border-slate-200 bg-white">
    <div class="border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">待我审批</h2>
    </div>
    <ul class="divide-y divide-slate-100">
      <li v-for="r in pendingRequests" :key="r.id" class="flex items-center justify-between px-4 py-3">
        <div>
          <p class="text-sm text-slate-900">{{ lineSummary(r.lines) }} · 合计 ¥{{ r.totalAmount.toFixed(2) }}</p>
          <p class="mt-0.5 text-xs text-slate-400">申请人 #{{ r.applicantUserId }}</p>
        </div>
        <div class="flex gap-1.5">
          <button
            @click="doDecide(r.id, 'approve')"
            class="rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700"
          >批准</button>
          <button
            @click="doDecide(r.id, 'reject')"
            class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50"
          >拒绝</button>
        </div>
      </li>
      <li v-if="!pendingRequests.length" class="px-4 py-8 text-center text-sm text-slate-400">没有待审批的申请</li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import * as financeApi from '../api/departmentFinance'
import type { ExpenseClaimDto, ExpenseLineDto } from '../api/departmentFinance'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { statusBadgeClass, statusLabel } from '../utils/requestStatus'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'
import InvoiceRecognizer from './department/InvoiceRecognizer.vue'
import { checkPolicy, type InvoiceExtraction } from '../api/financeExtras'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

const myRequests = ref<ExpenseClaimDto[]>([])
const pendingRequests = ref<ExpenseClaimDto[]>([])
const showPendingSection = ref(false)

const CATEGORY_OPTIONS = ['TRAVEL', 'MEAL', 'OFFICE_SUPPLY', 'TRANSPORT', 'OTHER']
const CATEGORY_LABELS: Record<string, string> = {
  TRAVEL: '差旅', MEAL: '餐饮', OFFICE_SUPPLY: '办公用品', TRANSPORT: '交通', OTHER: '其它',
}

interface DraftLine {
  category: string; amount: number; description: string; invoiceNo: string
  invoiceExtractionId?: number; overReason?: string; policyMessage?: string; needsReason?: boolean
}
const newLine = (): DraftLine => ({ category: 'TRAVEL', amount: 0, description: '', invoiceNo: '' })
const draftForm = reactive({
  visible: false, submitting: false, cityLevel: '',
  lines: [newLine()] as DraftLine[],
})
const recognizing = ref<number | null>(null)

function useInvoice(idx: number, invoice: InvoiceExtraction) {
  const line = draftForm.lines[idx]
  line.invoiceExtractionId = invoice.id
  line.invoiceNo = invoice.fields.invoice_number || ''
  if (!line.amount && invoice.fields.total_amount) line.amount = Number(invoice.fields.total_amount)
  if (!line.description && invoice.fields.seller_name) line.description = invoice.fields.seller_name
  recognizing.value = null
}

const lineSummary = (lines: ExpenseLineDto[]) =>
  lines.map((l) => `${CATEGORY_LABELS[l.category] || l.category} ¥${l.amount.toFixed(2)}`).join('、') || '无明细'

async function loadMyRequests() {
  try {
    myRequests.value = await financeApi.getMyExpenseClaims()
  } catch (e) {
    toastError(getErrorMessage(e, '加载我的报销记录失败'))
  }
}

async function loadPending() {
  try {
    pendingRequests.value = await financeApi.getTeamPendingExpenseClaims(props.teamId)
    showPendingSection.value = true
  } catch (e: any) {
    // 403（不是部门负责人/企业管理员）就隐藏这个区块，不当错误处理——
    // 普通员工没有这个权限是正常状态，不是异常。
    if (e?.response?.status === 403) {
      showPendingSection.value = false
    } else {
      toastError(getErrorMessage(e, '加载待审批列表失败'))
    }
  }
}

function resetDraftForm() {
  draftForm.lines = [newLine()]
  draftForm.cityLevel = ''
  recognizing.value = null
}

/** 按费用标准预检：缺票、超标准未写说明时在对应行提示，返回能否提交。 */
async function policyOk(lines: DraftLine[]): Promise<boolean> {
  const results = await checkPolicy(props.teamId, lines.map((l) => ({ category: l.category, amount: l.amount, invoice_no: l.invoiceNo || undefined })),
    draftForm.cityLevel)
  let ok = true
  results.forEach((r, i) => {
    const line = lines[i]
    line.policyMessage = r.status === 'ok' ? undefined : r.message
    line.needsReason = r.status === 'over_limit'
    if (r.status === 'missing_receipt' || (r.status === 'over_limit' && !(line.overReason || '').trim())) ok = false
  })
  return ok
}

async function submitDraft() {
  const lines = draftForm.lines.filter((l) => l.amount > 0)
  if (!lines.length) {
    toastError('请至少填写一行有效的费用明细')
    return
  }
  draftForm.submitting = true
  try {
    if (!(await policyOk(lines))) {
      toastError('有明细不符合费用标准，请按提示补充发票或填写超标准说明')
      return
    }
    await financeApi.createMyExpenseDraft({
      team_id: props.teamId,
      city_level: draftForm.cityLevel || undefined,
      lines: lines.map((l) => ({
        category: l.category, amount: l.amount,
        description: l.description || undefined, invoice_no: l.invoiceNo || undefined,
        invoice_extraction_id: l.invoiceExtractionId, over_standard_reason: l.overReason || undefined,
      })),
    })
    toastSuccess('已创建报销草稿')
    draftForm.visible = false
    resetDraftForm()
    await loadMyRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '创建失败'))
  } finally {
    draftForm.submitting = false
  }
}

async function doSubmitRequest(requestId: number) {
  try {
    await financeApi.submitMyExpenseClaim(requestId)
    toastSuccess('已提交，等待部门负责人审批')
    await loadMyRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '提交失败'))
  }
}

async function doDecide(requestId: number, action: 'approve' | 'reject') {
  try {
    await financeApi.decideExpenseClaim(requestId, { team_id: props.teamId, action })
    toastSuccess(action === 'approve' ? '已批准' : '已拒绝')
    await Promise.all([loadPending(), loadMyRequests()])
  } catch (e) {
    toastError(getErrorMessage(e, '处理失败'))
  }
}

watch(() => props.teamId, () => {
  loadMyRequests()
  loadPending()
})

onMounted(() => {
  loadMyRequests()
  loadPending()
})

// 助手回复里的卡片 → 打开这条记录
useFocusRecord(['expense'], (id) => void flashRecord(`[data-record="expense-${id}"]`))
</script>
