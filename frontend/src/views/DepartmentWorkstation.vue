<template>
  <div class="p-6 max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h1 class="text-lg font-semibold text-slate-900">部门工作台</h1>
        <p class="text-xs text-slate-500 mt-0.5">
          <span v-if="currentDept">{{ currentDept.name }} · {{ currentDept.role_name }}</span>
          <span v-else>还没有加入任何部门</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-if="deptStore.departments.length > 1"
          :value="deptStore.currentTeamId ?? ''"
          @change="onSwitchDept(($event.target as HTMLSelectElement).value)"
          class="rounded border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700"
        >
          <option v-for="d in deptStore.departments" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
        <button
          @click="reload(true)"
          :disabled="loading"
          class="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <div v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</div>

    <div v-else-if="!currentDept" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 py-16 text-center">
      <p class="text-sm text-slate-500">你还不属于任何部门，请联系企业管理员分配部门。</p>
    </div>

    <div v-else class="space-y-5">
      <!-- 我的请假 -->
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
          <li v-for="r in myRequests" :key="r.id" class="flex items-center justify-between px-4 py-3">
            <div>
              <p class="text-sm text-slate-900">{{ leaveTypeLabel(r.leaveTypeCode) }} · {{ r.startDate }} ~ {{ r.endDate }}（{{ r.days }} 天）</p>
              <p class="mt-0.5 text-xs text-slate-400">{{ r.reason || '无备注' }}<span v-if="r.decisionNote"> · 处理意见：{{ r.decisionNote }}</span></p>
            </div>
            <div class="flex items-center gap-2">
              <span class="rounded px-2 py-0.5 text-xs" :class="statusBadgeClass(r.status)">{{ statusLabel(r.status) }}</span>
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

      <!-- 待我审批 -->
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

      <!-- 部门助手 -->
      <section v-if="deptAgent" class="rounded-lg border border-slate-200 bg-white">
        <div class="border-b border-slate-200 px-4 py-3">
          <h2 class="text-sm font-semibold text-slate-900">部门助手 · {{ deptAgent.name }}</h2>
          <p class="text-xs text-slate-400 mt-0.5">{{ deptAgent.description }}</p>
        </div>
        <EmbeddedAgentChatPanel :agent-id="deptAgent.id" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Plus, RefreshCcw } from 'lucide-vue-next'
import { useCurrentDepartmentStore } from '../stores/currentDepartment'
import * as leaveApi from '../api/departmentLeave'
import type { LeaveRequestDto } from '../api/departmentLeave'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import EmbeddedAgentChatPanel from '../components/EmbeddedAgentChatPanel.vue'

const deptStore = useCurrentDepartmentStore()

const loading = ref(true)
const errorMsg = ref('')
const myRequests = ref<LeaveRequestDto[]>([])
const pendingRequests = ref<LeaveRequestDto[]>([])
const showPendingSection = ref(false)

const currentDept = computed(() => deptStore.currentDepartment)
const deptAgent = computed(() => {
  const teamId = deptStore.currentTeamId
  if (teamId == null) return null
  return deptStore.workspace?.agents.find((a) => a.agent_type === 'department' && a.team_id === teamId) || null
})

const draftForm = reactive({
  visible: false, submitting: false,
  leaveTypeCode: 'annual', startDate: '', endDate: '', reason: '',
})

const LEAVE_TYPE_LABELS: Record<string, string> = { annual: '年假', sick: '病假', personal: '事假' }
const leaveTypeLabel = (code: string) => LEAVE_TYPE_LABELS[code] || code

const STATUS_LABELS: Record<string, string> = { DRAFT: '草稿', SUBMITTED: '待审批', APPROVED: '已批准', REJECTED: '已拒绝' }
const statusLabel = (status: string) => STATUS_LABELS[status] || status
const statusBadgeClass = (status: string) => ({
  DRAFT: 'bg-slate-100 text-slate-500',
  SUBMITTED: 'bg-amber-50 text-amber-700',
  APPROVED: 'bg-emerald-50 text-emerald-700',
  REJECTED: 'bg-red-50 text-red-700',
}[status] || 'bg-slate-100 text-slate-500')

async function loadMyRequests() {
  try {
    myRequests.value = await leaveApi.getMyLeaveRequests()
  } catch (e) {
    errorMsg.value = getErrorMessage(e, '加载我的请假记录失败')
  }
}

async function loadPending() {
  const teamId = deptStore.currentTeamId
  if (teamId == null) {
    showPendingSection.value = false
    return
  }
  try {
    pendingRequests.value = await leaveApi.getTeamPendingLeaveRequests(teamId)
    showPendingSection.value = true
  } catch (e: any) {
    // 403（不是部门负责人/企业管理员）就隐藏这个区块，不当错误处理——
    // 普通员工没有这个权限是正常状态，不是异常。
    if (e?.response?.status === 403) {
      showPendingSection.value = false
    } else {
      errorMsg.value = getErrorMessage(e, '加载待审批列表失败')
    }
  }
}

async function reload(force = false) {
  loading.value = true
  errorMsg.value = ''
  try {
    await deptStore.load(force)
    await Promise.all([loadMyRequests(), loadPending()])
  } catch (e) {
    errorMsg.value = getErrorMessage(e, '加载部门工作台失败')
  } finally {
    loading.value = false
  }
}

function onSwitchDept(value: string) {
  deptStore.selectTeam(value ? Number(value) : null)
  loadPending()
}

async function submitDraft() {
  const teamId = deptStore.currentTeamId
  if (teamId == null) return
  if (!draftForm.startDate || !draftForm.endDate) {
    toastError('请填写起止日期')
    return
  }
  draftForm.submitting = true
  try {
    await leaveApi.createMyLeaveDraft({
      team_id: teamId,
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
  const teamId = deptStore.currentTeamId
  if (teamId == null) return
  try {
    await leaveApi.decideLeaveRequest(requestId, { team_id: teamId, action })
    toastSuccess(action === 'approve' ? '已批准' : '已拒绝')
    await Promise.all([loadPending(), loadMyRequests()])
  } catch (e) {
    toastError(getErrorMessage(e, '处理失败'))
  }
}

watch(() => deptStore.currentTeamId, () => loadPending())

onMounted(() => reload())
</script>
