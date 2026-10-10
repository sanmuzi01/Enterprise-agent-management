<template>
  <section ref="floatRoot" class="group fixed bottom-5 right-4 z-[9999] flex items-end justify-end max-md:hidden md:right-5">
    <div class="relative">
      <button
        type="button"
        @click="go('chat')"
        class="ui-glass-float relative inline-flex h-12 w-12 items-center justify-center rounded-full text-[var(--accent)] transition-transform duration-500 ease-[var(--spring)] hover:-translate-y-0.5"
        :title="activeAgent ? `当前助手：${activeAgent.name}` : '选择当前助手'"
        :aria-label="activeAgent ? `当前助手：${activeAgent.name}` : '选择当前助手'"
      >
        <Bot :size="22" :stroke-width="1.7" class="relative" />
        <span class="absolute right-0.5 top-0.5 h-3 w-3 rounded-full border-2 border-white bg-emerald-500"></span>
      </button>
    </div>

    <aside
      :class="[
        'absolute bottom-14 right-0 w-[min(calc(100vw-2rem),21rem)] translate-y-1 rounded-[20px] bg-[var(--glass)] p-3.5 text-slate-900 opacity-0 shadow-[var(--sh-glass)] backdrop-blur-2xl backdrop-saturate-150 transition group-hover:pointer-events-auto group-hover:translate-y-0 group-hover:opacity-100',
        chooserOpen ? 'pointer-events-auto translate-y-0 opacity-100' : 'pointer-events-none',
      ]"
    >
      <template v-if="activeAgent">
        <div class="flex items-start gap-3">
          <span class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[11px] bg-slate-100 text-slate-700">
            <Bot :size="18" />
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <p class="truncate text-sm font-semibold">{{ activeAgent.name }}</p>
              <span v-if="isRouteAgent" class="shrink-0 rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-700">正在使用</span>
              <span v-else class="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">默认</span>
            </div>
            <p class="mt-0.5 truncate text-[11px] text-slate-500">{{ modelDisplayName(activeAgent.model_name) }}</p>
          </div>
        </div>

        <div class="mt-3 grid grid-cols-3 gap-2 text-[11px]">
          <span class="rounded bg-slate-100 px-2 py-1 text-slate-600">
            温度 {{ activeAgent.temperature ?? '-' }}
          </span>
          <span :class="activeAgent.rag_enabled === 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-50 text-slate-500'" class="rounded-lg bg-slate-100 px-2 py-1">
            知识 {{ activeAgent.rag_enabled === 1 ? '开' : '关' }}
          </span>
          <span :class="activeAgent.memory_enabled === 1 ? 'bg-blue-50 text-blue-700' : 'bg-slate-50 text-slate-500'" class="rounded-lg bg-slate-100 px-2 py-1">
            记忆 {{ activeAgent.memory_enabled === 1 ? '开' : '关' }}
          </span>
        </div>

        <div class="mt-3 grid grid-cols-6 gap-1.5">
          <button @click="go('chat')" class="float-action" title="聊天">
            <MessageSquare :size="15" />
          </button>
          <button @click="go('knowledge')" class="float-action" title="知识库">
            <BookOpen :size="15" />
          </button>
          <button @click="go('memory')" class="float-action" title="记忆">
            <Brain :size="15" />
          </button>
          <button @click="go('debug')" class="float-action" title="运行检查">
            <Bug :size="15" />
          </button>
          <button @click.stop="toggleKeySettings" class="float-action" title="设置 API Key">
            <KeyRound :size="15" />
          </button>
          <button @click.stop="toggleChooser" class="float-action" title="切换助手">
            <Shuffle :size="15" />
          </button>
        </div>

        <div v-if="keySettingsOpen" class="mt-3 rounded-lg border border-amber-100 bg-amber-50/70 p-3">
          <div class="mb-2 flex items-center justify-between gap-2">
            <p class="text-xs font-semibold text-slate-700">API Key 设置</p>
            <button @click.stop="keySettingsOpen = false" class="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-white hover:text-slate-700">关闭</button>
          </div>

          <label class="block">
            <span class="mb-1 block text-[11px] text-slate-500">当前助手模型</span>
            <input
              v-model="chatModelName"
              class="ui-field h-9 w-full rounded px-2 text-xs outline-none"
              placeholder="例如 glm-4、deepseek-chat、gpt-4o-mini"
            />
          </label>

          <label class="mt-2 block">
            <span class="mb-1 block text-[11px] text-slate-500">访问密钥</span>
            <div class="relative">
              <input
                v-model="apiKey"
                :type="showKey ? 'text' : 'password'"
                class="ui-field h-9 w-full rounded px-2 pr-14 text-xs outline-none"
                placeholder="粘贴 API Key"
              />
              <button type="button" @click.stop="showKey = !showKey" class="absolute right-1 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-[11px] text-slate-500 hover:bg-white">
                {{ showKey ? '隐藏' : '显示' }}
              </button>
            </div>
          </label>

          <label
            class="mt-2 flex items-start gap-2 rounded border border-amber-100 bg-white/70 p-2 text-[11px] text-slate-600"
            :class="!defaultEmbeddingModel ? 'opacity-55' : ''"
          >
            <input v-model="saveEmbeddingKey" type="checkbox" class="mt-0.5 h-3.5 w-3.5 accent-sky-600" :disabled="!defaultEmbeddingModel" />
            <span>
              同时保存资料检索 Key
              <span class="block text-slate-400">
                {{ defaultEmbeddingModel ? `将保存 ${defaultEmbeddingModel}` : '当前模型厂商暂未内置同 Key 检索模型。' }}
              </span>
            </span>
          </label>

          <div class="mt-3 flex items-center gap-2">
            <button
              @click.stop="saveKeySettings"
              :disabled="keySaving || !canSaveKey"
              class="ui-primary h-8 flex-1 rounded text-xs font-semibold text-white disabled:bg-slate-300 disabled:shadow-none"
            >
              {{ keySaving ? '保存中...' : '保存 Key' }}
            </button>
            <button @click.stop="router.push('/llm-configs')" class="h-8 rounded border border-slate-200 bg-white px-2 text-xs text-slate-600 hover:bg-slate-50">
              高级
            </button>
          </div>
        </div>

        <div v-if="chooserOpen" class="mt-3 rounded-lg border border-sky-100 bg-sky-50/70 p-2">
          <div class="mb-2 flex items-center justify-between gap-2">
            <p class="text-xs font-semibold text-slate-700">切换助手</p>
            <button @click.stop="chooserOpen = false" class="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-white hover:text-slate-700">关闭</button>
          </div>
          <div v-if="agentLoading" class="py-3 text-center text-xs text-slate-500">加载助手中...</div>
          <div v-else-if="agents.length === 0" class="rounded border border-dashed border-sky-200 bg-white/70 p-3 text-xs text-slate-500">
            暂无可选助手。
          </div>
          <div v-else class="max-h-64 space-y-1 overflow-y-auto pr-1">
            <button
              v-for="agent in agents"
              :key="agent.id"
              @click.stop="chooseAgent(agent)"
              class="flex w-full items-center gap-2 rounded border border-transparent bg-white/70 px-2 py-2 text-left hover:border-sky-200 hover:bg-white disabled:cursor-wait disabled:opacity-60"
              :disabled="selectingId === agent.id"
            >
              <span class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded bg-sky-50 text-sky-700">
                <Bot :size="15" />
              </span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-xs font-semibold text-slate-800">{{ agent.name }}</span>
                <span class="block truncate text-[11px] text-slate-500">{{ modelDisplayName(agent.model_name) }}</span>
              </span>
              <span v-if="agent.id === activeAgent?.id" class="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">当前</span>
            </button>
          </div>
        </div>
      </template>

      <div v-else class="rounded border border-dashed border-sky-200 bg-sky-50/70 p-3 text-xs text-slate-500">
        还没有默认助手，先去工作台选择一个。
        <button @click="router.push('/agents')" class="mt-2 block rounded bg-white px-3 py-1.5 text-sky-700 hover:bg-sky-100">去选择</button>
      </div>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { modelDisplayName } from '../utils/displayNames'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BookOpen, Bot, Brain, Bug, KeyRound, MessageSquare, Shuffle } from 'lucide-vue-next'
import { useAgentSessionStore } from '../stores/agentSession'
import { useUserStore } from '../stores/user'
import * as agentApi from '../api/agent'
import * as llmApi from '../api/llmConfig'
import type { AgentInfo } from '../api/agent'
import { toastError, toastSuccess } from '../utils/toast'
import { getErrorMessage } from '../utils/request'

const route = useRoute()
const router = useRouter()
const agentSession = useAgentSessionStore()
const userStore = useUserStore()
const routeAgent = ref<AgentInfo | null>(null)
const agents = ref<AgentInfo[]>([])
const agentLoading = ref(false)
const chooserOpen = ref(false)
const selectingId = ref<number | null>(null)
const floatRoot = ref<HTMLElement | null>(null)
const keySettingsOpen = ref(false)
const apiKey = ref('')
const showKey = ref(false)
const keySaving = ref(false)
const saveEmbeddingKey = ref(true)
const chatModelName = ref('')

const routeAgentId = computed(() => {
  const value = route.params.agentId
  if (Array.isArray(value)) return Number(value[0])
  return value ? Number(value) : null
})

const isRouteAgent = computed(() => Boolean(routeAgentId.value))
const activeAgent = computed(() => routeAgent.value || agentSession.selectedAgent)
const canSaveKey = computed(() => chatModelName.value.trim().length > 0 && apiKey.value.trim().length > 0)
const defaultEmbeddingModel = computed(() => {
  const model = chatModelName.value.trim().toLowerCase()
  if (model.startsWith('gpt') || model.startsWith('o')) return 'text-embedding-3-small'
  if (model.startsWith('glm')) return 'embedding-3'
  return ''
})

const go = (tab: 'chat' | 'knowledge' | 'memory' | 'debug') => {
  const id = activeAgent.value?.id
  if (!id) {
    router.push('/agents')
    return
  }
  router.push(`/agents/${id}/${tab}`)
}

const loadAgents = async () => {
  agentLoading.value = true
  try {
    agents.value = await agentApi.listAgents()
  } catch (e: any) {
    toastError(getErrorMessage(e, '加载助手列表失败'))
  } finally {
    agentLoading.value = false
  }
}

const toggleChooser = async () => {
  chooserOpen.value = !chooserOpen.value
  if (chooserOpen.value) keySettingsOpen.value = false
  if (chooserOpen.value && agents.value.length === 0) {
    await loadAgents()
  }
}

const toggleKeySettings = () => {
  keySettingsOpen.value = !keySettingsOpen.value
  if (keySettingsOpen.value) {
    chooserOpen.value = false
    chatModelName.value = activeAgent.value?.model_name || chatModelName.value || 'glm-4'
    saveEmbeddingKey.value = activeAgent.value?.rag_enabled === 1 && Boolean(defaultEmbeddingModel.value)
  }
}

const saveKeySettings = async () => {
  if (!canSaveKey.value) return
  keySaving.value = true
  const key = apiKey.value.trim()
  const models = [chatModelName.value.trim()]
  if (saveEmbeddingKey.value && defaultEmbeddingModel.value) models.push(defaultEmbeddingModel.value)
  try {
    for (const modelName of Array.from(new Set(models))) {
      await llmApi.saveConfig({ model_name: modelName, api_key: key })
    }
    apiKey.value = ''
    showKey.value = false
    keySettingsOpen.value = false
    toastSuccess(`已保存 ${models.length} 项 Key`)
  } catch (e: any) {
    toastError(getErrorMessage(e, '保存 API Key 失败'))
  } finally {
    keySaving.value = false
  }
}

const chooseAgent = async (agent: AgentInfo) => {
  selectingId.value = agent.id
  try {
    await agentApi.selectAgent(agent.id)
    if (userStore.user) {
      userStore.user.selected_agent_id = agent.id
      localStorage.setItem('user', JSON.stringify(userStore.user))
    }
    agentSession.remember({ ...agent, is_selected: true })
    routeAgent.value = { ...agent, is_selected: true }
    chooserOpen.value = false
    toastSuccess(`已切换到「${agent.name}」`)
    router.push(`/agents/${agent.id}/chat`)
  } catch (e: any) {
    toastError(getErrorMessage(e, '切换助手失败'))
  } finally {
    selectingId.value = null
  }
}

const closeChooserOnOutsideClick = (event: MouseEvent) => {
  if (!chooserOpen.value && !keySettingsOpen.value) return
  const target = event.target
  if (!(target instanceof Node)) return
  if (floatRoot.value?.contains(target)) return
  chooserOpen.value = false
  keySettingsOpen.value = false
}

const closeChooserOnEscape = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    chooserOpen.value = false
    keySettingsOpen.value = false
  }
}

const loadActiveAgent = async () => {
  const id = routeAgentId.value
  if (id) {
    routeAgent.value = await agentSession.loadAgent(id)
    return
  }
  routeAgent.value = null
  await agentSession.loadSelected()
}

onMounted(() => {
  document.addEventListener('mousedown', closeChooserOnOutsideClick)
  document.addEventListener('keydown', closeChooserOnEscape)
  void loadActiveAgent()
})
onUnmounted(() => {
  document.removeEventListener('mousedown', closeChooserOnOutsideClick)
  document.removeEventListener('keydown', closeChooserOnEscape)
})
watch(routeAgentId, loadActiveAgent)
</script>

<style scoped>
.float-action {
  display: inline-flex;
  height: 2rem;
  align-items: center;
  justify-content: center;
  border-radius: 0.625rem;
  background: rgba(0, 0, 0, 0.05);
  color: var(--ink-2);
}

.float-action:hover {
  background: var(--accent-soft);
  color: var(--accent);
}
</style>
