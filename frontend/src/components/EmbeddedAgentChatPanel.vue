<template>
  <div class="flex flex-col" style="height: 480px">
    <div ref="listRef" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
      <div v-for="(m, i) in messages" :key="'m-' + i" class="flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
        <div
          class="max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap"
          :class="m.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-800'"
        >{{ m.content || (loading && i === messages.length - 1 ? '…' : '') }}</div>
      </div>

      <div v-for="(evt, i) in eventTraces" :key="'evt-' + i" class="flex justify-start">
        <div
          v-if="evt.type === 'confirmation_required'"
          class="max-w-[90%] w-full rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs text-amber-900 space-y-2"
        >
          <div class="flex items-center gap-1.5 font-semibold">
            <AlertTriangle :size="14" class="shrink-0" />
            高风险操作待确认：{{ evt.name }}
          </div>
          <pre class="text-[11px] text-amber-800 whitespace-pre-wrap">{{ JSON.stringify(evt.args, null, 2) }}</pre>
          <div v-if="!evt.decision" class="flex gap-2">
            <button
              :disabled="evt.deciding"
              @click="decideToolConfirmation(evt, true)"
              class="rounded-full bg-amber-600 px-3 py-1 text-white text-xs font-medium disabled:opacity-50"
            >确认执行</button>
            <button
              :disabled="evt.deciding"
              @click="decideToolConfirmation(evt, false)"
              class="rounded-full bg-white border border-amber-300 px-3 py-1 text-amber-800 text-xs font-medium disabled:opacity-50"
            >取消</button>
          </div>
          <div v-else-if="evt.decision === 'confirmed'" class="text-emerald-700">
            已确认执行<span v-if="evt.resultText">：{{ short(evt.resultText, 200) }}</span>
          </div>
          <div v-else class="text-gray-500">已取消，未执行</div>
        </div>
        <div v-else class="max-w-[85%] rounded-lg bg-slate-50 border border-slate-200 px-3 py-2 text-xs text-slate-500 font-mono">
          <span v-if="evt.type === 'tool_call'"><span class="text-blue-500 font-semibold">调用工具</span> {{ evt.name }}</span>
          <span v-else-if="evt.type === 'tool_result'"><span class="text-green-500 font-semibold">工具结果</span> {{ evt.name }}：{{ short(evt.result, 150) }}</span>
        </div>
      </div>
    </div>

    <div class="border-t border-slate-200 p-3 flex gap-2">
      <input
        v-model="inputText"
        @keydown.enter="sendMessage"
        :disabled="loading"
        placeholder="跟部门助手说点什么…"
        class="flex-1 rounded border border-slate-200 px-3 py-2 text-sm"
      />
      <button
        @click="sendMessage"
        :disabled="loading || !inputText.trim()"
        class="rounded bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50"
      >发送</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import * as chatApi from '../api/chat'

const props = defineProps<{ agentId: number }>()

const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const eventTraces = ref<any[]>([])
const inputText = ref('')
const loading = ref(false)
const conversationId = ref<number | null>(null)
const listRef = ref<HTMLElement | null>(null)

// 部门工作台切换部门时这个组件会被复用（同一个实例换 agentId），不会重新挂载——
// 不重置的话上一个部门的聊天记录会原样留在面板里，看起来像串号。
watch(() => props.agentId, () => {
  messages.value = []
  eventTraces.value = []
  conversationId.value = null
  inputText.value = ''
})

const short = (s: string, n: number) => {
  const s2 = s || ''
  return s2.length > n ? s2.slice(0, n) + '...' : s2
}

const scrollToBottom = async () => {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

/** 跟 Chat.vue 里的同名函数逻辑一致（第五轮审计 P0-2 的确认卡片）——这里独立
 * 一份，不共享状态，是因为这个面板是嵌在别的页面里的轻量组件，不依赖
 * Chat.vue 的会话侧栏/运行轨迹抽屉那一整套。 */
async function decideToolConfirmation(evt: any, approve: boolean) {
  if (evt.deciding || evt.decision) return
  evt.deciding = true
  try {
    if (approve) {
      const res = await chatApi.confirmToolCall(evt.token)
      evt.decision = 'confirmed'
      evt.resultText = res.result || ''
    } else {
      await chatApi.rejectToolCall(evt.token)
      evt.decision = 'rejected'
    }
  } catch (e: any) {
    evt.resultText = '处理失败，请重试'
  } finally {
    evt.deciding = false
  }
}

async function sendMessage() {
  const msg = inputText.value.trim()
  if (!msg || loading.value) return
  loading.value = true
  eventTraces.value = []
  messages.value.push({ role: 'user', content: msg })
  inputText.value = ''
  const placeholderIdx = messages.value.length
  messages.value.push({ role: 'assistant', content: '' })
  scrollToBottom()

  try {
    await chatApi.sendStream({
      agentId: props.agentId,
      conversationId: conversationId.value,
      message: msg,
      onEvent: (evt) => {
        if (evt.type === 'tool_call') {
          eventTraces.value.push({ type: 'tool_call', name: evt.name, args: evt.args || {} })
        } else if (evt.type === 'tool_result') {
          const pending = chatApi.parseConfirmationRequired(evt.result)
          if (pending) {
            eventTraces.value.push({
              type: 'confirmation_required', name: evt.name, token: pending.confirmation_token,
              args: pending.tool_args, decision: null, resultText: '', deciding: false,
            })
          } else {
            eventTraces.value.push({ type: 'tool_result', name: evt.name, result: evt.result || '' })
          }
        } else if (evt.type === 'answer_delta') {
          messages.value[placeholderIdx].content += evt.content || ''
        } else if (evt.type === 'answer') {
          messages.value[placeholderIdx].content = evt.content || ''
        } else if (evt.type === 'done') {
          if (evt.conversation_id) conversationId.value = evt.conversation_id
        } else if (evt.type === 'error') {
          messages.value[placeholderIdx].content = '出错了：' + (evt.message || evt.detail || '请稍后重试')
        }
        scrollToBottom()
      },
    })
  } catch (e: any) {
    messages.value[placeholderIdx].content = '发送失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>
