<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-sky-200/70 ui-glass px-5 py-3 shadow-sm backdrop-blur-xl lg:px-8">
      <AgentSubnav :agent-id="agentId" :agent-name="agent?.name" active="knowledge" />
    </header>

    <main class="mx-auto w-full max-w-5xl flex-1 space-y-5 overflow-y-auto p-5 lg:p-8">
      <!-- 绑定的知识库空间 -->
      <section class="rounded-lg border border-sky-200 bg-white/80 p-4">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-slate-900">绑定的知识库空间</h2>
          <button @click="router.push('/knowledge-spaces')"
            class="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50">
            去知识库中心管理
          </button>
        </div>
        <p class="mb-3 text-xs text-slate-500">
          回答时会从这些空间联合检索并标注来源。绑定关系在「工作台 → 编辑助手 → 知识库」里调整。
        </p>
        <div v-if="boundSpaces.length" class="space-y-2">
          <button
            v-for="s in boundSpaces"
            :key="s.id"
            @click="router.push(`/knowledge-spaces/${s.id}`)"
            class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] flex w-full items-center justify-between rounded border border-slate-200 bg-white px-3 py-2 text-left text-sm"
          >
            <span class="min-w-0">
              <span class="block truncate font-medium text-slate-800">{{ s.name }}</span>
              <span class="block truncate text-xs text-slate-400">{{ s.purpose_label }} · {{ s.doc_count }} 份资料 · 健康分 {{ s.health_score ?? '—' }}</span>
            </span>
            <span class="shrink-0 text-xs text-sky-600">打开 →</span>
          </button>
        </div>
        <div v-else class="rounded border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-400">
          还没有绑定知识库空间。{{ privateDocCount > 0 ? `本助手另有 ${privateDocCount} 份历史私有资料仍可检索。` : '' }}
        </div>

        <div class="mt-3 flex flex-wrap gap-1.5 text-[11px]">
          <span class="rounded bg-slate-100 px-2 py-0.5 text-slate-600">检索条数 {{ agent?.kb_top_k ?? 5 }}</span>
          <span v-if="agent?.kb_rerank_enabled" class="rounded bg-violet-50 px-2 py-0.5 text-violet-700">重排序</span>
          <span v-if="agent?.kb_force_citation" class="rounded bg-sky-50 px-2 py-0.5 text-sky-700">回答带来源</span>
          <span v-if="agent?.kb_refuse_when_empty" class="rounded bg-amber-50 px-2 py-0.5 text-amber-700">查不到就说不知道</span>
          <span v-if="!agent?.rag_enabled" class="rounded bg-red-50 px-2 py-0.5 text-red-600">未开启知识库检索</span>
        </div>
      </section>

      <!-- 快速检索测试 -->
      <section class="rounded-lg border border-sky-200 bg-white/80 p-4">
        <h2 class="mb-2 text-sm font-semibold text-slate-900">快速检索测试</h2>
        <p class="mb-2 text-xs text-slate-500">按本助手实际的绑定和配置跑一次检索，看会命中哪些资料。</p>
        <div class="flex gap-2">
          <input
            v-model="query" placeholder="例如：报销标准是多少？" @keydown.enter="!isImeEnter($event) && runTest()"
            class="h-9 flex-1 rounded border border-slate-300 px-2 text-sm outline-none focus:border-sky-400"
          />
          <button
            @click="runTest" :disabled="testing || !query.trim()"
            class="ui-primary h-9 rounded px-3 text-sm font-medium text-white disabled:opacity-40"
          >{{ testing ? '检索中…' : '试一下' }}</button>
        </div>
        <p v-if="testErr" class="mt-2 text-xs text-red-600">{{ testErr }}</p>
        <div v-if="trace" class="mt-3">
          <RagTracePanel :trace="trace" />
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AgentSubnav from '../components/agent/AgentSubnav.vue'
import RagTracePanel from '../components/knowledge/RagTracePanel.vue'
import { getAgent, type AgentInfo } from '../api/agent'
import { listSpaces, type KnowledgeSpace } from '../api/knowledgeSpace'
import { listDocuments } from '../api/knowledge'
import { runRagDebug, type RagDebugTrace } from '../api/ragDebug'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ agentId: string | number }>()
const router = useRouter()
const agentId = computed(() => Number(props.agentId))

const agent = ref<AgentInfo | null>(null)
const allSpaces = ref<KnowledgeSpace[]>([])
const privateDocCount = ref(0)

const query = ref('')
const testing = ref(false)
const testErr = ref('')
const trace = ref<RagDebugTrace | null>(null)

const boundSpaces = computed(() => {
  const ids = new Set(agent.value?.space_ids || [])
  return allSpaces.value.filter((s) => ids.has(s.id))
})

const load = async () => {
  try {
    agent.value = await getAgent(agentId.value)
  } catch { agent.value = null }
  try {
    allSpaces.value = (await listSpaces()).items
  } catch { allSpaces.value = [] }
  try {
    privateDocCount.value = (await listDocuments(agentId.value)).length
  } catch { privateDocCount.value = 0 }
}

const runTest = async () => {
  testing.value = true
  testErr.value = ''
  trace.value = null
  try {
    trace.value = await runRagDebug({ agent_id: agentId.value, query: query.value.trim(), top_k: agent.value?.kb_top_k || 5 })
  } catch (e: any) {
    testErr.value = getErrorMessage(e, '检索失败')
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>
