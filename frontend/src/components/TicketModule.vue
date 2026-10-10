<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="ticket-module">
    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">IT 服务 · 我的工单</h2>
        <p class="mt-0.5 text-xs text-slate-400">故障、账号、权限、设备申请。先看看有没有自助排查办法，再提交工单。</p>
      </div>
      <button @click="form.visible = !form.visible" class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700" data-testid="ticket-new">
        <Plus :size="13" /> 新建工单
      </button>
    </div>

    <!-- 新建工单：边写边给自助建议和分类建议 -->
    <div v-if="form.visible" class="space-y-3 border-b border-slate-100 bg-slate-50 px-4 py-3" data-testid="ticket-form">
      <div>
        <label for="ticket-desc" class="mb-1 block text-xs text-slate-500">遇到了什么问题或需要什么服务（现象、位置、影响范围）</label>
        <textarea id="ticket-desc" v-model="form.description" rows="3" maxlength="2000" placeholder="例如：三楼打印机卡纸后一直脱机，整个部门都没法打印"
          class="w-full rounded border border-slate-200 px-2 py-1.5 text-sm"></textarea>
      </div>

      <button v-if="!selfSession" @click="findHelp" :disabled="busy || form.description.trim().length < 4" data-testid="ticket-self-help"
        class="rounded border border-emerald-300 bg-white px-2.5 py-1 text-xs text-emerald-800 hover:bg-emerald-50 disabled:opacity-50">先找找自助办法</button>
      <div v-if="selfSession" class="space-y-2" data-testid="ticket-suggestion">
        <div v-if="selfSession.articles.length" class="rounded border border-emerald-200 bg-emerald-50 px-3 py-2">
          <p class="text-xs font-medium text-emerald-800">先试试这些自助办法</p>
          <details v-for="a in selfSession.articles" :key="a.id" class="mt-1 text-xs text-emerald-900" @toggle="onToggle($event, a.id)">
            <summary class="cursor-pointer select-none">{{ a.title }}</summary>
            <p class="mt-1 whitespace-pre-line pl-3">{{ a.steps }}</p>
            <p class="mt-1 flex gap-1.5 pl-3">
              <span class="text-emerald-700">这篇有用吗？</span>
              <button :disabled="rated[a.id] !== undefined" @click="onRate(a.id, true)" class="rounded border border-emerald-300 px-1.5 hover:bg-white disabled:opacity-50">{{ rated[a.id] === true ? '已评价：有用' : '有用' }}</button>
              <button :disabled="rated[a.id] !== undefined" @click="onRate(a.id, false)" class="rounded border border-emerald-300 px-1.5 hover:bg-white disabled:opacity-50">{{ rated[a.id] === false ? '已评价：没用' : '没用' }}</button>
            </p>
          </details>
        </div>
        <p v-else class="text-xs text-slate-500">没有找到相关的自助办法，请直接提交工单。</p>
        <div v-if="selfSession.classification_detail" class="flex flex-wrap items-center gap-2 text-xs text-slate-600">
          <span>系统判断：<b>{{ selfSession.classification_detail.categoryLabel }}</b> · 优先级 <b>{{ selfSession.classification_detail.priorityLabel }}</b></span>
          <span v-if="selfSession.classification_detail.reasons?.length" class="text-slate-400">（{{ selfSession.classification_detail.reasons.join('；') }}）</span>
          <button @click="adoptSuggestion" class="rounded border border-indigo-200 px-2 py-0.5 text-indigo-700 hover:bg-indigo-50" data-testid="ticket-adopt">采用建议</button>
        </div>
        <div v-if="selfSession.articles.length" class="flex flex-wrap items-center gap-2 text-xs">
          <button :disabled="busy" @click="onSolved" data-testid="ticket-self-solved"
            class="rounded bg-emerald-600 px-2.5 py-1 text-white hover:bg-emerald-700 disabled:opacity-50">已解决</button>
          <span class="text-slate-500">没解决就在下面点“没有解决，创建工单”，会和这次自助记录关联。</span>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-2 sm:grid-cols-[150px_110px_1fr]">
        <div>
          <label for="ticket-category" class="mb-1 block text-xs text-slate-500">类型</label>
          <select id="ticket-category" v-model="form.category" class="w-full rounded border border-slate-200 px-2 py-1.5 text-sm">
            <option v-for="(label, key) in CATEGORY_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div>
          <label for="ticket-priority" class="mb-1 block text-xs text-slate-500">优先级</label>
          <select id="ticket-priority" v-model="form.priority" class="w-full rounded border border-slate-200 px-2 py-1.5 text-sm">
            <option v-for="(label, key) in PRIORITY_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div>
          <label for="ticket-title" class="mb-1 block text-xs text-slate-500">标题</label>
          <input id="ticket-title" v-model="form.title" maxlength="120" placeholder="一句话概括" class="w-full rounded border border-slate-200 px-2 py-1.5 text-sm" />
        </div>
      </div>
      <p v-if="needsApproval" class="rounded bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800" data-testid="ticket-approval-note">
        「{{ CATEGORY_LABELS[form.category] }}」提交后需要先由本部门负责人批准，批准后 IT 才会处理。
      </p>
      <div class="flex gap-2">
        <button @click="submit" :disabled="form.submitting || !canSubmit" class="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50" data-testid="ticket-submit">{{ selfSession?.articles.length ? '没有解决，创建工单' : '提交工单' }}</button>
        <button @click="form.visible = false" class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-white">取消</button>
      </div>
    </div>

    <ul class="divide-y divide-slate-100" data-testid="ticket-list">
      <li v-for="t in tickets" :key="t.id">
        <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50" :class="open?.id === t.id ? 'bg-indigo-50/50' : ''"
          @click="toggle(t.id)" :data-testid="`ticket-row-${t.id}`">
          <div class="min-w-0">
            <p class="truncate text-sm text-slate-900">#{{ t.id }} · {{ t.title }}</p>
            <p class="mt-0.5 text-xs text-slate-400">{{ t.categoryLabel }} · 优先级{{ t.priorityLabel }}<span v-if="t.assigneeName"> · 处理人 {{ t.assigneeName }}</span></p>
          </div>
          <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="statusClass(t.status)">{{ t.statusLabel }}</span>
        </button>
      </li>
      <li v-if="!tickets.length && !loading" class="px-4 py-8 text-center text-sm text-slate-400">还没有提交过工单</li>
    </ul>

    <!-- 工单详情与跟进 -->
    <div v-if="open" class="space-y-3 border-t border-slate-200 bg-slate-50/60 px-4 py-4" data-testid="ticket-detail">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-sm font-medium text-slate-900">#{{ open.id }} {{ open.title }}</p>
        <span class="flex items-center gap-2">
          <button v-if="askAgent" @click="askAgent(`分析工单 #${open.id}「${open.title}」：现在卡在哪一步，我接下来该做什么？`)"
            data-testid="ticket-ask-agent" class="rounded border border-indigo-200 px-2 py-0.5 text-xs text-indigo-700 hover:bg-indigo-50">让助手分析</button>
          <span class="rounded px-2 py-0.5 text-xs" :class="statusClass(open.status)">{{ open.statusLabel }}</span>
        </span>
      </div>
      <p class="whitespace-pre-line text-sm text-slate-700">{{ open.description }}</p>
      <p v-if="open.status === 'WAITING_USER'" class="rounded border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">IT 在等你补充信息，请在下面回复。</p>
      <p v-if="open.status === 'REJECTED'" class="rounded border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700">部门负责人未批准<span v-if="open.decisionNote">：{{ open.decisionNote }}</span></p>
      <p v-if="open.status === 'RESOLVED'" class="rounded border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-800">IT 已处理：{{ open.resolution }}。请确认问题是否解决。</p>

      <ol class="space-y-2" data-testid="ticket-timeline">
        <li v-for="c in open.comments" :key="c.id" class="rounded border px-2.5 py-1.5 text-xs" :class="c.kind === 'COMMENT' ? 'border-slate-200 bg-white' : 'border-slate-100 bg-slate-100/70 text-slate-500'">
          <span class="font-medium text-slate-700">{{ c.kind === 'COMMENT' ? (c.authorName || `#${c.authorUserId}`) : '系统' }}</span>
          <span class="ml-2 text-slate-400">{{ c.createdAt.slice(0, 16).replace('T', ' ') }}</span>
          <p class="mt-0.5 whitespace-pre-line">{{ c.body }}</p>
        </li>
      </ol>

      <div v-if="!['CLOSED', 'CANCELLED', 'REJECTED'].includes(open.status)" class="space-y-2">
        <div class="flex gap-2">
          <input v-model="reply" placeholder="补充信息…" class="flex-1 rounded border border-slate-200 px-2 py-1.5 text-xs" data-testid="ticket-reply" @keydown.enter="!isImeEnter($event) && sendReply()" />
          <button @click="sendReply" :disabled="busy || !reply.trim()" class="rounded border border-indigo-200 px-3 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">发送</button>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <template v-if="open.status === 'RESOLVED'">
            <button @click="confirm" :disabled="busy" class="rounded bg-emerald-600 px-3 py-1.5 text-white hover:bg-emerald-700" data-testid="ticket-confirm">已解决，关闭工单</button>
            <input v-model="reopenReason" placeholder="没解决？写下现象" class="min-w-[160px] flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="ticket-reopen-reason" />
            <button @click="reopen" :disabled="busy || reopenReason.trim().length < 2" class="rounded border border-amber-300 px-3 py-1.5 text-amber-800 hover:bg-amber-50 disabled:opacity-50" data-testid="ticket-reopen">重新打开</button>
          </template>
          <button v-else @click="cancel" :disabled="busy" class="rounded border border-red-200 px-3 py-1.5 text-red-700 hover:bg-red-50">撤销工单</button>
        </div>
      </div>
    </div>

    <!-- 我名下的设备 -->
    <div v-if="devices.length" class="border-t border-slate-200 px-4 py-3" data-testid="my-devices">
      <p class="text-xs font-medium text-slate-500">我名下的设备</p>
      <ul class="mt-1 space-y-0.5 text-xs text-slate-600">
        <li v-for="d in devices" :key="d.id">{{ d.assetNo }} · {{ d.model }}<span v-if="d.status === 'REPAIR'" class="ml-1 text-amber-700">（维修中）</span>
          <span v-if="d.warrantyStatus === 'EXPIRING'" class="ml-1 text-amber-700">（保修将到期 {{ d.warrantyUntil }}）</span>
          <span v-else-if="d.warrantyStatus === 'EXPIRED'" class="ml-1 text-slate-400">（已过保）</span></li>
      </ul>
    </div>
  </section>

  <section v-if="showPending" class="rounded-lg border border-slate-200 bg-white" data-testid="ticket-pending">
    <div class="border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">待我批准的 IT 申请</h2>
      <p class="mt-0.5 text-xs text-slate-400">账号、权限、设备申请批准后才会进入 IT 队列。</p>
    </div>
    <ul class="divide-y divide-slate-100">
      <li v-for="t in pending" :key="t.id" class="flex items-center justify-between gap-3 px-4 py-3">
        <div class="min-w-0">
          <p class="truncate text-sm text-slate-900">{{ t.categoryLabel }} · {{ t.title }}</p>
          <p class="mt-0.5 text-xs text-slate-400">申请人 {{ t.requesterName || `#${t.requesterUserId}` }}</p>
        </div>
        <div class="flex shrink-0 gap-1.5">
          <button @click="decide(t.id, 'approve')" class="rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700" :data-testid="`ticket-approve-${t.id}`">批准</button>
          <button @click="decide(t.id, 'reject')" class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">拒绝</button>
        </div>
      </li>
      <li v-if="!pending.length" class="px-4 py-8 text-center text-sm text-slate-400">没有待批准的申请</li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import * as api from '../api/itService'
import type { Device, TicketCategory, TicketDetail, TicketPriority, TicketStatus, TicketSummary } from '../api/itService'
import * as selfApi from '../api/financeExtras'
import type { SelfServiceSession } from '../api/financeExtras'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { flashRecord, useFocusRecord, useAskDeptAgent } from './department/askAgent'

const askAgent = useAskDeptAgent()

const props = defineProps<{ teamId: number }>()

const CATEGORY_LABELS: Record<TicketCategory, string> = { INCIDENT: '故障', ACCOUNT: '账号申请', PERMISSION: '权限申请', DEVICE: '设备申请', OTHER: '咨询/其他' }
const PRIORITY_LABELS: Record<TicketPriority, string> = { LOW: '低', NORMAL: '普通', HIGH: '高', URGENT: '紧急' }
const NEEDS_APPROVAL: TicketCategory[] = ['ACCOUNT', 'PERMISSION', 'DEVICE']

const tickets = ref<TicketSummary[]>([])
const pending = ref<TicketSummary[]>([])
const showPending = ref(false)
const devices = ref<Device[]>([])
const open = ref<TicketDetail | null>(null)
const selfSession = ref<SelfServiceSession | null>(null)
const rated = reactive<Record<number, boolean>>({})
const viewed = new Set<number>()
const loading = ref(false)
const busy = ref(false)
const reply = ref('')
const reopenReason = ref('')
const form = reactive({
  visible: false, submitting: false, description: '', title: '',
  category: 'INCIDENT' as TicketCategory, priority: 'NORMAL' as TicketPriority,
})

const needsApproval = computed(() => NEEDS_APPROVAL.includes(form.category))
const canSubmit = computed(() => form.title.trim().length >= 2 && form.description.trim().length >= 4)

const STATUS_CLASSES: Record<TicketStatus, string> = {
  PENDING_APPROVAL: 'bg-amber-50 text-amber-700', OPEN: 'bg-slate-100 text-slate-600', IN_PROGRESS: 'bg-sky-50 text-sky-700',
  WAITING_USER: 'bg-orange-50 text-orange-700', RESOLVED: 'bg-emerald-50 text-emerald-700', CLOSED: 'bg-slate-100 text-slate-400',
  CANCELLED: 'bg-slate-100 text-slate-400', REJECTED: 'bg-red-50 text-red-700',
}
const statusClass = (s: TicketStatus) => STATUS_CLASSES[s] || 'bg-slate-100 text-slate-500'

// 自助：员工点“先找找自助办法”才建一次自助会话（边打字边建会让统计失真）；看文章、评价、“已解决”都明确记录
async function findHelp() {
  await guarded(async () => {
    selfSession.value = await selfApi.startSelfService(form.description.trim(), props.teamId)
  }, '没有拿到自助办法，可以直接提交工单')
}

function onToggle(event: Event, articleId: number) {
  if (!(event.target as HTMLDetailsElement).open || !selfSession.value || viewed.has(articleId)) return
  viewed.add(articleId)   // 只记“看过”，看过不等于解决
  void selfApi.viewArticle(selfSession.value.id, articleId).catch(() => undefined)
}

async function onRate(articleId: number, helpful: boolean) {
  if (!selfSession.value) return
  await guarded(async () => {
    await selfApi.rateArticle(selfSession.value!.id, articleId, helpful)
    rated[articleId] = helpful
  }, '评价失败')
}

async function onSolved() {
  if (!selfSession.value) return
  await guarded(async () => {
    await selfApi.markSolved(selfSession.value!.id)
    toastSuccess('好的，问题已解决，不用提交工单了')
    resetForm()
  }, '操作失败')
}

function resetForm() {
  Object.assign(form, { visible: false, description: '', title: '', category: 'INCIDENT', priority: 'NORMAL' })
  selfSession.value = null
  viewed.clear()
  for (const key of Object.keys(rated)) delete rated[Number(key)]
}

// 改了问题描述：之前的自助结果不再对应，需要重新找
watch(() => form.description, () => {
  if (selfSession.value && selfSession.value.question !== form.description.trim()) selfSession.value = null
})

function adoptSuggestion() {
  const detail = selfSession.value?.classification_detail
  if (!detail) return
  form.category = detail.category as TicketCategory
  form.priority = detail.priority as TicketPriority
  if (!form.title.trim()) form.title = form.description.trim().replace(/\s+/g, ' ').slice(0, 30)
}

async function loadTickets() {
  loading.value = true
  try {
    tickets.value = await api.getMyTickets()
  } catch (e) {
    toastError(getErrorMessage(e, '加载我的工单失败'))
  } finally {
    loading.value = false
  }
}

async function loadPending() {
  try {
    pending.value = await api.getTeamPendingTickets(props.teamId)
    showPending.value = true
  } catch (e: any) {
    if (e?.response?.status === 403) showPending.value = false
    else toastError(getErrorMessage(e, '加载待批准的 IT 申请失败'))
  }
}

async function loadDevices() {
  try {
    devices.value = await api.getMyDevices()
  } catch {
    devices.value = []
  }
}

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

async function submit() {
  form.submitting = true
  try {
    const payload = { team_id: props.teamId, category: form.category, priority: form.priority,
      title: form.title.trim(), description: form.description.trim() }
    // 先看过自助办法的：工单和这次自助记录关联（自助解决率、转工单率都靠它算）
    const created = selfSession.value
      ? (await selfApi.convertToTicket(selfSession.value.id, payload)).ticket as unknown as TicketDetail
      : await api.createTicket(payload)
    toastSuccess(created.status === 'PENDING_APPROVAL' ? `工单 #${created.id} 已提交，等待部门负责人批准` : `工单 #${created.id} 已提交，等待 IT 接单`)
    resetForm()
    await loadTickets()
    open.value = created
  } catch (e) {
    toastError(getErrorMessage(e, '提交失败'))
  } finally {
    form.submitting = false
  }
}

async function toggle(id: number) {
  if (open.value?.id === id) {
    open.value = null
    return
  }
  await guarded(async () => {
    open.value = await api.getTicket(id, props.teamId)
    reply.value = ''
    reopenReason.value = ''
  }, '加载工单失败')
}

async function refreshOpen(detail: TicketDetail) {
  open.value = detail
  await loadTickets()
}

async function sendReply() {
  if (!open.value || !reply.value.trim()) return
  await guarded(async () => {
    await refreshOpen(await api.commentTicket(open.value!.id, reply.value.trim()))
    reply.value = ''
  }, '发送失败')
}

async function cancel() {
  if (!open.value) return
  await guarded(async () => {
    await refreshOpen(await api.cancelTicket(open.value!.id))
    toastSuccess('工单已撤销')
  }, '撤销失败')
}

async function confirm() {
  if (!open.value) return
  await guarded(async () => {
    await refreshOpen(await api.confirmTicket(open.value!.id))
    toastSuccess('工单已关闭，感谢确认')
  }, '确认失败')
}

async function reopen() {
  if (!open.value) return
  await guarded(async () => {
    await refreshOpen(await api.reopenTicket(open.value!.id, reopenReason.value.trim()))
    reopenReason.value = ''
    toastSuccess('已重新打开，IT 会继续处理')
  }, '重新打开失败')
}

async function decide(id: number, action: 'approve' | 'reject') {
  await guarded(async () => {
    await api.decideTicket(id, { team_id: props.teamId, action })
    toastSuccess(action === 'approve' ? '已批准，工单进入 IT 队列' : '已拒绝')
    await Promise.all([loadPending(), loadTickets()])
  }, '处理失败')
}

function init() {
  open.value = null
  form.visible = false
  loadTickets()
  loadPending()
  loadDevices()
}

watch(() => props.teamId, init)
onMounted(init)

// 助手回复里的卡片 → 打开这条记录
useFocusRecord(['ticket'], (id) => {
  if (open.value?.id !== id) void toggle(id)
  void flashRecord(`[data-testid="ticket-row-${id}"]`)
})
</script>
