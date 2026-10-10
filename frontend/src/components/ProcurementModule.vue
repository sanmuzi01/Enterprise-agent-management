<template>
  <section class="rounded-lg border border-slate-200 bg-white">
    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">我的采购申请</h2>
      <button
        @click="draftForm.visible = true"
        class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
      >
        <Plus :size="13" />
        新建申请
      </button>
    </div>

    <div v-if="draftForm.visible" class="border-b border-slate-100 bg-slate-50 px-4 py-3 space-y-2">
      <div v-for="(line, idx) in draftForm.lines" :key="idx" class="grid grid-cols-[1fr_100px_auto] gap-2 items-center">
        <input v-model="line.sku" placeholder="产品编号 (SKU)" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model.number="line.quantity" type="number" min="1" placeholder="数量" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <button
          v-if="draftForm.lines.length > 1"
          @click="draftForm.lines.splice(idx, 1)"
          class="rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-500 hover:bg-white"
        ><Trash2 :size="13" /></button>
      </div>
      <button
        @click="draftForm.lines.push({ sku: '', quantity: 1 })"
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
      <li v-for="r in myRequests" :key="r.id" :data-record="`purchase-${r.id}`" class="flex items-center justify-between rounded px-4 py-3">
        <div>
          <p class="text-sm text-slate-900">
            {{ lineSummary(r.lines) }} · 合计 ¥{{ r.totalAmount.toFixed(2) }}
          </p>
          <p class="mt-0.5 text-xs text-slate-400">
            <span v-if="r.decisionNote">处理意见：{{ r.decisionNote }}</span>
            <span v-if="r.purchaseOrder">· 供应商：{{ r.purchaseOrder.supplierName || r.purchaseOrder.supplierCode }}</span>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span class="rounded px-2 py-0.5 text-xs" :class="statusBadgeClass(r.status)">{{ statusLabel(r.status) }}</span>
          <button v-if="askAgent" data-testid="purchase-ask-agent" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            @click="askAgent(`分析采购申请 #${r.id}（${lineSummary(r.lines)}，合计 ¥${r.totalAmount.toFixed(2)}，${statusLabel(r.status)}）：库存和预算是否支持、审批到哪一步了、我下一步该做什么？`)"
          >让助手分析</button>
          <button
            v-if="r.status === 'DRAFT'"
            @click="doSubmitRequest(r.id)"
            class="rounded border border-indigo-200 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50"
          >提交</button>
        </div>
      </li>
      <li v-if="!myRequests.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有采购申请记录</li>
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
          <p class="mt-0.5 text-xs text-slate-400">申请人 #{{ r.requesterUserId }}</p>
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
import * as procurementApi from '../api/departmentProcurement'
import type { PurchaseLineDto, PurchaseRequestDto } from '../api/departmentProcurement'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { statusBadgeClass, statusLabel } from '../utils/requestStatus'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

const myRequests = ref<PurchaseRequestDto[]>([])
const pendingRequests = ref<PurchaseRequestDto[]>([])
const showPendingSection = ref(false)

const draftForm = reactive({
  visible: false, submitting: false,
  lines: [{ sku: '', quantity: 1 }] as { sku: string; quantity: number }[],
})

const lineSummary = (lines: PurchaseLineDto[]) =>
  lines.map((l) => `${l.productName || l.sku} ×${l.quantity}`).join('、') || '无明细'

async function loadMyRequests() {
  try {
    myRequests.value = await procurementApi.getMyPurchaseRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '加载我的采购申请失败'))
  }
}

async function loadPending() {
  try {
    pendingRequests.value = await procurementApi.getTeamPendingPurchaseRequests(props.teamId)
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
  draftForm.lines = [{ sku: '', quantity: 1 }]
}

async function submitDraft() {
  const lines = draftForm.lines.filter((l) => l.sku.trim() && l.quantity > 0)
  if (!lines.length) {
    toastError('请至少填写一行有效的产品编号和数量')
    return
  }
  draftForm.submitting = true
  try {
    await procurementApi.createMyPurchaseDraft({ team_id: props.teamId, lines })
    toastSuccess('已创建采购草稿')
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
    await procurementApi.submitMyPurchaseRequest(requestId)
    toastSuccess('已提交，等待部门负责人审批')
    await loadMyRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '提交失败'))
  }
}

async function doDecide(requestId: number, action: 'approve' | 'reject') {
  try {
    await procurementApi.decidePurchaseRequest(requestId, { team_id: props.teamId, action })
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
useFocusRecord(['purchase'], (id) => void flashRecord(`[data-record="purchase-${id}"]`))
</script>
