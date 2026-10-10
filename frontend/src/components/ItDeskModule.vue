<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="it-desk">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">IT 服务台</h2>
        <p class="mt-0.5 text-xs text-slate-400">工单队列按处理时限排序，最急的在前；接单、等待用户、解决都会留痕。</p>
      </div>
      <div class="flex items-center gap-1 text-xs">
        <button v-for="t in TABS" :key="t.value" @click="setTab(t.value)" class="rounded px-2.5 py-1"
          :class="tab === t.value ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50'" :data-testid="`desk-tab-${t.value}`">{{ t.label }}</button>
      </div>
    </div>

    <!-- ================= 汇总 ================= -->
    <div v-if="tab === 'summary'" class="space-y-3 px-4 py-4" data-testid="desk-summary">
      <template v-if="summary">
        <p class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="desk-narrative">{{ summary.narrative }}</p>
        <div class="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
          <div class="rounded border border-slate-200 px-2 py-2"><p class="text-slate-400">待接单</p><p class="text-base font-semibold text-slate-900">{{ summary.unassigned }}</p></div>
          <div class="rounded border px-2 py-2" :class="summary.overdue ? 'border-red-200 bg-red-50' : 'border-slate-200'"><p class="text-slate-400">已超时</p><p class="text-base font-semibold" :class="summary.overdue ? 'text-red-700' : 'text-slate-900'">{{ summary.overdue }}</p></div>
          <div class="rounded border border-slate-200 px-2 py-2"><p class="text-slate-400">SLA 达成率</p><p class="text-base font-semibold text-slate-900">{{ summary.effect.slaMetRate === null ? '—' : summary.effect.slaMetRate + '%' }}</p></div>
          <div class="rounded border border-slate-200 px-2 py-2"><p class="text-slate-400">平均解决</p><p class="text-base font-semibold text-slate-900">{{ summary.effect.avgResolveHours === null ? '—' : summary.effect.avgResolveHours + ' 小时' }}</p></div>
        </div>
        <div v-if="summary.load.length" class="text-xs text-slate-600">
          <p class="mb-1 text-slate-400">处理人负载（处理中的工单）</p>
          <p>{{ summary.load.map((l) => `${l.userName || '#' + l.userId} ${l.open} 张`).join('、') }}</p>
        </div>
      </template>
      <ItSelfServiceMetrics :team-id="teamId" />
    </div>

    <!-- ================= 设备 ================= -->
    <div v-else-if="tab === 'devices'" class="space-y-3 px-4 py-4" data-testid="desk-devices">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <label for="device-filter" class="text-slate-500">状态</label>
        <select id="device-filter" v-model="deviceFilter" @change="loadDevices" class="rounded border border-slate-200 px-2 py-1">
          <option value="">全部</option><option v-for="(label, key) in DEVICE_STATUS" :key="key" :value="key">{{ label }}</option>
        </select>
        <button @click="deviceForm.visible = !deviceForm.visible" class="ml-auto rounded border border-indigo-200 px-2.5 py-1 text-indigo-700 hover:bg-indigo-50" data-testid="device-new">入库设备</button>
      </div>
      <div v-if="deviceForm.visible" class="grid grid-cols-2 gap-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs sm:grid-cols-5">
        <input v-model="deviceForm.assetNo" placeholder="资产编号" aria-label="资产编号" class="rounded border border-slate-200 px-2 py-1.5" data-testid="device-asset" />
        <select v-model="deviceForm.deviceType" aria-label="设备类型" class="rounded border border-slate-200 px-2 py-1.5">
          <option v-for="(label, key) in DEVICE_TYPES" :key="key" :value="key">{{ label }}</option>
        </select>
        <input v-model="deviceForm.model" placeholder="型号" aria-label="型号" class="rounded border border-slate-200 px-2 py-1.5" data-testid="device-model" />
        <input v-model="deviceForm.warrantyUntil" type="date" aria-label="保修截止" class="rounded border border-slate-200 px-2 py-1.5" />
        <button @click="createDevice" :disabled="busy || !deviceForm.assetNo.trim() || !deviceForm.model.trim()" class="rounded bg-indigo-600 px-2 py-1.5 text-white disabled:opacity-50" data-testid="device-create">入库</button>
      </div>
      <ul class="divide-y divide-slate-100">
        <li v-for="d in devices" :key="d.id" class="py-2.5">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0 text-sm text-slate-900">{{ d.assetNo }} · {{ d.model }}
              <span class="ml-1 rounded px-1.5 py-0.5 text-xs" :class="deviceClass(d.status)">{{ DEVICE_STATUS[d.status] }}</span>
              <span v-if="d.assigneeName" class="ml-1 text-xs text-slate-500">持有人 {{ d.assigneeName }}</span>
              <span v-if="d.warrantyStatus === 'EXPIRING'" class="ml-1 text-xs text-amber-700">保修将到期 {{ d.warrantyUntil }}</span>
              <span v-else-if="d.warrantyStatus === 'EXPIRED'" class="ml-1 text-xs text-slate-400">已过保</span>
            </div>
            <div class="flex gap-1.5 text-xs">
              <button v-if="d.status === 'ASSIGNED'" @click="deviceAction(d.id, 'return')" class="rounded border border-slate-200 px-2 py-0.5 hover:bg-slate-50">收回</button>
              <button v-if="d.status === 'IN_STOCK' || d.status === 'ASSIGNED'" @click="deviceAction(d.id, 'repair')" class="rounded border border-slate-200 px-2 py-0.5 hover:bg-slate-50">送修</button>
              <button v-if="d.status === 'REPAIR'" @click="deviceAction(d.id, 'repair-done')" class="rounded border border-slate-200 px-2 py-0.5 hover:bg-slate-50">修好</button>
              <button v-if="d.status !== 'RETIRED' && !d.assigneeUserId" @click="deviceAction(d.id, 'retire')" class="rounded border border-red-200 px-2 py-0.5 text-red-700 hover:bg-red-50">报废</button>
              <button @click="showHistory(d.id)" class="rounded border border-slate-200 px-2 py-0.5 hover:bg-slate-50">历史</button>
            </div>
          </div>
          <ul v-if="history?.id === d.id" class="mt-1.5 space-y-0.5 pl-3 text-xs text-slate-500">
            <li v-for="(e, i) in history.events" :key="i">{{ e.createdAt.slice(0, 16).replace('T', ' ') }} · {{ EVENT_LABELS[e.eventType] || e.eventType }}<span v-if="e.subjectName"> → {{ e.subjectName }}</span><span v-if="e.ticketId"> · 工单 #{{ e.ticketId }}</span><span v-if="e.note"> · {{ e.note }}</span></li>
          </ul>
        </li>
        <li v-if="!devices.length" class="py-6 text-center text-sm text-slate-400">没有设备记录</li>
      </ul>
      <p class="text-xs text-slate-400">发放设备：在“设备申请”工单详情里选择在库设备发放给申请人，会自动关联工单。</p>
    </div>

    <!-- ================= 队列 ================= -->
    <template v-else>
      <div class="flex flex-wrap items-center gap-1.5 border-b border-slate-100 px-4 py-2 text-xs">
        <button v-for="f in QUEUE_FILTERS" :key="f.value" @click="setFilter(f.value)" class="rounded-full px-2.5 py-0.5"
          :class="filter === f.value ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" :data-testid="`desk-filter-${f.value}`">{{ f.label }}</button>
      </div>
      <ul class="divide-y divide-slate-100" data-testid="desk-queue">
        <li v-for="t in queue" :key="t.id">
          <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50" :class="selected?.id === t.id ? 'bg-indigo-50/50' : ''"
            @click="select(t.id)" :data-testid="`desk-row-${t.id}`">
            <div class="min-w-0">
              <p class="truncate text-sm text-slate-900">#{{ t.id }} · {{ t.title }}</p>
              <p class="mt-0.5 text-xs text-slate-400">{{ t.categoryLabel }} · {{ t.teamName }} · 申请人 {{ t.requesterName }}<span v-if="t.assigneeName"> · 处理人 {{ t.assigneeName }}</span></p>
            </div>
            <div class="flex shrink-0 items-center gap-1.5 text-xs">
              <span class="rounded px-2 py-0.5" :class="priorityClass(t.priority)">{{ t.priorityLabel }}</span>
              <span class="rounded px-2 py-0.5" :class="slaClass(t.slaStatus)">{{ SLA_LABELS[t.slaStatus] }}</span>
              <span class="rounded bg-slate-100 px-2 py-0.5 text-slate-600">{{ t.statusLabel }}</span>
            </div>
          </button>
        </li>
        <li v-if="!queue.length && !loading" class="px-4 py-8 text-center text-sm text-slate-400">队列是空的</li>
      </ul>

      <div v-if="selected" class="space-y-3 border-t border-slate-200 bg-slate-50/60 px-4 py-4" data-testid="desk-detail">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="flex items-center gap-2 text-sm font-medium text-slate-900">#{{ selected.id }} {{ selected.title }}
            <button v-if="askAgent" data-testid="desk-ask-agent" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
              @click="askAgent(`分析工单 #${selected.id}「${selected.title}」：根据描述给出排查思路、需要向提交人确认的信息，以及有没有超时风险`)"
            >让助手分析</button>
          </p>
          <p class="text-xs text-slate-500">处理时限 {{ selected.slaDueAt.slice(0, 16).replace('T', ' ') }}（UTC）· <span :class="slaClass(selected.slaStatus)" class="rounded px-1.5 py-0.5">{{ SLA_LABELS[selected.slaStatus] }}</span></p>
        </div>
        <p class="whitespace-pre-line text-sm text-slate-700">{{ selected.description }}</p>
        <p v-if="selected.classifyReason" class="rounded bg-white px-2.5 py-1.5 text-xs text-slate-500" data-testid="desk-classify">
          系统建议：{{ CATEGORY_LABELS[selected.suggestedCategory as TicketCategory] }} · {{ PRIORITY_LABELS[selected.suggestedPriority as TicketPriority] }}（{{ selected.classifyReason }}）
          <span v-if="selected.suggestedCategory !== selected.category || selected.suggestedPriority !== selected.priority" class="text-amber-700">；当前为 {{ selected.categoryLabel }} · {{ selected.priorityLabel }}，与建议不同</span>
        </p>

        <ol class="space-y-1.5">
          <li v-for="c in selected.comments" :key="c.id" class="rounded border px-2.5 py-1.5 text-xs"
            :class="c.internal ? 'border-violet-200 bg-violet-50' : c.kind === 'COMMENT' ? 'border-slate-200 bg-white' : 'border-slate-100 bg-slate-100/70 text-slate-500'">
            <span class="font-medium text-slate-700">{{ c.kind === 'COMMENT' ? (c.authorName || `#${c.authorUserId}`) : '系统' }}</span>
            <span v-if="c.internal" class="ml-1 rounded bg-violet-100 px-1.5 text-violet-700">内部</span>
            <span class="ml-2 text-slate-400">{{ c.createdAt.slice(0, 16).replace('T', ' ') }}</span>
            <p class="mt-0.5 whitespace-pre-line">{{ c.body }}</p>
          </li>
        </ol>

        <div v-if="isActive(selected.status)" class="space-y-2.5 border-t border-slate-200 pt-3">
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <button @click="take" :disabled="busy" class="rounded bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-700 disabled:opacity-50" data-testid="desk-take">{{ selected.assigneeUserId ? '改为我处理' : '接单' }}</button>
            <label for="desk-assignee" class="text-slate-500">指派给</label>
            <select id="desk-assignee" :value="selected.assigneeUserId ?? ''" @change="assignTo(($event.target as HTMLSelectElement).value)" class="rounded border border-slate-200 px-2 py-1.5" data-testid="desk-assignee">
              <option value="">（未指派）</option><option v-for="s in staff" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
            <button v-if="selected.status !== 'IN_PROGRESS'" @click="setStatus('IN_PROGRESS')" :disabled="busy" class="rounded border border-slate-200 px-3 py-1.5 hover:bg-white">转处理中</button>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <input v-model="waitNote" placeholder="需要用户补充什么？" class="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="desk-wait-note" />
            <button @click="setStatus('WAITING_USER')" :disabled="busy || waitNote.trim().length < 2 || selected.status === 'WAITING_USER'" class="rounded border border-orange-300 px-3 py-1.5 text-orange-800 hover:bg-orange-50 disabled:opacity-50" data-testid="desk-wait">等待用户</button>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <input v-model="resolution" placeholder="怎么解决的（用户会看到）" class="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="desk-resolution" />
            <button @click="resolve" :disabled="busy || resolution.trim().length < 4" class="rounded bg-emerald-600 px-3 py-1.5 text-white hover:bg-emerald-700 disabled:opacity-50" data-testid="desk-resolve">解决</button>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <input v-model="commentBody" placeholder="评论…" class="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="desk-comment" />
            <label class="flex items-center gap-1 text-slate-500"><input v-model="commentInternal" type="checkbox" />仅内部可见</label>
            <button @click="comment" :disabled="busy || !commentBody.trim()" class="rounded border border-slate-200 px-3 py-1.5 hover:bg-white disabled:opacity-50">发送</button>
          </div>
          <details class="text-xs text-slate-500">
            <summary class="cursor-pointer select-none">调整分类 / 优先级</summary>
            <div class="mt-1.5 flex flex-wrap items-center gap-2">
              <select v-model="reclass.category" aria-label="分类" class="rounded border border-slate-200 px-2 py-1.5"><option v-for="(l, k) in CATEGORY_LABELS" :key="k" :value="k">{{ l }}</option></select>
              <select v-model="reclass.priority" aria-label="优先级" class="rounded border border-slate-200 px-2 py-1.5" data-testid="reclass-priority"><option v-for="(l, k) in PRIORITY_LABELS" :key="k" :value="k">{{ l }}</option></select>
              <input v-model="reclass.reason" placeholder="调整原因" class="min-w-[140px] flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="reclass-reason" />
              <button @click="reclassify" :disabled="busy || reclass.reason.trim().length < 2" class="rounded border border-slate-200 px-3 py-1.5 hover:bg-white disabled:opacity-50" data-testid="reclass-apply">应用</button>
            </div>
          </details>
          <div v-if="selected.category === 'DEVICE'" class="flex flex-wrap items-center gap-2 text-xs" data-testid="desk-issue-device">
            <label for="issue-device" class="text-slate-500">发放设备给申请人</label>
            <select id="issue-device" v-model="issueDeviceId" class="rounded border border-slate-200 px-2 py-1.5">
              <option :value="0">选择在库设备</option><option v-for="d in stockDevices" :key="d.id" :value="d.id">{{ d.assetNo }} · {{ d.model }}</option>
            </select>
            <button @click="issueDevice" :disabled="busy || !issueDeviceId" class="rounded border border-indigo-200 px-3 py-1.5 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50" data-testid="desk-issue">发放</button>
          </div>
        </div>
        <p v-else-if="selected.status === 'RESOLVED'" class="text-xs text-emerald-700">已解决，等待申请人确认：{{ selected.resolution }}</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import ItSelfServiceMetrics from './department/ItSelfServiceMetrics.vue'
import { onMounted, reactive, ref, watch } from 'vue'
import * as api from '../api/itService'
import type { DeskSummary, Device, SlaStatus, TicketCategory, TicketDetail, TicketPriority, TicketStatus, TicketSummary } from '../api/itService'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

type Tab = 'queue' | 'devices' | 'summary'
const TABS: { value: Tab; label: string }[] = [{ value: 'queue', label: '工单队列' }, { value: 'devices', label: '设备台账' }, { value: 'summary', label: '汇总' }]
const QUEUE_FILTERS = [
  { value: 'all', label: '全部' }, { value: 'unassigned', label: '待接单' }, { value: 'me', label: '我的' },
  { value: 'overdue', label: '已超时' }, { value: 'WAITING_USER', label: '等待用户' }, { value: 'RESOLVED', label: '已解决待确认' },
  { value: 'CLOSED', label: '已关闭' },
]
const CATEGORY_LABELS: Record<TicketCategory, string> = { INCIDENT: '故障', ACCOUNT: '账号申请', PERMISSION: '权限申请', DEVICE: '设备申请', OTHER: '咨询/其他' }
const PRIORITY_LABELS: Record<TicketPriority, string> = { LOW: '低', NORMAL: '普通', HIGH: '高', URGENT: '紧急' }
const SLA_LABELS: Record<SlaStatus, string> = { OK: '正常', AT_RISK: '即将超时', BREACHED: '已超时', MET: '按时', PAUSED: '暂停', NONE: '—' }
const DEVICE_STATUS = { IN_STOCK: '在库', ASSIGNED: '已领用', REPAIR: '维修中', RETIRED: '已报废' } as const
const DEVICE_TYPES = { LAPTOP: '笔记本', DESKTOP: '台式机', MONITOR: '显示器', PHONE: '手机', PERIPHERAL: '外设', OTHER: '其他' } as const
const EVENT_LABELS: Record<string, string> = { CREATED: '入库', ASSIGNED: '发放', RETURNED: '收回', REPAIR: '送修', REPAIRED: '修好', RETIRED: '报废' }

const tab = ref<Tab>('queue')
const filter = ref('all')
const queue = ref<TicketSummary[]>([])
const selected = ref<TicketDetail | null>(null)
const staff = ref<{ id: number; name: string }[]>([])
const summary = ref<DeskSummary | null>(null)
const devices = ref<Device[]>([])
const stockDevices = ref<Device[]>([])
const history = ref<Device | null>(null)
const deviceFilter = ref('')
const loading = ref(false)
const busy = ref(false)
const waitNote = ref('')
const resolution = ref('')
const commentBody = ref('')
const commentInternal = ref(false)
const issueDeviceId = ref(0)
const reclass = reactive({ category: 'INCIDENT' as TicketCategory, priority: 'NORMAL' as TicketPriority, reason: '' })
const deviceForm = reactive({ visible: false, assetNo: '', deviceType: 'LAPTOP', model: '', warrantyUntil: '' })

const isActive = (s: TicketStatus) => ['OPEN', 'IN_PROGRESS', 'WAITING_USER'].includes(s)
const priorityClass = (p: TicketPriority) => ({ URGENT: 'bg-red-50 text-red-700', HIGH: 'bg-orange-50 text-orange-700', NORMAL: 'bg-slate-100 text-slate-600', LOW: 'bg-slate-50 text-slate-400' }[p])
const slaClass = (s: SlaStatus) => ({ BREACHED: 'bg-red-50 text-red-700', AT_RISK: 'bg-amber-50 text-amber-700', OK: 'bg-emerald-50 text-emerald-700', MET: 'bg-emerald-50 text-emerald-700', PAUSED: 'bg-slate-100 text-slate-500', NONE: 'bg-slate-50 text-slate-300' }[s])
const deviceClass = (s: string) => ({ IN_STOCK: 'bg-emerald-50 text-emerald-700', ASSIGNED: 'bg-sky-50 text-sky-700', REPAIR: 'bg-amber-50 text-amber-700', RETIRED: 'bg-slate-100 text-slate-400' }[s] || '')

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

async function loadQueue() {
  loading.value = true
  try {
    const f = filter.value
    queue.value = await api.deskTickets(props.teamId, {
      status: ['WAITING_USER', 'RESOLVED', 'CLOSED'].includes(f) ? f : undefined,
      assignee: f === 'unassigned' || f === 'me' ? f : undefined,
      overdue: f === 'overdue' || undefined,
    })
  } catch (e) {
    toastError(getErrorMessage(e, '加载工单队列失败'))
  } finally {
    loading.value = false
  }
}

async function loadDevices() {
  try {
    devices.value = await api.deskDevices(props.teamId, deviceFilter.value || undefined)
    stockDevices.value = deviceFilter.value === 'IN_STOCK' ? devices.value : await api.deskDevices(props.teamId, 'IN_STOCK')
  } catch (e) {
    toastError(getErrorMessage(e, '加载设备台账失败'))
  }
}

async function loadSummary() {
  try {
    summary.value = await api.deskSummary(props.teamId)
  } catch (e) {
    toastError(getErrorMessage(e, '加载汇总失败'))
  }
}

function setTab(value: Tab) {
  tab.value = value
  if (value === 'queue') loadQueue()
  else if (value === 'devices') loadDevices()
  else loadSummary()
}

function setFilter(value: string) {
  filter.value = value
  selected.value = null
  loadQueue()
}

function resetForms() {
  waitNote.value = ''
  resolution.value = ''
  commentBody.value = ''
  commentInternal.value = false
  issueDeviceId.value = 0
  reclass.reason = ''
}

function adopt(detail: TicketDetail) {
  selected.value = detail
  reclass.category = detail.category
  reclass.priority = detail.priority
  const row = queue.value.findIndex((t) => t.id === detail.id)
  if (row >= 0) queue.value[row] = { ...queue.value[row], ...detail }
}

async function select(id: number) {
  await guarded(async () => {
    adopt(await api.deskTicket(props.teamId, id))
    resetForms()
    if (selected.value?.category === 'DEVICE') await loadDevices()
  }, '加载工单失败')
}

async function act(request: () => Promise<TicketDetail>, success: string, failure: string) {
  await guarded(async () => {
    adopt(await request())
    toastSuccess(success)
    resetForms()
    await loadQueue()
  }, failure)
}

const take = () => act(() => api.deskAssign(props.teamId, selected.value!.id, { take: true }), '已接单，开始处理', '接单失败')
const assignTo = (value: string) => act(() => api.deskAssign(props.teamId, selected.value!.id, { assignee_user_id: value ? Number(value) : null }), '已更新处理人', '指派失败')
const setStatus = (status: 'IN_PROGRESS' | 'WAITING_USER') => act(
  () => api.deskStatus(props.teamId, selected.value!.id, status, status === 'WAITING_USER' ? waitNote.value.trim() : undefined),
  status === 'WAITING_USER' ? '已通知用户补充信息，处理时限暂停计时' : '已转为处理中', '切换状态失败')
const resolve = () => act(() => api.deskResolve(props.teamId, selected.value!.id, resolution.value.trim()), '已标记解决，等待申请人确认', '解决失败')
const comment = () => act(() => api.deskComment(props.teamId, selected.value!.id, commentBody.value.trim(), commentInternal.value), '已发送', '发送失败')
const reclassify = () => act(() => api.deskReclassify(props.teamId, selected.value!.id, { ...reclass, reason: reclass.reason.trim() }), '已调整分类/优先级', '调整失败')

async function issueDevice() {
  const ticket = selected.value
  if (!ticket || !issueDeviceId.value) return
  await guarded(async () => {
    await api.deskAssignDevice(props.teamId, issueDeviceId.value, { user_id: ticket.requesterUserId, ticket_id: ticket.id })
    toastSuccess('设备已发放，并记录在工单里')
    adopt(await api.deskTicket(props.teamId, ticket.id))
    issueDeviceId.value = 0
    await loadDevices()
  }, '发放设备失败')
}

async function createDevice() {
  await guarded(async () => {
    await api.deskCreateDevice(props.teamId, {
      asset_no: deviceForm.assetNo.trim(), device_type: deviceForm.deviceType, model: deviceForm.model.trim(),
      warranty_until: deviceForm.warrantyUntil || undefined,
    })
    toastSuccess('设备已入库')
    Object.assign(deviceForm, { visible: false, assetNo: '', model: '', warrantyUntil: '' })
    await loadDevices()
  }, '入库失败')
}

async function deviceAction(id: number, action: 'return' | 'repair' | 'repair-done' | 'retire') {
  await guarded(async () => {
    await api.deskDeviceAction(props.teamId, id, action)
    toastSuccess('设备状态已更新')
    history.value = null
    await loadDevices()
  }, '操作失败')
}

async function showHistory(id: number) {
  if (history.value?.id === id) {
    history.value = null
    return
  }
  await guarded(async () => {
    history.value = await api.deskDevice(props.teamId, id)
  }, '加载设备历史失败')
}

async function init() {
  selected.value = null
  try {
    staff.value = await api.deskStaff(props.teamId)
  } catch {
    staff.value = []
  }
  await loadQueue()
}

watch(() => props.teamId, init)
onMounted(init)

// 助手回复里的卡片 → 打开这条记录
useFocusRecord(['ticket'], (id) => {
  void select(id)
  void flashRecord(`[data-testid="desk-row-${id}"]`)
})
</script>
