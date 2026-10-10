<template>
  <div class="flex flex-col" :style="{ height: messages.length || !compactWhenEmpty ? height : 'auto' }">
    <div ref="listRef" class="flex-1 space-y-4 overflow-y-auto px-4 py-3" data-testid="agent-messages">
      <p v-if="!messages.length && emptyHint" class="py-6 text-center text-xs text-slate-400">{{ emptyHint }}</p>

      <div v-for="(m, i) in messages" :key="'m-' + i" class="flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
        <div v-if="m.role === 'user'" class="max-w-[85%] whitespace-pre-wrap rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white">{{ m.content }}</div>

        <div v-else class="w-full max-w-[92%] space-y-2">
          <!-- 执行过程：让用户看到助手在“办事”，而不只是在回答 -->
          <ol v-if="m.steps.length" class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs" data-testid="agent-steps">
            <li v-for="(s, j) in m.steps" :key="j" class="flex items-center gap-1.5" :data-state="s.state">
              <span v-if="j" class="text-slate-300">→</span>
              <span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5" :class="stepClass(s.state)" :title="s.error || ''">
                <Loader2 v-if="s.state === 'running'" :size="11" class="animate-spin" />
                <Check v-else-if="s.state === 'done'" :size="11" />
                <X v-else-if="s.state === 'error'" :size="11" />
                <Hand v-else-if="s.state === 'confirm'" :size="11" />
                <Minus v-else-if="s.state === 'skipped'" :size="11" />
                {{ s.label }}
              </span>
            </li>
          </ol>

          <!-- 结果小结：办成了什么、做了几步、还要你处理什么、哪一步没成功 -->
          <div v-if="m.finished && hasSummary(m)" class="space-y-1 rounded-lg border px-3 py-2 text-xs" data-testid="agent-summary"
            :class="summaryOf(m).failed.length ? 'border-red-200 bg-red-50/50' : 'border-emerald-200 bg-emerald-50/50'">
            <p v-if="summaryOf(m).done.length" class="text-emerald-800" data-testid="agent-summary-done">
              <span class="font-medium">已办成：</span>{{ summaryOf(m).done.join('；') }}
            </p>
            <p v-if="summaryOf(m).stepCount" class="text-slate-500">替你完成了 {{ summaryOf(m).stepCount }} 步（{{ doneStepLabels(m) }}）</p>
            <p v-if="summaryOf(m).failed.length" class="text-red-700" data-testid="agent-summary-failed">
              <span class="font-medium">没有办成：</span>{{ summaryOf(m).failed.join('；') }}
            </p>
            <div v-if="summaryOf(m).attention.length" class="text-amber-800" data-testid="agent-summary-attention">
              <span class="font-medium">需要你处理：</span>
              <ul class="ml-4 list-disc">
                <li v-for="a in summaryOf(m).attention" :key="a">{{ a }}</li>
              </ul>
            </div>
          </div>

          <!-- 业务结果卡片：报销草稿、请假单、工单…… -->
          <div v-if="cardsOf(m).cards.length" class="grid gap-2 sm:grid-cols-2">
            <AgentResultCard v-for="(c, k) in cardsOf(m).cards" :key="`${c.kind}-${c.id ?? k}`" :card="c"
              @open="(target) => emit('open', target)" @changed="emit('changed')" @ask="(text) => send(text)" />
          </div>
          <p v-if="cardsOf(m).hidden" class="text-xs text-slate-400">还有 {{ cardsOf(m).hidden }} 条记录没有展开，可以到工作台里查看全部。</p>

          <!-- 高风险操作：必须由人确认才执行 -->
          <div v-for="(c, k) in m.confirmations" :key="'c-' + k" data-testid="agent-confirmation"
            class="space-y-2 rounded-lg border px-3 py-2.5 text-xs"
            :class="c.decision === 'confirmed' ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
              : c.decision === 'failed' ? 'border-red-200 bg-red-50 text-red-900'
              : c.decision === 'rejected' ? 'border-slate-200 bg-slate-50 text-slate-600'
              : 'border-amber-300 bg-amber-50 text-amber-900'">
            <div class="flex items-center gap-1.5 font-semibold">
              <AlertTriangle v-if="!c.decision" :size="14" class="shrink-0" />
              {{ c.decision ? toolDisplayName(c.name) : `需要你确认：${toolDisplayName(c.name)}` }}
            </div>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 opacity-90">
              <template v-for="(v, key) in c.args" :key="key">
                <dt class="opacity-70">{{ argLabel(String(key)) }}</dt>
                <dd>{{ argValue(String(key), v) }}</dd>
              </template>
            </dl>
            <div v-if="!c.decision" class="flex gap-2">
              <button :disabled="c.deciding" @click="decide(m, c, true)" data-testid="agent-confirm"
                class="rounded-full bg-amber-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-50">
                {{ c.deciding ? '执行中…' : '确认执行' }}</button>
              <button :disabled="c.deciding" @click="decide(m, c, false)"
                class="rounded-full border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-800 disabled:opacity-50">取消</button>
            </div>
            <p v-else-if="c.decision === 'confirmed'" class="flex items-center gap-1 font-medium" data-testid="agent-confirm-done">
              <Check :size="12" /> 已执行成功<span v-if="c.resultRef">：{{ c.resultRef }}</span>
            </p>
            <p v-else-if="c.decision === 'failed'" class="font-medium" data-testid="agent-confirm-failed">没有执行成功：{{ c.resultText }}</p>
            <p v-else>已取消，没有执行</p>
          </div>

          <div v-if="m.content || (loading && i === messages.length - 1)"
            class="md-body rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-800">
            <div v-if="m.content" v-html="renderMarkdown(m.content)"></div>
            <span v-else class="text-slate-400">正在处理…</span>
          </div>

          <details v-if="m.results.length" class="text-xs text-slate-400">
            <summary class="cursor-pointer select-none hover:text-slate-600">查看执行详情</summary>
            <ul class="mt-1 space-y-1">
              <li v-for="(r, k) in m.results" :key="k" class="break-all rounded bg-slate-50 px-2 py-1 font-mono">
                {{ toolDisplayName(r.name) }}：{{ short(r.result, 300) }}
              </li>
            </ul>
          </details>
        </div>
      </div>
    </div>

    <div class="flex gap-2 border-t border-slate-200 p-3">
      <input
        ref="inputRef"
        v-model="inputText"
        @keydown.enter="!isImeEnter($event) && send()"
        :disabled="loading"
        :placeholder="placeholder"
        data-testid="agent-input"
        class="flex-1 rounded border border-slate-200 px-3 py-2 text-sm"
      />
      <button @click="send()" :disabled="loading || !inputText.trim()" data-testid="agent-send"
        class="rounded bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">发送</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { nextTick, ref, watch } from 'vue'
import { AlertTriangle, Check, Hand, Loader2, Minus, X } from 'lucide-vue-next'
import * as chatApi from '../api/chat'
import AgentResultCard from './department/AgentResultCard.vue'
import { buildMessageCards, cardFromRecord, parseJson, toolError, type CardTarget, type MessageCards, type ToolResultInput } from '../utils/agentCards'
import { isWriteTool, summarizeReply, type ReplySummary, type SummaryStep } from '../utils/agentSummary'
import { toolDisplayName } from '../utils/displayNames'
import { renderMarkdown } from '../utils/markdown'

const props = withDefaults(defineProps<{
  agentId: number
  height?: string
  placeholder?: string
  emptyHint?: string
  /** 还没有对话时只占一行输入框的高度（部门工作台顶部常驻用） */
  compactWhenEmpty?: boolean
}>(), {
  height: '480px',
  compactWhenEmpty: false,
  placeholder: '跟部门助手说点什么…',
  emptyHint: '说说你要办的事，助手会查数据、起草单据，需要你确认的地方会停下来等你。',
})

const emit = defineEmits<{
  /** 助手或卡片改了业务数据（建了草稿、提交了单子……），页面据此刷新业务模块 */
  (e: 'changed'): void
  /** 用户想去工作台里看这张单子 */
  (e: 'open', target: CardTarget): void
}>()

type Step = SummaryStep
interface Confirmation {
  name: string; token: string; args: Record<string, unknown>
  decision: 'confirmed' | 'rejected' | 'failed' | null
  resultText: string
  /** 执行成功后的单号，例如“报销单 #18” */
  resultRef: string
  deciding: boolean
  /** 对应执行过程里的哪一步 */
  step: Step | null
}
interface Message {
  role: 'user' | 'assistant'
  content: string
  steps: Step[]
  results: ToolResultInput[]
  confirmations: Confirmation[]
  /** 这次回复跑完了（成功或失败），才显示结果小结 */
  finished: boolean
}

const messages = ref<Message[]>([])
const inputText = ref('')
const loading = ref(false)
const conversationId = ref<number | null>(null)
const listRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)

// 部门工作台切换部门时这个组件会被复用（同一个实例换 agentId），不会重新挂载——
// 不重置的话上一个部门的聊天记录会原样留在面板里，看起来像串号。
watch(() => props.agentId, () => {
  messages.value = []
  conversationId.value = null
  inputText.value = ''
})

const ARG_LABELS: Record<string, string> = {
  request_id: '单号', ticket_id: '工单号', note: '备注', reason: '原因', decision: '决定', amount: '金额',
  days: '天数', start_date: '开始日期', end_date: '结束日期', customer_id: '客户', content: '内容',
  resolution: '处理结果', case_type: '事项类型', employee_user_id: '员工', employee_team_id: '所在部门',
  target_team_id: '目标部门', effective_date: '生效日期', position: '岗位', case_id: '事项号', task_id: '任务号',
  claim_id: '报销单号', voucher_id: '凭证号', summary: '说明', link: '链接', stage: '阶段',
}
const argLabel = (key: string) => ARG_LABELS[key] || '参数'
const CASE_TYPES: Record<string, string> = { ONBOARDING: '入职', PROBATION: '转正', TRANSFER: '调岗', OFFBOARDING: '离职' }
function argValue(key: string, v: unknown): string {
  if (key === 'case_type' && typeof v === 'string') return CASE_TYPES[v.toUpperCase()] || v
  if (key.endsWith('_id') && (typeof v === 'number' || typeof v === 'string')) return `#${v}`
  return typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')
}

const short = (s: string, n: number) => {
  const s2 = s || ''
  return s2.length > n ? s2.slice(0, n) + '…' : s2
}

// 卡片按消息缓存：消息流式更新时不必每次重算
const cardCache = new WeakMap<Message, { count: number; value: MessageCards }>()
function cardsOf(m: Message): MessageCards {
  const hit = cardCache.get(m)
  if (hit && hit.count === m.results.length) return hit.value
  const value = buildMessageCards(m.results)
  cardCache.set(m, { count: m.results.length, value })
  return value
}

function summaryOf(m: Message): ReplySummary {
  return summarizeReply({
    steps: m.steps,
    results: m.results,
    pendingConfirmations: m.confirmations.filter((c) => !c.decision).map((c) => c.name),
    cards: cardsOf(m).cards,
  })
}
const hasSummary = (m: Message) => {
  const s = summaryOf(m)
  return s.done.length || s.stepCount || s.failed.length || s.attention.length
}
const doneStepLabels = (m: Message) =>
  m.steps.filter((s) => s.state === 'done' && s.name !== 'understand' && s.name !== 'wait').map((s) => s.label).join('、')

function stepClass(state: Step['state']) {
  return {
    running: 'bg-indigo-50 text-indigo-700',
    done: 'bg-emerald-50 text-emerald-700',
    error: 'bg-red-50 text-red-700',
    confirm: 'bg-amber-50 text-amber-800',
    skipped: 'bg-slate-100 text-slate-500',
  }[state]
}

const scrollToBottom = async () => {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

/** 所有待确认的操作都有了结果，就把“等待你确认”这一步收起来 */
function settleWaitStep(m: Message) {
  if (m.confirmations.some((c) => !c.decision)) return
  const wait = m.steps.find((s) => s.name === 'wait')
  if (wait) {
    const ok = m.confirmations.some((c) => c.decision === 'confirmed')
    wait.label = ok ? '已确认执行' : m.confirmations.some((c) => c.decision === 'failed') ? '确认后执行失败' : '已取消'
    wait.state = ok ? 'done' : m.confirmations.some((c) => c.decision === 'failed') ? 'error' : 'skipped'
  }
}

/** 用户点确认 / 取消。确认成功后把执行结果放进这条回复：卡片立刻变成最新状态（比如草稿 → 待审批），步骤打勾。 */
async function decide(m: Message, c: Confirmation, approve: boolean) {
  if (c.deciding || c.decision) return
  c.deciding = true
  try {
    if (approve) {
      const res = await chatApi.confirmToolCall(c.token)
      const failure = toolError(res.result)
      if (failure) {
        c.decision = 'failed'
        c.resultText = failure
        if (c.step) Object.assign(c.step, { state: 'error', error: failure })
      } else {
        c.decision = 'confirmed'
        c.resultText = res.result || ''
        const card = cardFromRecord(parseJson(res.result))
        c.resultRef = card ? card.title : ''
        if (c.step) c.step.state = 'done'
        m.results.push({ name: c.name, result: res.result || '' })
        emit('changed')
      }
    } else {
      await chatApi.rejectToolCall(c.token)
      c.decision = 'rejected'
      if (c.step) Object.assign(c.step, { state: 'skipped', label: `${c.step.label}（已取消）` })
    }
  } catch (e: any) {
    c.decision = 'failed'
    c.resultText = e?.response?.data?.detail || '网络或服务异常，请稍后重试'
    if (c.step) Object.assign(c.step, { state: 'error', error: c.resultText })
  } finally {
    c.deciding = false
    settleWaitStep(m)
  }
}

/** 发送一句话。工作台上的建议、业务记录上的“让助手分析”都走这里。 */
async function send(text?: string) {
  const msg = (text ?? inputText.value).trim()
  if (!msg || loading.value) return
  loading.value = true
  messages.value.push({ role: 'user', content: msg, steps: [], results: [], confirmations: [], finished: true })
  inputText.value = ''
  messages.value.push({
    role: 'assistant', content: '', steps: [{ name: 'understand', label: '理解需求', state: 'running' }],
    results: [], confirmations: [], finished: false,
  })
  // 必须从响应式数组里取回来再改，直接改上面那个字面量对象不会触发界面更新
  const reply = messages.value[messages.value.length - 1]
  let wrote = false
  let failed = false
  const finishUnderstanding = () => {
    const first = reply.steps[0]
    if (first?.name === 'understand' && first.state === 'running') first.state = 'done'
  }
  scrollToBottom()

  try {
    await chatApi.sendStream({
      agentId: props.agentId,
      conversationId: conversationId.value,
      message: msg,
      onEvent: (evt) => {
        if (evt.type === 'retrieval') {
          finishUnderstanding()
          reply.steps.push({ name: 'rag_search', label: evt.hit_count ? `检索资料 · ${evt.hit_count} 条` : '检索资料 · 无匹配', state: 'done' })
        } else if (evt.type === 'tool_call') {
          finishUnderstanding()
          reply.steps.push({ name: evt.name || '', label: toolDisplayName(evt.name), state: 'running' })
        } else if (evt.type === 'tool_result') {
          finishUnderstanding()
          const name = evt.name || ''
          const step = [...reply.steps].reverse().find((s) => s.name === name && s.state === 'running') || null
          const pending = chatApi.parseConfirmationRequired(evt.result)
          if (pending) {
            if (step) step.state = 'confirm'
            reply.confirmations.push({
              name, token: pending.confirmation_token, args: pending.tool_args || {},
              decision: null, resultText: '', resultRef: '', deciding: false, step,
            })
          } else {
            const failure = toolError(evt.result)
            if (step) Object.assign(step, failure ? { state: 'error', error: failure } : { state: 'done' })
            reply.results.push({ name, result: evt.result || '' })
            if (!failure && isWriteTool(name)) wrote = true
          }
        } else if (evt.type === 'answer_delta') {
          finishUnderstanding()
          reply.content += evt.content || ''
        } else if (evt.type === 'answer') {
          finishUnderstanding()
          reply.content = evt.content || ''
        } else if (evt.type === 'done') {
          if (evt.conversation_id) conversationId.value = evt.conversation_id
        } else if (evt.type === 'error') {
          failed = true
          reply.content = '出错了：' + (evt.message || evt.detail || '请稍后重试')
        }
        scrollToBottom()
      },
    })
  } catch {
    failed = true
    if (!reply.content) reply.content = '发送失败，请稍后重试'
  } finally {
    finishUnderstanding()
    // 没跑完的步骤：整次回复失败了就标成“没完成”，不能显示成打勾
    for (const s of reply.steps) {
      if (s.state === 'running') Object.assign(s, failed ? { state: 'error', error: '这一步没有完成' } : { state: 'done' })
    }
    if (failed && !reply.steps.some((s) => s.state === 'error')) {
      reply.steps.push({ name: 'failed', label: '没有完成', state: 'error', error: reply.content.replace(/^出错了：/, '') })
    }
    if (reply.confirmations.some((c) => !c.decision)) {
      reply.steps.push({ name: 'wait', label: '等待你确认', state: 'confirm' })
    }
    reply.finished = true
    loading.value = false
    if (wrote) emit('changed')
    scrollToBottom()
  }
}

defineExpose({
  /** 由页面代用户问一句（建议卡片、业务记录上的“让助手分析”） */
  ask: (text: string) => send(text),
  focus: () => inputRef.value?.focus(),
  busy: () => loading.value,
})
</script>
