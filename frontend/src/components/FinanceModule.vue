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
      <div v-for="(line, idx) in draftForm.lines" :key="idx" class="grid grid-cols-[110px_100px_1fr_1fr_auto] gap-2 items-center">
        <select v-model="line.category" class="rounded border border-slate-200 px-2 py-1.5 text-sm">
          <option v-for="c in CATEGORY_OPTIONS" :key="c" :value="c">{{ CATEGORY_LABELS[c] }}</option>
        </select>
        <input v-model.number="line.amount" type="number" min="0" placeholder="金额" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="line.description" placeholder="说明（可选）" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="line.invoiceNo" placeholder="发票号（可选）" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <button
          v-if="draftForm.lines.length > 1"
          @click="draftForm.lines.splice(idx, 1)"
          class="rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-500 hover:bg-white"
        ><Trash2 :size="13" /></button>
      </div>
      <button
        @click="draftForm.lines.push({ category: 'TRAVEL', amount: 0, description: '', invoiceNo: '' })"
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
      <li v-for="r in myRequests" :key="r.id" class="flex items-center justify-between px-4 py-3">
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
import { useAskDeptAgent } from './department/askAgent'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

const myRequests = ref<ExpenseClaimDto[]>([])
const pendingRequests = ref<ExpenseClaimDto[]>([])
const showPendingSection = ref(false)

const CATEGORY_OPTIONS = ['TRAVEL', 'MEAL', 'OFFICE_SUPPLY', 'TRANSPORT', 'OTHER']
const CATEGORY_LABELS: Record<string, string> = {
  TRAVEL: '差旅', MEAL: '餐饮', OFFICE_SUPPLY: '办公用品', TRANSPORT: '交通', OTHER: '其它',
}

const draftForm = reactive({
  visible: false, submitting: false,
  lines: [{ category: 'TRAVEL', amount: 0, description: '', invoiceNo: '' }] as
    { category: string; amount: number; description: string; invoiceNo: string }[],
})

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
  draftForm.lines = [{ category: 'TRAVEL', amount: 0, description: '', invoiceNo: '' }]
}

async function submitDraft() {
  const lines = draftForm.lines.filter((l) => l.amount > 0)
  if (!lines.length) {
    toastError('请至少填写一行有效的费用明细')
    return
  }
  draftForm.submitting = true
  try {
    await financeApi.createMyExpenseDraft({
      team_id: props.teamId,
      lines: lines.map((l) => ({
        category: l.category, amount: l.amount,
        description: l.description || undefined, invoice_no: l.invoiceNo || undefined,
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
</script>
