<template>
  <section class="rounded-lg border border-slate-200 bg-white">
    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">我的请假</h2>
      <button
        @click="draftForm.visible = true"
        class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
      >
        <Plus :size="13" />
        新建申请
      </button>
    </div>

    <div v-if="draftForm.visible" class="border-b border-slate-100 bg-slate-50 px-4 py-3 space-y-2">
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <select v-model="draftForm.leaveTypeCode" class="rounded border border-slate-200 px-2 py-1.5 text-sm">
          <option value="annual">年假</option>
          <option value="sick">病假</option>
          <option value="personal">事假</option>
        </select>
        <input v-model="draftForm.startDate" type="date" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="draftForm.endDate" type="date" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
        <input v-model="draftForm.reason" placeholder="原因（可选）" class="rounded border border-slate-200 px-2 py-1.5 text-sm" />
      </div>
      <div class="flex gap-2">
        <button
          @click="submitDraft"
          :disabled="draftForm.submitting"
          class="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
        >提交申请</button>
        <button @click="draftForm.visible = false" class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-white">取消</button>
      </div>
    </div>

    <ul class="divide-y divide-slate-100">
      <li v-for="r in myRequests" :key="r.id" :data-record="`leave-${r.id}`" class="flex items-center justify-between rounded px-4 py-3">
        <div>
          <p class="text-sm text-slate-900">{{ leaveTypeLabel(r.leaveTypeCode) }} · {{ r.startDate }} ~ {{ r.endDate }}（{{ r.days }} 天）</p>
          <p class="mt-0.5 text-xs text-slate-400">{{ r.reason || '无备注' }}<span v-if="r.decisionNote"> · 处理意见：{{ r.decisionNote }}</span></p>
        </div>
        <div class="flex items-center gap-2">
          <span class="rounded px-2 py-0.5 text-xs" :class="statusBadgeClass(r.status)">{{ statusLabel(r.status) }}</span>
          <button v-if="askAgent" data-testid="leave-ask-agent" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            @click="askAgent(`分析请假单 #${r.id}（${leaveTypeLabel(r.leaveTypeCode)}，${r.startDate} 至 ${r.endDate}，${statusLabel(r.status)}）：假期余额够不够、审批到哪一步了、我下一步该做什么？`)"
          >让助手分析</button>
          <button
            v-if="r.status === 'DRAFT'"
            @click="doSubmitRequest(r.id)"
            class="rounded border border-indigo-200 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50"
          >提交</button>
        </div>
      </li>
      <li v-if="!myRequests.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有请假记录</li>
    </ul>
  </section>

  <section v-if="showPendingSection" class="rounded-lg border border-slate-200 bg-white">
    <div class="border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">待我审批</h2>
    </div>
    <ul class="divide-y divide-slate-100">
      <li v-for="r in pendingRequests" :key="r.id" class="flex items-center justify-between px-4 py-3">
        <div>
          <p class="text-sm text-slate-900">{{ leaveTypeLabel(r.leaveTypeCode) }} · {{ r.startDate }} ~ {{ r.endDate }}（{{ r.days }} 天）</p>
          <p class="mt-0.5 text-xs text-slate-400">申请人 #{{ r.applicantUserId }}{{ r.reason ? ' · ' + r.reason : '' }}</p>
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
import { Plus } from 'lucide-vue-next'
import * as leaveApi from '../api/departmentLeave'
import type { LeaveRequestDto } from '../api/departmentLeave'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { statusBadgeClass, statusLabel } from '../utils/requestStatus'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

const myRequests = ref<LeaveRequestDto[]>([])
const pendingRequests = ref<LeaveRequestDto[]>([])
const showPendingSection = ref(false)

const draftForm = reactive({
  visible: false, submitting: false,
  leaveTypeCode: 'annual', startDate: '', endDate: '', reason: '',
})

const LEAVE_TYPE_LABELS: Record<string, string> = { annual: '年假', sick: '病假', personal: '事假' }
const leaveTypeLabel = (code: string) => LEAVE_TYPE_LABELS[code] || code

async function loadMyRequests() {
  try {
    myRequests.value = await leaveApi.getMyLeaveRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '加载我的请假记录失败'))
  }
}

async function loadPending() {
  try {
    pendingRequests.value = await leaveApi.getTeamPendingLeaveRequests(props.teamId)
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

async function submitDraft() {
  if (!draftForm.startDate || !draftForm.endDate) {
    toastError('请填写起止日期')
    return
  }
  draftForm.submitting = true
  try {
    await leaveApi.createMyLeaveDraft({
      team_id: props.teamId,
      leave_type_code: draftForm.leaveTypeCode,
      start_date: draftForm.startDate,
      end_date: draftForm.endDate,
      reason: draftForm.reason || undefined,
    })
    toastSuccess('已创建请假草稿')
    draftForm.visible = false
    draftForm.startDate = ''
    draftForm.endDate = ''
    draftForm.reason = ''
    await loadMyRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '创建失败'))
  } finally {
    draftForm.submitting = false
  }
}

async function doSubmitRequest(requestId: number) {
  try {
    await leaveApi.submitMyLeaveRequest(requestId)
    toastSuccess('已提交，等待部门负责人审批')
    await loadMyRequests()
  } catch (e) {
    toastError(getErrorMessage(e, '提交失败'))
  }
}

async function doDecide(requestId: number, action: 'approve' | 'reject') {
  try {
    await leaveApi.decideLeaveRequest(requestId, { team_id: props.teamId, action })
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
useFocusRecord(['leave'], (id) => void flashRecord(`[data-record="leave-${id}"]`))
</script>
