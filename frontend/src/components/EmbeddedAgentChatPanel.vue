<template>
  <div class="flex flex-col" :style="{ height: messages.length || !compactWhenEmpty ? height : 'auto' }">
    <div ref="listRef" class="flex-1 space-y-4 overflow-y-auto px-4 py-3" data-testid="agent-messages">
      <p v-if="!messages.length && emptyHint" class="py-6 text-center text-xs text-slate-400">{{ emptyHint }}</p>

      <div v-for="(m, i) in messages" :key="'m-' + i" class="flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
        <div v-if="m.role === 'user'" class="max-w-[85%] whitespace-pre-wrap rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white">{{ m.content }}</div>

        <div v-else class="w-full max-w-[92%] space-y-2">
          <!-- 执行过程：让用户看到助手在“办事”，而不只是在回答 -->
          <ol v-if="m.steps.length" class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs" data-testid="agent-steps">
            <li v-for="(s, j) in m.steps" :key="j" class="flex items-center gap-1.5">
              <span v-if="j" class="text-slate-300">→</span>
              <span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5" :class="stepClass(s.state)">
                <Loader2 v-if="s.state === 'running'" :size="11" class="animate-spin" />
                <Check v-else-if="s.state === 'done'" :size="11" />
                <X v-else-if="s.state === 'error'" :size="11" />
                <Hand v-else-if="s.state === 'confirm'" :size="11" />
                {{ s.label }}
              </span>
            </li>
          </ol>

          <!-- 业务结果卡片：报销草稿、请假单、工单…… -->
          <div v-if="cardsOf(m).cards.length" class="grid gap-2 sm:grid-cols-2">
            <AgentResultCard v-for="(c, k) in cardsOf(m).cards" :key="`${c.kind}-${c.id ?? k}`" :card="c"
              @open="(target) => emit('open', target)" @changed="emit('changed')" @ask="(text) => send(text)" />
          </div>
          <p v-if="cardsOf(m).hidden" class="text-xs text-slate-400">还有 {{ cardsOf(m).hidden }} 条记录没有展开，可以到工作台里查看全部。</p>

          <!-- 高风险操作：必须由人确认才执行 -->
          <div v-for="(c, k) in m.confirmations" :key="'c-' + k"
            class="space-y-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
            <div class="flex items-center gap-1.5 font-semibold">
              <AlertTriangle :size="14" class="shrink-0" />
              需要你确认：{{ toolDisplayName(c.name) }}
            </div>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-amber-800">
              <template v-for="(v, key) in c.args" :key="key">
                <dt class="text-amber-600">{{ argLabel(String(key)) }}</dt>
                <dd>{{ typeof v === 'object' ? JSON.stringify(v) : v }}</dd>
              </template>
            </dl>
            <div v-if="!c.decision" class="flex gap-2">
              <button :disabled="c.deciding" @click="decide(c, true)"
                class="rounded-full bg-amber-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-50">确认执行</button>
              <button :disabled="c.deciding" @click="decide(c, false)"
                class="rounded-full border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-800 disabled:opacity-50">取消</button>
            </div>
            <div v-else-if="c.decision === 'confirmed'" class="text-emerald-700">已确认执行<span v-if="c.resultText">：{{ short(c.resultText, 200) }}</span></div>
            <div v-else class="text-slate-500">已取消，未执行</div>
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
        @keydown.enter="send()"
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
import { nextTick, ref, watch } from 'vue'
import { AlertTriangle, Check, Hand, Loader2, X } from 'lucide-vue-next'
import * as chatApi from '../api/chat'
import AgentResultCard from './department/AgentResultCard.vue'
import { buildMessageCards, toolError, type CardTarget, type MessageCards, type ToolResultInput } from '../utils/agentCards'
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

type StepState = 'running' | 'done' | 'error' | 'confirm'
interface Step { name: string; label: string; state: StepState }
interface Confirmation {
  name: string; token: string; args: Record<string, unknown>
  decision: 'confirmed' | 'rejected' | null; resultText: string; deciding: boolean
}
interface Message {
  role: 'user' | 'assistant'
  content: string
  steps: Step[]
  results: ToolResultInput[]
  confirmations: Confirmation[]
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

// 会改业务数据的工具：成功后通知页面刷新（只读查询不触发）
const WRITE_PREFIXES = ['create_', 'submit_', 'approve_', 'reject_', 'update_', 'confirm_', 'accept_', 'cancel_', 'generate_', 'record_', 'report_']
const isWriteTool = (name: string) => WRITE_PREFIXES.some((p) => name.startsWith(p))

const ARG_LABELS: Record<string, string> = {
  request_id: '单号', ticket_id: '工单号', note: '备注', reason: '原因', decision: '决定', amount: '金额',
  days: '天数', start_date: '开始日期', end_date: '结束日期', customer_id: '客户', content: '内容',
}
const argLabel = (key: string) => ARG_LABELS[key] || '参数'

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

function stepClass(state: StepState) {
  return {
    running: 'bg-indigo-50 text-indigo-700',
    done: 'bg-emerald-50 text-emerald-700',
    error: 'bg-red-50 text-red-700',
    confirm: 'bg-amber-50 text-amber-800',
  }[state]
}

const scrollToBottom = async () => {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

async function decide(c: Confirmation, approve: boolean) {
  if (c.deciding || c.decision) return
  c.deciding = true
  try {
    if (approve) {
      const res = await chatApi.confirmToolCall(c.token)
      c.decision = 'confirmed'
      c.resultText = res.result || ''
      emit('changed')
    } else {
      await chatApi.rejectToolCall(c.token)
      c.decision = 'rejected'
    }
  } catch {
    c.resultText = '处理失败，请重试'
  } finally {
    c.deciding = false
  }
}

/** 发送一句话。工作台上的建议、业务记录上的“让助手分析”都走这里。 */
async function send(text?: string) {
  const msg = (text ?? inputText.value).trim()
  if (!msg || loading.value) return
  loading.value = true
  messages.value.push({ role: 'user', content: msg, steps: [], results: [], confirmations: [] })
  inputText.value = ''
  messages.value.push({ role: 'assistant', content: '', steps: [{ name: 'understand', label: '理解需求', state: 'running' }], results: [], confirmations: [] })
  // 必须从响应式数组里取回来再改，直接改上面那个字面量对象不会触发界面更新
  const reply = messages.value[messages.value.length - 1]
  let wrote = false
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
          const step = [...reply.steps].reverse().find((s) => s.name === name && s.state === 'running')
          const pending = chatApi.parseConfirmationRequired(evt.result)
          if (pending) {
            if (step) step.state = 'confirm'
            reply.confirmations.push({
              name, token: pending.confirmation_token, args: pending.tool_args || {},
              decision: null, resultText: '', deciding: false,
            })
          } else {
            const failed = toolError(evt.result)
            if (step) step.state = failed ? 'error' : 'done'
            reply.results.push({ name, result: evt.result || '' })
            if (!failed && isWriteTool(name)) wrote = true
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
          reply.content = '出错了：' + (evt.message || evt.detail || '请稍后重试')
        }
        scrollToBottom()
      },
    })
  } catch {
    if (!reply.content) reply.content = '发送失败，请稍后重试'
  } finally {
    finishUnderstanding()
    for (const s of reply.steps) if (s.state === 'running') s.state = 'done'
    if (reply.confirmations.some((c) => !c.decision)) {
      reply.steps.push({ name: 'wait', label: '等待你确认', state: 'confirm' })
    }
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
