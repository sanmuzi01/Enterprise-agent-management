<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-sky-200/70 bg-white/80 px-5 py-4 shadow-sm backdrop-blur-xl lg:px-8">
      <div class="mx-auto flex max-w-6xl items-center gap-3">
        <button
          @click="router.push(`/knowledge-spaces/${spaceId}`)"
          class="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-black/[.06] hover:text-slate-900"
          title="返回空间详情"
        >
          <ArrowLeft :size="16" />
        </button>
        <div>
          <h1 class="text-xl font-semibold">知识库调试台</h1>
          <p class="mt-0.5 text-sm text-slate-500">
            {{ space?.name || '知识库空间' }} · 试问一个问题，看命中了哪些资料、答得对不对
          </p>
        </div>
      </div>
    </header>

    <main class="mx-auto grid w-full max-w-6xl flex-1 gap-5 overflow-y-auto p-5 lg:grid-cols-[360px_1fr] lg:p-8">
      <!-- 左：输入 -->
      <section class="space-y-3">
        <div class="rounded-lg border border-sky-200 bg-white/80 p-4">
          <label class="mb-1 block text-xs font-medium text-slate-600">问题</label>
          <textarea
            v-model="query" rows="4" placeholder="例如：年假有多少天？"
            class="w-full resize-none rounded border border-sky-200 px-2 py-1.5 text-sm outline-none focus:border-sky-400"
          ></textarea>

          <div class="mt-3 space-y-2 text-xs">
            <label class="block">
              检索条数（top_k）：{{ topK }}
              <input v-model.number="topK" type="range" min="1" max="20" class="mt-1 w-full" />
            </label>
            <label class="flex items-center justify-between rounded border border-slate-200 px-2 py-1.5">
              <span>重排序（rerank）</span>
              <input v-model="rerank" type="checkbox" class="h-4 w-4" />
            </label>
            <label class="flex items-center justify-between rounded border border-slate-200 px-2 py-1.5">
              <span>查不到就说不知道</span>
              <input v-model="refuseWhenEmpty" type="checkbox" class="h-4 w-4" />
            </label>
            <label class="flex items-center justify-between rounded border border-slate-200 px-2 py-1.5">
              <span>顺便让模型作答</span>
              <input v-model="withAnswer" type="checkbox" class="h-4 w-4" />
            </label>
            <select v-if="withAnswer" v-model="modelName" class="w-full rounded border border-slate-200 px-2 py-1.5">
              <option value="">选择模型…</option>
              <option v-for="c in configs" :key="c.model_name" :value="c.model_name">{{ modelDisplayName(c.model_name) }}</option>
            </select>
          </div>

          <button
            @click="run" :disabled="running || !query.trim() || (withAnswer && !modelName)"
            class="ui-primary mt-3 w-full rounded px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {{ running ? '检索中…' : '跑一次' }}
          </button>
          <p v-if="errorMsg" class="mt-2 text-xs text-red-600">{{ errorMsg }}</p>
        </div>

        <!-- 保存为样例 -->
        <div v-if="trace" class="rounded-lg border border-sky-200 bg-white/80 p-4 text-xs">
          <p class="mb-2 font-semibold text-slate-700">这次结果</p>
          <div class="flex gap-2">
            <button
              v-for="v in (['useful','useless'] as const)" :key="v"
              @click="verdict = verdict === v ? null : v"
              :class="[
                'rounded border px-2 py-1',
                verdict === v ? 'border-sky-400 bg-sky-50 text-sky-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50',
              ]"
            >{{ v === 'useful' ? '有用' : '无用' }}</button>
          </div>
          <label class="mt-2 flex items-center gap-2">
            <input v-model="addToEvalSet" type="checkbox" class="h-4 w-4" />
            同时加入评估集
          </label>
          <button
            @click="save" :disabled="saving"
            class="mt-2 w-full rounded border border-slate-300 px-2 py-1.5 text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >{{ saving ? '保存中…' : '存为测试样例' }}</button>
        </div>
      </section>

      <!-- 右：过程 + 样例列表 -->
      <section class="space-y-5">
        <div class="rounded-lg border border-sky-200 bg-white/80 p-4">
          <div v-if="!trace" class="py-12 text-center text-sm text-slate-400">
            左侧输入问题后「跑一次」，这里会分步展示命中的资料、上下文和回答
          </div>
          <RagTracePanel :trace="trace" />
        </div>

        <div class="rounded-lg border border-sky-200 bg-white/80 p-4">
          <div class="mb-2 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-slate-800">我的测试样例（本空间）</h3>
            <button @click="copyEvalCases" class="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50">
              复制评估集用例 JSON
            </button>
          </div>
          <p v-if="exportMsg" class="mb-2 text-xs text-emerald-600">{{ exportMsg }}</p>
          <div v-if="samples.length === 0" class="py-6 text-center text-xs text-slate-400">还没有保存的样例</div>
          <ul v-else class="divide-y divide-slate-100 text-xs">
            <li v-for="s in samples" :key="s.id" class="flex items-center gap-2 py-2">
              <span class="min-w-0 flex-1 truncate" :title="s.query">{{ s.query }}</span>
              <span v-if="s.verdict" :class="s.verdict === 'useful' ? 'text-emerald-600' : 'text-amber-600'">
                {{ s.verdict === 'useful' ? '有用' : '无用' }}
              </span>
              <span class="text-slate-400">命中 {{ s.hit_count }}</span>
              <label class="flex items-center gap-1 text-slate-500">
                <input type="checkbox" :checked="s.in_eval_set" @change="toggleEval(s)" class="h-3.5 w-3.5" />
                评估集
              </label>
              <button @click="remove(s)" class="rounded px-1.5 py-0.5 text-slate-400 hover:bg-red-50 hover:text-red-600">删</button>
            </li>
          </ul>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { modelDisplayName } from '../../utils/displayNames'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import * as ksApi from '../../api/knowledgeSpace'
import type { KnowledgeSpace } from '../../api/knowledgeSpace'
import * as llmConfigApi from '../../api/llmConfig'
import type { LlmConfig } from '../../api/llmConfig'
import * as ragDebugApi from '../../api/ragDebug'
import type { RagDebugTrace, RagDebugSample } from '../../api/ragDebug'
import RagTracePanel from '../../components/knowledge/RagTracePanel.vue'
import { getErrorMessage } from '../../utils/request'
import { toastSuccess, toastError } from '../../utils/toast'

const props = defineProps<{ id: string | number }>()
const router = useRouter()
const spaceId = computed(() => Number(props.id))

const space = ref<KnowledgeSpace | null>(null)
const configs = ref<LlmConfig[]>([])

const query = ref('')
const topK = ref(5)
const rerank = ref(false)
const refuseWhenEmpty = ref(true)
const withAnswer = ref(false)
const modelName = ref('')

const running = ref(false)
const errorMsg = ref('')
const trace = ref<RagDebugTrace | null>(null)

const verdict = ref<'useful' | 'useless' | null>(null)
const addToEvalSet = ref(false)
const saving = ref(false)

const samples = ref<RagDebugSample[]>([])
const exportMsg = ref('')

const loadSpace = async () => {
  try { space.value = await ksApi.getSpace(spaceId.value) } catch { space.value = null }
}
const loadConfigs = async () => {
  try { configs.value = await llmConfigApi.listConfigs() } catch { configs.value = [] }
}
const loadSamples = async () => {
  try {
    samples.value = (await ragDebugApi.listRagDebugSamples({ space_id: spaceId.value })).items
  } catch { samples.value = [] }
}

const run = async () => {
  running.value = true
  errorMsg.value = ''
  trace.value = null
  verdict.value = null
  addToEvalSet.value = false
  try {
    trace.value = await ragDebugApi.runRagDebug({
      query: query.value.trim(),
      space_ids: [spaceId.value],
      top_k: topK.value,
      rerank: rerank.value,
      refuse_when_empty: refuseWhenEmpty.value,
      with_answer: withAnswer.value,
      model_name: withAnswer.value ? modelName.value : null,
    })
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '检索失败')
  } finally {
    running.value = false
  }
}

const save = async () => {
  if (!trace.value) return
  saving.value = true
  try {
    await ragDebugApi.saveRagDebugSample({
      query: trace.value.query,
      result: trace.value,
      space_ids: [spaceId.value],
      top_k: topK.value,
      rerank: rerank.value,
      verdict: verdict.value,
      in_eval_set: addToEvalSet.value,
    })
    toastSuccess('已存为测试样例')
    await loadSamples()
  } catch (e: any) {
    toastError(getErrorMessage(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

const toggleEval = async (s: RagDebugSample) => {
  try {
    await ragDebugApi.patchRagDebugSample(s.id, { in_eval_set: !s.in_eval_set })
    await loadSamples()
  } catch (e: any) {
    toastError(getErrorMessage(e, '更新失败'))
  }
}

const remove = async (s: RagDebugSample) => {
  if (!confirm('删除这个样例？')) return
  try {
    await ragDebugApi.deleteRagDebugSample(s.id)
    await loadSamples()
  } catch (e: any) {
    toastError(getErrorMessage(e, '删除失败'))
  }
}

const copyEvalCases = async () => {
  try {
    const { cases } = await ragDebugApi.exportRagEvalCases({ space_id: spaceId.value })
    await navigator.clipboard.writeText(JSON.stringify(cases, null, 2))
    exportMsg.value = `已复制 ${cases.length} 条评估用例到剪贴板，可粘贴到「RAG 评估」`
  } catch (e: any) {
    toastError(getErrorMessage(e, '导出失败'))
  }
}

onMounted(() => {
  loadSpace()
  loadConfigs()
  loadSamples()
})
</script>
