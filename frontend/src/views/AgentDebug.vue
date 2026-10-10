<template>
  <div class="h-screen overflow-y-auto bg-transparent">
    <div class="mx-auto max-w-7xl px-4 py-5 lg:px-6">
      <header class="mb-4 rounded-lg border border-sky-200/70 ui-glass px-3 py-2">
        <AgentSubnav :agent-id="agentId" :agent-name="debug?.agent.name" active="debug">
          <template #actions>
            <button @click="runOneClickCheck" :disabled="dryRunLoading" class="inline-flex h-9 items-center gap-2 rounded border border-sky-200 bg-white px-3 text-sm font-medium text-sky-700 hover:bg-sky-50 disabled:opacity-60">
              <ClipboardCheck :size="15" />
              一键体检
            </button>
            <button @click="loadDebug" :disabled="loading" class="ui-primary inline-flex h-9 items-center gap-2 rounded px-3 text-sm font-medium text-white disabled:bg-slate-300 disabled:shadow-none">
              <RefreshCcw :size="15" :class="loading ? 'animate-spin' : ''" />
              刷新
            </button>
          </template>
        </AgentSubnav>
      </header>

      <div v-if="loading && !debug" class="rounded-lg border border-slate-200 bg-white py-16 text-center text-sm text-slate-500">
        加载中...
      </div>
      <div v-else-if="errorMsg" class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {{ errorMsg }}
      </div>

      <template v-else-if="debug">
        <section class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <article v-for="item in readinessCards" :key="item.key" :class="item.tone" class="rounded-lg border p-4">
            <div class="mb-3 flex items-center justify-between gap-2">
              <component :is="item.icon" :size="17" />
              <span class="rounded bg-white/70 px-2 py-0.5 text-[11px] font-medium">{{ item.badge }}</span>
            </div>
            <p class="text-xs opacity-75">{{ item.label }}</p>
            <p class="mt-1 truncate text-sm font-semibold">{{ item.value }}</p>
          </article>
        </section>

        <div class="grid grid-cols-1 gap-4 xl:grid-cols-[220px_minmax(0,1fr)_320px]">
          <aside class="ui-card rounded-lg p-2 xl:sticky xl:top-5 xl:self-start">
            <button
              v-for="item in workbenchTabs"
              :key="item.key"
              @click="activePanel = item.key"
              :class="activePanel === item.key ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-200' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'"
              class="mb-1 flex h-10 w-full items-center gap-2 rounded px-3 text-left text-sm transition-colors"
            >
              <component :is="item.icon" :size="16" />
              <span class="flex-1">{{ item.label }}</span>
            </button>
          </aside>

          <main class="min-w-0 space-y-4">
            <section v-if="activePanel === 'dry-run'" class="ui-card rounded-lg">
              <header class="border-b border-slate-200 px-4 py-3">
                <h2 class="text-sm font-semibold text-slate-900">对话预检</h2>
                <p class="mt-1 text-xs text-slate-500">只检查资料命中和消息组装，不调用大模型，也不写入聊天记录。</p>
              </header>
              <div class="space-y-4 p-4">
                <div class="flex flex-col gap-2 lg:flex-row">
                  <input
                    v-model="dryRunMessage"
                    class="ui-field h-10 min-w-0 flex-1 rounded px-3 text-sm outline-none"
                    placeholder="例如：根据我上传的资料，总结一下项目当前进度"
                    @keydown.enter="!isImeEnter($event) && runDryRun()"
                  />
                  <button
                    @click="runDryRun"
                    :disabled="dryRunLoading || !dryRunMessage.trim()"
                    class="ui-primary inline-flex h-10 items-center justify-center rounded px-4 text-sm font-medium text-white disabled:bg-slate-300 disabled:shadow-none"
                  >
                    {{ dryRunLoading ? '检查中...' : '运行检查' }}
                  </button>
                </div>
                <p v-if="dryRunError" class="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{{ dryRunError }}</p>

                <div v-if="dryRun" class="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <MetricTile label="最终指令" :value="`${dryRun.stats.final_prompt_chars} 字`" />
                  <MetricTile label="消息数量" :value="`${dryRun.stats.message_count} 条`" />
                  <MetricTile label="历史消息" :value="`${dryRun.stats.history_message_count} 条`" />
                  <MetricTile label="资料命中" :value="dryRun.rag.enabled ? `${dryRun.rag.hit_count} 条` : '未启用'" :tone="dryRun.rag.ok ? 'good' : 'bad'" />
                </div>

                <div v-if="dryRun" class="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <section class="rounded border border-slate-200 bg-white">
                    <PanelHeader title="模型消息" action="复制" @action="copyText(JSON.stringify(dryRun.messages, null, 2), '消息内容')" />
                    <div class="max-h-80 overflow-y-auto p-3">
                      <article v-for="(msg, idx) in dryRun.messages" :key="idx" class="mb-2 rounded border border-slate-100 bg-slate-50 p-3">
                        <p class="mb-1 text-xs font-semibold text-slate-500">{{ messageRoleLabel(msg.role) }}</p>
                        <p class="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">{{ short(msg.content, 1200) }}</p>
                      </article>
                    </div>
                  </section>
                  <section class="rounded border border-slate-200 bg-white">
                    <PanelHeader title="命中片段" />
                    <div class="max-h-80 overflow-y-auto p-3">
                      <p v-if="dryRun.rag.error" class="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{{ dryRun.rag.error }}</p>
                      <article v-for="(item, idx) in dryRun.rag.results" :key="idx" class="mb-2 rounded border border-slate-100 bg-slate-50 p-3">
                        <div class="mb-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                          <span class="truncate">{{ item.file_name || `片段 ${idx + 1}` }}</span>
                          <span v-if="typeof item.score === 'number'">{{ (item.score * 100).toFixed(1) }}%</span>
                        </div>
                        <p class="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">{{ short(item.content, 800) }}</p>
                      </article>
                      <div v-if="!dryRun.rag.error && dryRun.rag.results.length === 0" class="py-8 text-center text-sm text-slate-400">没有命中片段</div>
                    </div>
                  </section>
                </div>
              </div>
            </section>

            <section v-if="activePanel === 'evaluation'" class="ui-card rounded-lg">
              <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
                <div>
                  <h2 class="text-sm font-semibold text-slate-900">RAG 评估</h2>
                  <p class="mt-1 text-xs text-slate-500">用标注问题集量化检索质量和答案忠诚度。</p>
                </div>
                <button
                  @click="showEvalResults = !showEvalResults"
                  class="inline-flex h-8 items-center gap-2 rounded border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  <component :is="showEvalResults ? Eye : EyeOff" :size="14" />
                  {{ showEvalResults ? '显示结果' : '隐藏结果' }}
                </button>
              </div>

              <div class="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_260px]">
                <label class="block">
                  <span class="mb-1 block text-xs font-medium text-slate-600">评估集 JSON</span>
                  <textarea
                    v-model="evalCasesText"
                    rows="12"
                    class="ui-field w-full rounded px-3 py-2 font-mono text-xs leading-relaxed outline-none"
                    placeholder="填写评估问题集"
                  ></textarea>
                </label>

                <div class="space-y-3">
                  <label class="block">
                    <span class="mb-1 block text-xs font-medium text-slate-600">Top K</span>
                    <input v-model.number="evalTopK" type="number" min="1" max="20" class="ui-field h-10 w-full rounded px-3 text-sm outline-none" />
                  </label>
                  <label class="block" for="eval-judge-model">
                    <span class="mb-1 block text-xs font-medium text-slate-600">评分模型（可选）</span>
                    <select
                      id="eval-judge-model"
                      v-model="evalJudgeModel"
                      class="ui-field h-10 w-full rounded px-3 text-sm outline-none"
                    >
                      <option value="">不评分，只看资料有没有找对</option>
                      <option v-for="m in judgeModels" :key="m.model_name" :value="m.model_name">
                        {{ modelDisplayName(m.model_name) }}（{{ m.source === 'personal' ? '我的密钥' : '公司统一连接' }}）
                      </option>
                    </select>
                    <span class="mt-1 block text-[11px] leading-relaxed text-slate-500">
                      选了模型，会让它逐句检查用例里的“标准回答”有没有被找到的资料支撑，算出“有据可依”的比例（防止助手瞎编）。
                      只对写了 answer 的用例打分，会消耗这个模型的额度。
                    </span>
                    <span v-if="judgeModelsLoaded && !judgeModels.length" class="mt-1 block text-[11px] text-amber-700">
                      还没有可用的聊天模型：请管理员在后台「模型连接」里连接服务商，或在「模型设置」里配置自己的密钥。
                    </span>
                  </label>
                  <button
                    @click="runRagEvaluation"
                    :disabled="evalLoading || !evalCasesText.trim()"
                    class="ui-primary inline-flex h-10 w-full items-center justify-center gap-2 rounded px-4 text-sm font-medium text-white disabled:bg-slate-300 disabled:shadow-none"
                  >
                    <BarChart3 :size="16" />
                    {{ evalLoading ? '评估中...' : '运行评估' }}
                  </button>
                </div>
              </div>

              <div class="px-4 pb-4">
                <p v-if="evalError" class="mb-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{{ evalError }}</p>
                <p v-if="evalReport && !showEvalResults" class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-500">
                  评估已完成，结果已隐藏。打开右上角开关即可查看指标和明细。
                </p>

                <div v-if="evalReport && showEvalResults" class="space-y-4">
                  <div class="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <MetricTile v-for="metric in evalMetricCards" :key="metric.key" :label="metric.label" :value="metric.value" />
                  </div>

                  <div class="overflow-hidden rounded border border-slate-200 bg-white">
                    <table class="w-full text-left text-xs">
                      <thead class="bg-slate-50 text-slate-500">
                        <tr>
                          <th class="px-3 py-2 font-medium">问题</th>
                          <th class="px-3 py-2 font-medium">命中</th>
                          <th class="px-3 py-2 font-medium">召回</th>
                          <th class="px-3 py-2 font-medium">MRR</th>
                          <th class="px-3 py-2 font-medium">忠诚度</th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-slate-100">
                        <tr v-for="(item, idx) in evalReport.cases" :key="idx" class="align-top">
                          <td class="max-w-md px-3 py-2 text-slate-800">{{ item.question }}</td>
                          <td class="px-3 py-2">
                            <span :class="item.hit ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'" class="rounded px-2 py-1">
                              {{ item.hit === null ? '未标注' : item.hit ? '命中' : '未命中' }}
                            </span>
                          </td>
                          <td class="px-3 py-2 text-slate-700">{{ formatMetric(item.recall) }}</td>
                          <td class="px-3 py-2 text-slate-700">{{ formatMetric(item.ranking?.mrr) }}</td>
                          <td class="px-3 py-2 text-slate-700">
                            {{ formatMetric(item.faithfulness?.score ?? null) }}
                            <p v-if="item.faithfulness_judge_error" class="mt-1 text-red-500">{{ item.faithfulness_judge_error }}</p>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  <details class="rounded border border-slate-200 bg-slate-50">
                    <summary class="cursor-pointer px-3 py-2 text-xs font-medium text-slate-700">完整 JSON 报告</summary>
                    <pre class="max-h-96 overflow-auto whitespace-pre-wrap p-3 text-xs leading-relaxed text-slate-600">{{ JSON.stringify(evalReport, null, 2) }}</pre>
                  </details>
                </div>
              </div>
            </section>

            <section v-if="activePanel === 'prompt'" class="ui-card rounded-lg">
              <header class="border-b border-slate-200 px-4 py-3">
                <h2 class="text-sm font-semibold text-slate-900">指令与上下文</h2>
                <p class="mt-1 text-xs text-slate-500">拆开查看助手设定、用户画像、能力规则和最终发送给模型的完整指令。</p>
              </header>
              <div class="grid grid-cols-1 gap-3 p-4 lg:grid-cols-3">
                <article v-for="item in promptCards" :key="item.key" class="rounded border border-slate-200 bg-white p-3">
                  <div class="mb-2 flex items-center justify-between gap-2">
                    <h3 class="text-xs font-semibold text-slate-700">{{ item.label }}</h3>
                    <span class="text-xs text-slate-400">{{ item.text.length }} 字</span>
                  </div>
                  <p class="line-clamp-6 whitespace-pre-wrap text-xs leading-relaxed text-slate-500">{{ item.text || item.empty }}</p>
                </article>
              </div>
              <div class="border-t border-slate-200">
                <PanelHeader title="最终指令" action="复制" @action="copyText(debug.prompt.final_prompt, '最终指令')" />
                <pre class="max-h-[420px] overflow-auto whitespace-pre-wrap p-4 text-sm leading-relaxed text-slate-700">{{ debug.prompt.final_prompt }}</pre>
              </div>
            </section>

            <section v-if="activePanel === 'resources'" class="ui-card rounded-lg">
              <header class="border-b border-slate-200 px-4 py-3">
                <h2 class="text-sm font-semibold text-slate-900">能力、权限与知识库</h2>
                <p class="mt-1 text-xs text-slate-500">查看当前 Agent 能调用什么、能访问什么，以及有哪些资料可用于 RAG。</p>
              </header>
              <div class="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
                <section class="rounded border border-slate-200 bg-white">
                  <PanelHeader title="工具与能力" />
                  <div class="space-y-3 p-3">
                    <div class="flex flex-wrap gap-1.5">
                      <span v-for="tool in debug.tool_names" :key="tool" class="rounded bg-blue-50 px-2 py-1 text-xs text-blue-700">{{ toolDisplayName(tool) }}</span>
                      <span v-if="debug.tool_names.length === 0" class="text-xs text-slate-400">没有绑定工具</span>
                    </div>
                    <article v-for="skill in debug.skills" :key="skill.name" class="rounded border border-slate-100 bg-slate-50 p-3">
                      <h3 class="text-sm font-semibold text-slate-800">{{ skill.name }}</h3>
                      <p class="mt-1 text-xs text-slate-500">{{ skill.description || '暂无描述' }}</p>
                    </article>
                  </div>
                </section>

                <section class="rounded border border-slate-200 bg-white">
                  <PanelHeader title="运行时权限" />
                  <div class="space-y-3 p-3">
                    <div class="flex flex-wrap gap-2">
                      <span :class="debug.permissions?.network ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'" class="rounded px-2 py-1 text-xs">
                        网络 {{ debug.permissions?.network ? '允许' : '关闭' }}
                      </span>
                      <span class="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">资源 {{ debug.permissions?.file_read?.length || 0 }} 项</span>
                      <span class="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">执行代码 关闭</span>
                    </div>
                    <article v-for="resource in debug.resources || []" :key="`${resource.skill_name}-${resource.path}`" class="rounded border border-slate-100 bg-slate-50 p-3">
                      <div class="flex items-center justify-between gap-2">
                        <p class="truncate text-sm font-medium text-slate-800">{{ resource.path }}</p>
                        <span :class="resource.allowed ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'" class="shrink-0 rounded px-2 py-1 text-xs">
                          {{ resource.allowed ? '已授权' : '未授权' }}
                        </span>
                      </div>
                      <p class="mt-1 text-xs text-slate-400">{{ resource.skill_name || '能力' }} · {{ resource.exists ? `${resource.size} bytes` : '文件缺失' }}</p>
                    </article>
                    <div v-if="!debug.resources?.length" class="py-8 text-center text-sm text-slate-400">没有声明资源</div>
                  </div>
                </section>
              </div>

              <div class="border-t border-slate-200 p-4">
                <div class="mb-3 flex items-center justify-between gap-3">
                  <h3 class="text-sm font-semibold text-slate-900">知识库文档</h3>
                  <button @click="router.push(`/agents/${agentId}/knowledge`)" class="rounded border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50">管理资料</button>
                </div>
                <div class="grid grid-cols-1 gap-2 lg:grid-cols-2">
                  <article v-for="doc in debug.knowledge" :key="doc.id" class="rounded border border-slate-200 bg-white p-3">
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <h4 class="truncate text-sm font-medium text-slate-800">{{ doc.file_name }}</h4>
                        <p class="mt-1 text-xs text-slate-500">{{ doc.chunk_count }} 块</p>
                        <p v-if="doc.error_msg" class="mt-1 line-clamp-2 text-xs text-red-600">{{ doc.error_msg }}</p>
                      </div>
                      <span :class="doc.status === 'done' ? 'bg-emerald-50 text-emerald-700' : doc.status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'" class="shrink-0 rounded px-2 py-1 text-xs">
                        {{ documentStatusLabel(doc.status) }}
                      </span>
                    </div>
                  </article>
                  <div v-if="debug.knowledge.length === 0" class="rounded border border-dashed border-slate-200 bg-white py-8 text-center text-sm text-slate-400">暂无知识库文档</div>
                </div>
              </div>
            </section>
          </main>

          <aside class="ui-card rounded-lg xl:sticky xl:top-5 xl:self-start">
            <div class="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
              <div>
                <h2 class="text-sm font-semibold text-slate-900">诊断结论</h2>
                <p class="mt-1 text-xs text-slate-500">优先处理阻塞项</p>
              </div>
              <span :class="diagnosticBadgeClass" class="rounded px-2.5 py-1 text-xs font-medium">{{ diagnosticSummary }}</span>
            </div>
            <div class="divide-y divide-slate-100">
              <article v-for="item in diagnostics" :key="item.key" class="px-4 py-3">
                <div class="flex gap-3">
                  <span :class="diagnosticDotClass(item.level)" class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"></span>
                  <div class="min-w-0">
                    <h3 class="text-sm font-semibold text-slate-900">{{ item.title }}</h3>
                    <p class="mt-1 text-xs leading-relaxed text-slate-500">{{ item.description }}</p>
                    <button v-if="item.actionText" @click="item.action" class="mt-2 rounded border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50">
                      {{ item.actionText }}
                    </button>
                  </div>
                </div>
              </article>
            </div>
          </aside>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { documentStatusLabel, messageRoleLabel, toolDisplayName } from '../utils/displayNames'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  BarChart3,
  Brain,
  ClipboardCheck,
  Database,
  Eye,
  EyeOff,
  FileText,
  RefreshCcw,
  Wrench,
} from 'lucide-vue-next'
import AgentSubnav from '../components/agent/AgentSubnav.vue'
import * as agentApi from '../api/agent'
import type { AgentDebugInfo, AgentDryRunInfo } from '../api/agent'
import { evaluateRag } from '../api/evaluation'
import type { RagEvalCase, RagEvalReport } from '../api/evaluation'
import { toastSuccess } from '../utils/toast'
import { getErrorMessage } from '../utils/request'
import { listConfigs, listSupportedModelCatalog } from '../api/llmConfig'
import { listEnterpriseProviders } from '../api/adminLlm'
import { usableChatModels, type UsableModel } from '../utils/usableModels'
import { modelDisplayName } from '../utils/displayNames'

const PanelHeader = defineComponent({
  props: {
    title: { type: String, required: true },
    action: { type: String, default: '' },
  },
  emits: ['action'],
  setup(props, { emit }) {
    return () => h('header', { class: 'flex items-center justify-between gap-3 border-b border-slate-200 px-3 py-2' }, [
      h('h3', { class: 'text-xs font-semibold text-slate-700' }, props.title),
      props.action
        ? h('button', {
            class: 'rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-50',
            onClick: () => emit('action'),
          }, props.action)
        : null,
    ])
  },
})

const MetricTile = defineComponent({
  props: {
    label: { type: String, required: true },
    value: { type: String, required: true },
    tone: { type: String, default: 'neutral' },
  },
  setup(props) {
    const toneClass = computed(() => {
      if (props.tone === 'good') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
      if (props.tone === 'bad') return 'border-red-200 bg-red-50 text-red-700'
      return 'border-slate-200 bg-white text-slate-900'
    })
    return () => h('div', { class: ['rounded border p-3', toneClass.value] }, [
      h('p', { class: 'text-xs opacity-70' }, props.label),
      h('p', { class: 'mt-1 text-sm font-semibold' }, props.value),
    ])
  },
})

const route = useRoute()
const router = useRouter()
const agentId = computed(() => Number(route.params.agentId))
const activePanel = ref(localStorage.getItem('agent_debug_panel') || 'dry-run')
const debug = ref<AgentDebugInfo | null>(null)
const dryRun = ref<AgentDryRunInfo | null>(null)
const loading = ref(false)
const dryRunLoading = ref(false)
const errorMsg = ref('')
const dryRunError = ref('')
const dryRunMessage = ref('')
const evalCasesText = ref(JSON.stringify([
  {
    question: 'RAG 知识库入库流程是什么？',
    expected_knowledge_ids: [],
    expected_texts: ['上传文档后解析、切片、生成 embedding，并写入向量库'],
    answer: '上传文档后，系统会解析文本、切片、生成 embedding，然后写入向量库。',
  },
], null, 2))
const evalTopK = ref(5)
const evalJudgeModel = ref('')
const judgeModels = ref<UsableModel[]>([])
const judgeModelsLoaded = ref(false)

// 评分模型只能从“现在就能调用”的聊天模型里选：手填一个没接通的名字只会在运行时报错
const loadJudgeModels = async () => {
  try {
    const [catalog, providers, configs] = await Promise.all([
      listSupportedModelCatalog(),
      listEnterpriseProviders().catch(() => []),
      listConfigs().catch(() => []),
    ])
    judgeModels.value = usableChatModels(catalog.chat, providers.map((p) => p.provider), configs.map((c) => c.model_name))
  } catch {
    judgeModels.value = []
  } finally {
    judgeModelsLoaded.value = true
  }
}
const evalLoading = ref(false)
const evalError = ref('')
const evalReport = ref<RagEvalReport | null>(null)
const showEvalResults = ref(localStorage.getItem('show_rag_eval_results') !== '0')

type DiagnosticLevel = 'ok' | 'warn' | 'danger'

interface DiagnosticItem {
  key: string
  level: DiagnosticLevel
  title: string
  description: string
  actionText?: string
  action?: () => void
}

const workbenchTabs = [
  { key: 'dry-run', label: '对话预检', icon: ClipboardCheck },
  { key: 'evaluation', label: 'RAG 评估', icon: BarChart3 },
  { key: 'prompt', label: '指令上下文', icon: FileText },
  { key: 'resources', label: '能力资源', icon: Wrench },
]

const readinessCards = computed(() => {
  if (!debug.value) return []
  const current = debug.value
  const good = 'border-emerald-200 bg-emerald-50 text-emerald-700'
  const warn = 'border-amber-200 bg-amber-50 text-amber-700'
  const neutral = 'border-slate-200 bg-white/90 text-slate-800'
  return [
    {
      key: 'model',
      label: '模型连接',
      value: current.readiness.model_configured ? '已配置' : '缺少配置',
      badge: current.agent.model_name,
      icon: Activity,
      tone: current.readiness.model_configured ? good : warn,
    },
    {
      key: 'rag',
      label: '资料库',
      value: current.agent.rag_enabled === 1
        ? `${current.readiness.knowledge_done_count}/${current.readiness.knowledge_total_count} 文档可用`
        : '未启用',
      badge: current.readiness.embedding_configured ? 'Embedding OK' : '待配置',
      icon: Database,
      tone: current.readiness.rag_ready ? good : warn,
    },
    {
      key: 'skills',
      label: '能力工具',
      value: `${current.readiness.skill_count} 个能力 / ${current.readiness.tool_count} 个工具`,
      badge: current.readiness.skill_count > 0 ? '已装配' : '空',
      icon: Wrench,
      tone: current.readiness.skill_count > 0 ? neutral : warn,
    },
    {
      key: 'memory',
      label: '记忆',
      value: current.agent.memory_enabled === 1 ? '已启用' : '未启用',
      badge: `温度 ${current.agent.temperature}`,
      icon: Brain,
      tone: current.agent.memory_enabled === 1 ? good : neutral,
    },
  ]
})

const promptCards = computed(() => {
  if (!debug.value) return []
  return [
    { key: 'base', label: '助手基础设定', text: debug.value.prompt.base_prompt || '', empty: '暂无基础设定' },
    { key: 'profile', label: '用户画像', text: debug.value.prompt.profile_prompt || '', empty: '未配置用户画像' },
    { key: 'skill', label: '能力规则', text: debug.value.prompt.skill_prompt || '', empty: '暂无能力规则' },
  ]
})

const diagnostics = computed<DiagnosticItem[]>(() => {
  const current = debug.value
  if (!current) return []

  const items: DiagnosticItem[] = []
  const readiness = current.readiness
  const hasRag = current.agent.rag_enabled === 1
  const promptLength = current.prompt.final_prompt.trim().length

  if (!readiness.model_configured) {
    items.push({
      key: 'model-missing',
      level: 'danger',
      title: '聊天模型还没有连接',
      description: '请先在“连接模型”里添加可用模型名称和密钥。',
      actionText: '去连接模型',
      action: () => router.push('/llm-configs'),
    })
  }

  if (hasRag && !readiness.embedding_configured) {
    items.push({
      key: 'embedding-missing',
      level: 'danger',
      title: '资料检索缺少模型',
      description: '已开启资料库，但没有可用的检索模型。',
      actionText: '去连接模型',
      action: () => router.push('/llm-configs'),
    })
  }

  if (hasRag && readiness.knowledge_total_count === 0) {
    items.push({
      key: 'knowledge-empty',
      level: 'warn',
      title: '资料库还没有内容',
      description: '需要先上传文件或抓取网页并完成入库。',
      actionText: '添加资料',
      action: () => router.push(`/agents/${agentId.value}/knowledge`),
    })
  }

  if (hasRag && readiness.knowledge_total_count > 0 && readiness.knowledge_done_count === 0) {
    items.push({
      key: 'knowledge-not-ready',
      level: 'warn',
      title: '资料还没有完成入库',
      description: '查看后台任务是否排队、失败或需要重建。',
      actionText: '看进度',
      action: () => router.push('/tasks'),
    })
  }

  if (readiness.skill_count === 0) {
    items.push({
      key: 'skill-empty',
      level: 'warn',
      title: '还没有给助手添加能力',
      description: '需要工具调用或固定流程时，请绑定能力。',
      actionText: '去能力库',
      action: () => router.push('/skills'),
    })
  }

  if (promptLength < 80) {
    items.push({
      key: 'prompt-thin',
      level: 'warn',
      title: '助手设定比较简单',
      description: '角色、任务、约束或输出格式较少，回答可能不稳定。',
      actionText: '去工作台',
      action: () => router.push('/agents'),
    })
  }

  const lastDryRun = dryRun.value
  if (lastDryRun?.rag.enabled && !lastDryRun.rag.ok) {
    items.push({
      key: 'rag-error',
      level: 'danger',
      title: '刚才的资料检索失败',
      description: lastDryRun.rag.error || '请检查检索模型、资料入库状态和后端日志。',
      actionText: '去资料库',
      action: () => router.push(`/agents/${agentId.value}/knowledge`),
    })
  }

  if (lastDryRun?.rag.enabled && lastDryRun.rag.ok && lastDryRun.rag.hit_count === 0 && readiness.knowledge_done_count > 0) {
    items.push({
      key: 'rag-no-hit',
      level: 'warn',
      title: '刚才的问题没有命中资料',
      description: '可以换更接近文档原文的问题，或补充更准确的资料。',
      actionText: '去资料库',
      action: () => router.push(`/agents/${agentId.value}/knowledge`),
    })
  }

  if (items.length === 0) {
    items.push({
      key: 'ready',
      level: 'ok',
      title: '当前助手基础状态正常',
      description: '模型、资料库和能力装配没有发现明显阻塞。',
    })
  }

  return items
})

const diagnosticSummary = computed(() => {
  if (diagnostics.value.some((item) => item.level === 'danger')) return '需要处理'
  if (diagnostics.value.some((item) => item.level === 'warn')) return '可优化'
  return '状态正常'
})

const diagnosticBadgeClass = computed(() => {
  if (diagnostics.value.some((item) => item.level === 'danger')) return 'bg-red-50 text-red-700'
  if (diagnostics.value.some((item) => item.level === 'warn')) return 'bg-amber-50 text-amber-700'
  return 'bg-emerald-50 text-emerald-700'
})

const diagnosticDotClass = (level: DiagnosticLevel) => {
  if (level === 'danger') return 'bg-red-500'
  if (level === 'warn') return 'bg-amber-500'
  return 'bg-emerald-500'
}

const formatMetric = (value: number | null | undefined) => {
  if (typeof value !== 'number') return '-'
  return `${(value * 100).toFixed(1)}%`
}

const evalMetricCards = computed(() => {
  const metrics = evalReport.value?.metrics
  return [
    { key: 'hit_rate', label: '命中率', value: formatMetric(metrics?.hit_rate) },
    { key: 'recall', label: '召回率', value: formatMetric(metrics?.recall) },
    { key: 'precision_at_k', label: 'Precision@K', value: formatMetric(metrics?.precision_at_k) },
    { key: 'mrr', label: 'MRR', value: formatMetric(metrics?.mrr) },
    { key: 'faithfulness', label: '忠诚度', value: formatMetric(metrics?.faithfulness) },
  ]
})

const loadDebug = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    debug.value = await agentApi.getAgentDebug(agentId.value)
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '读取运行检查信息失败')
  } finally {
    loading.value = false
  }
}

const copyText = async (text: string, label = '内容') => {
  await navigator.clipboard.writeText(text)
  toastSuccess(`已复制${label}`)
}

const short = (text: string, max: number) => {
  return text.length > max ? `${text.slice(0, max)}...` : text
}

const runDryRun = async () => {
  const message = dryRunMessage.value.trim()
  if (!message || dryRunLoading.value) return
  dryRunLoading.value = true
  dryRunError.value = ''
  dryRun.value = null
  try {
    dryRun.value = await agentApi.dryRunAgent(agentId.value, { message })
  } catch (e: any) {
    dryRunError.value = getErrorMessage(e, 'Dry Run 失败')
  } finally {
    dryRunLoading.value = false
  }
}

const runOneClickCheck = async () => {
  activePanel.value = 'dry-run'
  if (!dryRunMessage.value.trim()) {
    dryRunMessage.value = '请用一句话说明你现在可以帮助我完成什么，并优先参考我提供的资料。'
  }
  await runDryRun()
}

const parseEvalCases = (): RagEvalCase[] => {
  const parsed = JSON.parse(evalCasesText.value)
  const cases = Array.isArray(parsed) ? parsed : parsed.cases
  if (!Array.isArray(cases) || cases.length === 0) {
    throw new Error('评估集必须是数组，或包含 cases 数组')
  }
  return cases.map((item: any) => ({
    question: String(item.question || '').trim(),
    expected_chunk_ids: Array.isArray(item.expected_chunk_ids) ? item.expected_chunk_ids : [],
    expected_knowledge_ids: Array.isArray(item.expected_knowledge_ids) ? item.expected_knowledge_ids : [],
    expected_texts: Array.isArray(item.expected_texts) ? item.expected_texts : [],
    answer: item.answer || null,
    knowledge_id: item.knowledge_id ?? null,
  })).filter((item: RagEvalCase) => item.question)
}

const runRagEvaluation = async () => {
  if (evalLoading.value) return
  evalLoading.value = true
  evalError.value = ''
  try {
    const cases = parseEvalCases()
    if (cases.length === 0) {
      throw new Error('至少需要一条带 question 的评估用例')
    }
    evalReport.value = await evaluateRag(agentId.value, {
      cases,
      top_k: evalTopK.value,
      faithfulness_judge_model: evalJudgeModel.value || null,
    })
    toastSuccess('RAG 评估完成')
  } catch (e: any) {
    evalError.value = getErrorMessage(e, e?.message || 'RAG 评估失败')
  } finally {
    evalLoading.value = false
  }
}

onMounted(() => {
  loadDebug()
  loadJudgeModels()
})
watch(activePanel, (value) => {
  localStorage.setItem('agent_debug_panel', value)
})
watch(showEvalResults, (value) => {
  localStorage.setItem('show_rag_eval_results', value ? '1' : '0')
})
watch(agentId, async () => {
  dryRun.value = null
  dryRunError.value = ''
  evalReport.value = null
  evalError.value = ''
  await loadDebug()
})
</script>
