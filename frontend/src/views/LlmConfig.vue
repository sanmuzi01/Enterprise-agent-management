<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-slate-200/80 bg-white/88 px-5 py-4 shadow-sm backdrop-blur-xl lg:px-8">
      <div class="mx-auto max-w-6xl">
        <SectionTabs class="mb-3" :tabs="[
          { label: '我的模型密钥', path: '/llm-configs' },
          { label: '个性化与系统状态', path: '/settings' },
          { label: '飞书 / 钉钉', path: '/settings/integrations' },
        ]" />
        <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 class="text-xl font-semibold text-slate-950">连接 AI 服务</h1>
            <p class="mt-1 max-w-2xl text-sm leading-6 text-slate-600">
              选择你已有的平台，粘贴一次访问密钥，系统会自动准备“回答问题”和“读取资料”两项能力。
            </p>
          </div>
          <div class="grid grid-cols-2 gap-2 lg:w-[330px]">
            <StatusCard label="回答问题" :ok="chatConfigs.length > 0 || enterpriseConnected" :text="chatConfigs.length || enterpriseConnected ? '已开启' : '未开启'" />
            <StatusCard label="读取资料" :ok="embeddingConfigs.length > 0 || enterpriseConnected" :text="embeddingConfigs.length || enterpriseConnected ? '已开启' : '未开启'" />
          </div>
        </div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto px-5 py-6 lg:px-8">
      <p v-if="enterpriseConnected" class="mx-auto mb-5 max-w-6xl rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800" data-testid="enterprise-llm-note">
        管理员已经为全公司统一连接了：{{ enterpriseProviders.map((p) => p.label).join('、') }}。这些服务你不需要再配置；
        如果你在下面填了自己的密钥，会优先使用你自己的。
      </p>
      <div class="mx-auto grid max-w-6xl gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section class="space-y-5">
          <section class="ui-card rounded-lg p-5">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p class="text-xs font-medium text-sky-700">快速开始</p>
                <h2 class="mt-1 text-lg font-semibold text-slate-950">一步开启助手能力</h2>
                <p class="mt-1 text-sm text-slate-500">普通用户只需要选平台、勾能力、粘贴密钥；推荐方案会自动处理。</p>
              </div>
              <button @click="reload" class="rounded border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
                刷新状态
              </button>
            </div>

            <div class="mt-4 grid gap-3 md:grid-cols-3">
              <button
                v-for="provider in providerOptions"
                :key="provider.key"
                @click="selectProvider(provider.key)"
                class="rounded-lg border p-4 text-left transition"
                :class="selectedProvider === provider.key ? 'border-transparent bg-[var(--accent-soft)] ring-2 ring-[var(--accent)]' : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'"
              >
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <p class="text-base font-semibold">{{ provider.label }}</p>
                    <p class="mt-1 text-sm leading-6" :class="'text-slate-600'">
                      {{ provider.description }}
                    </p>
                  </div>
                  <CheckCircle2 v-if="selectedProvider === provider.key" :size="18" class="text-[var(--accent)]" />
                </div>
                <div class="mt-3 flex flex-wrap gap-1.5">
                  <span
                    v-for="tag in provider.tags"
                    :key="tag"
                    class="rounded px-2 py-1 text-xs"
                    :class="selectedProvider === provider.key ? 'bg-white text-[var(--accent)]' : 'bg-slate-100 text-slate-500'"
                  >
                    {{ tag }}
                  </span>
                </div>
              </button>
            </div>

            <div class="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
              <section class="rounded-lg border border-slate-200 bg-white p-4">
                <h3 class="text-sm font-semibold text-slate-900">这次要开启什么</h3>
                <div class="mt-3 grid gap-2 md:grid-cols-2">
                  <label
                    v-for="item in capabilityChoices"
                    :key="item.key"
                    class="flex cursor-pointer items-start gap-3 rounded border border-slate-200 bg-slate-50 p-3"
                  >
                    <input v-model="selectedCapabilities" :value="item.key" type="checkbox" class="mt-1 h-4 w-4 accent-blue-600" :disabled="item.disabled" />
                    <span>
                      <span class="block text-sm font-semibold text-slate-800">{{ item.title }}</span>
                      <span class="mt-1 block text-xs leading-5 text-slate-500">{{ item.description }}</span>
                    </span>
                  </label>
                </div>

                <details class="mt-4 rounded border border-slate-200 bg-slate-50">
                  <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">技术人员选项</summary>
                  <div class="grid gap-3 border-t border-slate-200 p-3 md:grid-cols-2">
                    <label class="block">
                      <span class="mb-1 block text-xs font-medium text-slate-600">回答方案</span>
                      <select v-model="selectedChatModel" class="ui-field h-10 w-full rounded px-3 text-sm outline-none">
                        <option v-for="model in chatModelOptions" :key="model.model_name" :value="model.model_name">
                          {{ modelLabel(model.model_name) }}
                        </option>
                      </select>
                    </label>
                    <label class="block">
                      <span class="mb-1 block text-xs font-medium text-slate-600">资料读取方案</span>
                      <select v-model="selectedEmbeddingModel" class="ui-field h-10 w-full rounded px-3 text-sm outline-none" :disabled="!embeddingModelOptions.length">
                        <option v-if="!embeddingModelOptions.length" value="">该平台暂不支持资料读取</option>
                        <option v-for="model in embeddingModelOptions" :key="model.model_name" :value="model.model_name">
                          {{ modelLabel(model.model_name) }}
                        </option>
                      </select>
                    </label>
                  </div>
                </details>
              </section>

              <section class="rounded-lg border border-slate-200 bg-white p-4">
                <h3 class="text-sm font-semibold text-slate-900">访问密钥</h3>
                <p class="mt-1 text-xs leading-5 text-slate-500">从平台控制台复制密钥粘贴到这里。保存后只显示隐藏后的结果。</p>
                <div class="relative mt-3 rounded-[10px]" data-guide="api-key">
                  <KeyRound :size="16" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input
                    v-model="apiKey"
                    :type="showKey ? 'text' : 'password'"
                    class="ui-field h-11 w-full rounded pl-10 pr-16 text-sm outline-none"
                    :placeholder="`粘贴 ${selectedProviderMeta.label} 的访问密钥`"
                  />
                  <button type="button" @click="showKey = !showKey" class="absolute right-2 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100">
                    {{ showKey ? '隐藏' : '显示' }}
                  </button>
                </div>
                <button
                  @click="pasteKey"
                  class="mt-2 inline-flex h-8 items-center gap-2 rounded border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50"
                >
                  <Clipboard :size="14" />
                  从剪贴板粘贴
                </button>

                <p v-if="errorMsg" class="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm leading-6 text-red-700">{{ errorMsg }}</p>

                <button
                  @click="saveQuickConnect"
                  :disabled="!canQuickSave || submitting"
                  class="ui-primary mt-4 h-11 w-full rounded text-sm font-semibold text-white disabled:bg-slate-300 disabled:shadow-none"
                >
                  {{ submitting ? '正在连接...' : quickSaveText }}
                </button>
              </section>
            </div>
          </section>
        </section>

        <aside class="space-y-5">
          <section class="ui-card rounded-lg p-5 xl:sticky xl:top-6">
            <div class="flex items-center justify-between">
              <h2 class="text-base font-semibold text-slate-950">当前能力</h2>
              <span class="rounded bg-slate-100 px-2 py-1 text-xs text-slate-500">{{ configs.length }} 项</span>
            </div>
            <div class="mt-4 space-y-3">
              <ConnectionList
                title="回答问题"
                empty-text="先开启回答问题能力"
                :items="chatConfigs"
                :testing-name="testingName"
                :test-results="testResults"
                @test="testConfig"
                @remove="removeConfig"
              />
              <ConnectionList
                title="读取资料"
                empty-text="要让助手引用资料，需要先开启这项能力"
                :items="embeddingConfigs"
                :testing-name="testingName"
                :test-results="testResults"
                @test="testConfig"
                @remove="removeConfig"
              />
            </div>
          </section>
        </aside>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { MODEL_NAMES, modelDisplayName } from '../utils/displayNames'
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { CheckCircle2, Clipboard, KeyRound, Trash2 } from 'lucide-vue-next'
import SectionTabs from '../components/SectionTabs.vue'
import * as llmApi from '../api/llmConfig'
import { listEnterpriseProviders } from '../api/adminLlm'
import type { LlmConfig, LlmConfigTestResult, SupportedModel } from '../api/llmConfig'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'

type ProviderKey = 'zhipu' | 'deepseek' | 'openai' | 'moonshot' | 'qwen' | 'perplexity'
type CapabilityKey = 'chat' | 'embedding'

const configs = ref<LlmConfig[]>([])
const supportedCatalog = ref<{ chat: SupportedModel[]; embedding: SupportedModel[] }>({ chat: [], embedding: [] })
const selectedProvider = ref<ProviderKey>('zhipu')
const selectedCapabilities = ref<CapabilityKey[]>(['chat', 'embedding'])
const selectedChatModel = ref('glm-4')
const selectedEmbeddingModel = ref('embedding-3')
const apiKey = ref('')
const showKey = ref(false)
const submitting = ref(false)
const testingName = ref('')
const errorMsg = ref('')
const testResults = ref<Record<string, LlmConfigTestResult>>({})

const providerOptions = [
  {
    key: 'zhipu' as const,
    label: '智谱',
    description: '中文项目最省心，一次配置就能回答问题和读取资料。',
    tags: ['推荐', '中文友好', '一次配置'],
    chat: 'glm-4',
    embedding: 'embedding-3',
  },
  {
    key: 'deepseek' as const,
    label: 'DeepSeek',
    description: '适合代码、推理和日常问答；暂不负责读取资料。',
    tags: ['代码', '推理', '问答'],
    chat: 'deepseek-chat',
    embedding: '',
  },
  {
    key: 'openai' as const,
    label: 'OpenAI',
    description: '通用能力强，适合英文资料和中英混合场景。',
    tags: ['通用', '英文资料', '稳定'],
    chat: 'gpt-4o-mini',
    embedding: 'text-embedding-3-small',
  },
  {
    key: 'moonshot' as const,
    label: 'Kimi',
    description: '长文本和中文资料场景常用，适合阅读、总结和企业文档问答。',
    tags: ['长文本', '中文资料', '总结'],
    chat: 'kimi-latest',
    embedding: '',
  },
  {
    key: 'qwen' as const,
    label: '通义千问',
    description: '中文和企业场景覆盖面广，适合低成本、多用途助手。',
    tags: ['中文', '企业', '多模型'],
    chat: 'qwen-plus',
    embedding: '',
  },
  {
    key: 'perplexity' as const,
    label: 'Perplexity',
    description: '搜索式模型，回答自带实时联网和来源。用于工作台组件的「联网检索」数据源。',
    tags: ['联网', '实时', '带来源'],
    chat: 'sonar',
    embedding: '',
  },
]

const modelNames: Record<string, string> = MODEL_NAMES

const fallbackModels: SupportedModel[] = [
  { model_name: 'glm-4', provider: 'zhipu', kind: 'chat' },
  { model_name: 'deepseek-chat', provider: 'deepseek', kind: 'chat' },
  { model_name: 'deepseek-reasoner', provider: 'deepseek', kind: 'chat' },
  { model_name: 'gpt-4o-mini', provider: 'openai', kind: 'chat' },
  { model_name: 'o3-mini', provider: 'openai', kind: 'chat' },
  { model_name: 'o4-mini', provider: 'openai', kind: 'chat' },
  { model_name: 'kimi-latest', provider: 'moonshot', kind: 'chat' },
  { model_name: 'qwen-plus', provider: 'qwen', kind: 'chat' },
  { model_name: 'sonar', provider: 'perplexity', kind: 'chat' },
  { model_name: 'embedding-3', provider: 'zhipu', kind: 'embedding' },
  { model_name: 'text-embedding-3-small', provider: 'openai', kind: 'embedding' },
  { model_name: 'text-embedding-3-large', provider: 'openai', kind: 'embedding' },
]

const selectedProviderMeta = computed(() => providerOptions.find((item) => item.key === selectedProvider.value) || providerOptions[0])
const chatConfigs = computed(() => configs.value.filter((config) => (config.kind || inferKind(config.model_name)) !== 'embedding'))
const embeddingConfigs = computed(() => configs.value.filter((config) => (config.kind || inferKind(config.model_name)) === 'embedding'))
const allCatalogModels = computed(() => [...supportedCatalog.value.chat, ...supportedCatalog.value.embedding].length
  ? [...supportedCatalog.value.chat, ...supportedCatalog.value.embedding]
  : fallbackModels)
const chatModelOptions = computed(() => allCatalogModels.value.filter((model) => model.provider === selectedProvider.value && model.kind === 'chat'))
const embeddingModelOptions = computed(() => allCatalogModels.value.filter((model) => model.provider === selectedProvider.value && model.kind === 'embedding'))
const capabilityChoices = computed(() => [
  {
    key: 'chat' as const,
    title: '让助手回答问题',
    description: selectedChatModel.value ? `推荐：${friendlyModelName(selectedChatModel.value)}` : '让助手能聊天、写作、分析和调用工具。',
    disabled: !selectedChatModel.value,
  },
  {
    key: 'embedding' as const,
    title: '让助手读取资料',
    description: selectedEmbeddingModel.value ? `推荐：${friendlyModelName(selectedEmbeddingModel.value)}` : '该平台暂不支持读取资料。',
    disabled: !selectedEmbeddingModel.value,
  },
])
const selectedModels = computed(() => {
  const models: string[] = []
  if (selectedCapabilities.value.includes('chat') && selectedChatModel.value) models.push(selectedChatModel.value)
  if (selectedCapabilities.value.includes('embedding') && selectedEmbeddingModel.value) models.push(selectedEmbeddingModel.value)
  return Array.from(new Set(models))
})
const canQuickSave = computed(() => selectedModels.value.length > 0 && apiKey.value.trim().length > 0)
const quickSaveText = computed(() => `保存并测试 ${selectedModels.value.length || 0} 项能力`)

function inferKind(modelName: string): CapabilityKey {
  const name = modelName.toLowerCase()
  return name.includes('embedding') || name.startsWith('baai/') ? 'embedding' : 'chat'
}

function modelLabel(modelName: string) {
  return modelDisplayName(modelName)
}

function friendlyModelName(modelName: string) {
  return modelNames[modelName] || '推荐方案'
}

function selectProvider(provider: ProviderKey) {
  selectedProvider.value = provider
}

function applyProviderDefaults() {
  const meta = selectedProviderMeta.value
  selectedChatModel.value = chatModelOptions.value.some((item) => item.model_name === meta.chat)
    ? meta.chat
    : chatModelOptions.value[0]?.model_name || ''
  selectedEmbeddingModel.value = embeddingModelOptions.value.some((item) => item.model_name === meta.embedding)
    ? meta.embedding
    : embeddingModelOptions.value[0]?.model_name || ''
  selectedCapabilities.value = selectedEmbeddingModel.value ? ['chat', 'embedding'] : ['chat']
}

const enterpriseProviders = ref<{ provider: string; label: string }[]>([])
const enterpriseConnected = computed(() => enterpriseProviders.value.length > 0)

async function reload() {
  configs.value = await llmApi.listConfigs()
  try {
    enterpriseProviders.value = await listEnterpriseProviders()
  } catch {
    enterpriseProviders.value = []       // 读不到不影响个人配置
  }
}

async function loadSupportedModels() {
  try {
    supportedCatalog.value = await llmApi.listSupportedModelCatalog()
  } catch {
    supportedCatalog.value = { chat: [], embedding: [] }
  }
}

async function pasteKey() {
  try {
    apiKey.value = await navigator.clipboard.readText()
    toastSuccess('已粘贴访问密钥')
  } catch {
    toastError('浏览器未允许读取剪贴板，请手动粘贴')
  }
}

async function saveQuickConnect() {
  if (!canQuickSave.value) return
  submitting.value = true
  errorMsg.value = ''
  const key = apiKey.value.trim()
  const meta = selectedProviderMeta.value
  const caps = [...selectedCapabilities.value]
  // 选的就是平台推荐默认 → 走后端「一次连接」原子端点（两条配置同一事务，中途失败不留半套）；
  // 在「技术人员选项」里改过模型 → 退回逐条保存。
  const usingDefaults =
    (!caps.includes('chat') || selectedChatModel.value === meta.chat) &&
    (!caps.includes('embedding') || selectedEmbeddingModel.value === meta.embedding)
  try {
    if (usingDefaults) {
      const res = await llmApi.quickConnect({
        provider: selectedProvider.value,
        api_key: key,
        capabilities: caps as Array<'chat' | 'embedding'>,
      })
      await reload()
      for (const [name, result] of Object.entries(res.results)) {
        testResults.value[name] = result
      }
      apiKey.value = ''
      showKey.value = false
      const okCount = Object.values(res.results).filter((r) => r.ok).length
      toastSuccess(`已连接 ${res.saved.length} 项能力（${okCount} 项测试通过）`)
    } else {
      const savedModels = [...selectedModels.value]
      for (const modelName of savedModels) {
        await llmApi.saveConfig({ model_name: modelName, api_key: key })
      }
      await reload()
      apiKey.value = ''
      showKey.value = false
      toastSuccess(`已保存 ${savedModels.length} 项能力`)
      for (const modelName of savedModels) {
        const cfg = configs.value.find((item) => item.model_name === modelName)
        if (cfg) await testConfig(cfg, false)
      }
    }
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, 'AI 服务连接失败，请检查访问密钥后再试')
  } finally {
    submitting.value = false
  }
}

async function removeConfig(cfg: LlmConfig) {
  if (!confirm(`确认删除「${modelNames[cfg.model_name] || cfg.model_name}」？删除后相关能力会停用。`)) return
  await llmApi.deleteConfig(cfg.model_name)
  await reload()
}

async function testConfig(cfg: LlmConfig, toast = true) {
  testingName.value = cfg.model_name
  try {
    const result = await llmApi.testConfig(cfg.model_name)
    testResults.value[cfg.model_name] = result
    if (toast) toastSuccess(`${modelNames[cfg.model_name] || cfg.model_name} 连接正常`)
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    const result = typeof detail === 'object'
      ? detail as LlmConfigTestResult
      : { ok: false, model_name: cfg.model_name, message: getErrorMessage(e, '连接测试失败，请检查访问密钥或稍后重试') }
    testResults.value[cfg.model_name] = result
    if (toast) toastError(testResultText(result))
  } finally {
    testingName.value = ''
  }
}

function testResultText(result?: LlmConfigTestResult) {
  if (!result) return ''
  if (result.ok) {
    const elapsed = result.elapsed_ms !== undefined ? ` · ${result.elapsed_ms}ms` : ''
    if (result.kind === 'embedding') return `资料读取连接正常 · ${result.dimension || 0}维${elapsed}`
    return `${result.message}${elapsed}${result.preview ? ` · ${result.preview}` : ''}`
  }
  const error = cleanProviderError(result.error)
  return `${result.message}${error ? `：${error}` : ''}`
}

function cleanProviderError(error?: string) {
  if (!error) return ''
  const masked = error
    .replace(/\*{2,}[a-z0-9_-]+/gi, '已隐藏')
    .replace(/sk-[a-z0-9_-]+/gi, '已隐藏')
  const lower = masked.toLowerCase()
  if (lower.includes('authentication') || lower.includes('unauthorized') || lower.includes('invalid') || lower.includes('api key')) {
    return '访问密钥无效或没有权限，请检查后重新粘贴'
  }
  if (lower.includes('rate limit') || lower.includes('too many requests')) {
    return '服务调用太频繁，请稍后再试'
  }
  if (lower.includes('insufficient') || lower.includes('quota') || lower.includes('balance')) {
    return '账号额度不足，请检查服务商后台余额或套餐'
  }
  if (lower.includes('timeout')) {
    return '连接超时，请稍后重试'
  }
  if (lower.includes('connection') || lower.includes('connect')) {
    return '连接外部服务失败，请检查网络或服务状态'
  }
  return getErrorMessage({ message: masked }, '连接外部服务失败，请稍后重试')
}

const StatusCard = defineComponent({
  props: {
    label: { type: String, required: true },
    ok: { type: Boolean, required: true },
    text: { type: String, required: true },
  },
  setup(props) {
    return () => h('div', {
      class: [
        'rounded-lg border px-3 py-2',
        props.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-800',
      ],
    }, [
      h('p', { class: 'text-[11px] font-medium opacity-70' }, props.label),
      h('p', { class: 'mt-0.5 text-sm font-semibold' }, props.text),
    ])
  },
})

const ConnectionList = defineComponent({
  props: {
    title: { type: String, required: true },
    emptyText: { type: String, required: true },
    items: { type: Array as () => LlmConfig[], required: true },
    testingName: { type: String, default: '' },
    testResults: { type: Object as () => Record<string, LlmConfigTestResult>, required: true },
  },
  emits: ['test', 'remove'],
  setup(props, { emit }) {
    return () => h('div', { class: 'rounded-lg border border-slate-200 bg-white p-3' }, [
      h('div', { class: 'mb-3 flex items-center justify-between' }, [
        h('p', { class: 'text-sm font-semibold text-slate-800' }, props.title),
        h('span', { class: 'rounded bg-slate-100 px-2 py-1 text-xs text-slate-500' }, `${props.items.length} 个`),
      ]),
      props.items.length
        ? h('div', { class: 'space-y-2' }, props.items.map((cfg) => {
            const result = props.testResults[cfg.model_name]
            return h('article', { key: cfg.id, class: 'rounded border border-slate-100 bg-slate-50 p-3' }, [
              h('div', { class: 'flex items-start justify-between gap-2' }, [
                h('div', { class: 'min-w-0' }, [
                  h('p', { class: 'truncate text-sm font-medium text-slate-900' }, friendlyModelName(cfg.model_name)),
                  h('p', { class: 'mt-1 text-xs text-slate-500' }, cfg.api_key ? '访问密钥已保存' : '已保存'),
                  h('details', { class: 'mt-2 text-xs text-slate-400' }, [
                    h('summary', { class: 'cursor-pointer select-none hover:text-slate-600' }, '查看技术信息'),
                    h('p', { class: 'mt-1 break-all font-mono' }, cfg.model_name),
                  ]),
                ]),
              ]),
              result ? h('p', { class: result.ok ? 'mt-2 text-xs text-emerald-700' : 'mt-2 text-xs text-red-600' }, testResultText(result)) : null,
              h('div', { class: 'mt-3 flex justify-end gap-1.5' }, [
                h('button', {
                  class: 'h-8 rounded border border-slate-200 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50 disabled:text-slate-300',
                  disabled: props.testingName === cfg.model_name,
                  onClick: () => emit('test', cfg),
                }, props.testingName === cfg.model_name ? '测试中' : '测试'),
                h('button', {
                  class: 'inline-flex h-8 w-8 items-center justify-center rounded border border-red-100 bg-white text-red-500 hover:bg-red-50',
                  title: '删除',
                  onClick: () => emit('remove', cfg),
                }, [h(Trash2, { size: 14 })]),
              ]),
            ])
          }))
        : h('p', { class: 'rounded border border-dashed border-slate-200 bg-slate-50 py-7 text-center text-sm text-slate-500' }, props.emptyText),
    ])
  },
})

watch(selectedProvider, applyProviderDefaults)

onMounted(async () => {
  await Promise.all([reload(), loadSupportedModels()])
  applyProviderDefaults()
})
</script>
