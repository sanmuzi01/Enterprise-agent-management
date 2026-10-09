<template>
  <section class="rounded-xl border border-indigo-100 bg-white shadow-sm" data-testid="agent-hub">
    <div class="px-4 pt-4">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div class="flex min-w-0 items-center gap-2.5">
          <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white">
            <Sparkles :size="16" />
          </span>
          <div class="min-w-0">
            <h2 class="text-base font-semibold text-slate-900">今天需要我帮你处理什么？</h2>
            <p class="truncate text-xs text-slate-500" :title="agent?.description">
              {{ agent ? `${agent.name} · ${agent.description}` : '部门助手' }}
            </p>
          </div>
        </div>
        <RouterLink v-if="centralAgent" :to="`/agents/${centralAgent.id}/chat`" data-testid="central-entry"
          class="shrink-0 text-xs text-sky-700 hover:text-sky-900">不确定找哪个部门？问「{{ centralAgent.name }}」→</RouterLink>
      </div>

      <p v-if="agent && !agent.model_configured" class="mt-2 rounded bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
        需要先在「设置 → 模型连接」连接 {{ modelDisplayName(agent.model_name) }} 模型，部门助手才能回答。
      </p>

      <!-- 今日建议：来自首页已经算好的数字，点了才交给助手 -->
      <ul v-if="suggestions.length" class="mt-3 grid gap-2 sm:grid-cols-2" data-testid="agent-suggestions">
        <li v-for="s in suggestions" :key="s.key"
          class="flex items-center justify-between gap-2 rounded-lg border px-3 py-2"
          :class="s.tone === 'danger' ? 'border-red-200 bg-red-50/40' : s.tone === 'warn' ? 'border-amber-200 bg-amber-50/40' : 'border-slate-200'">
          <span class="min-w-0 truncate text-sm" :class="s.tone === 'danger' ? 'text-red-700' : s.tone === 'warn' ? 'text-amber-800' : 'text-slate-700'"
            :title="s.hint">{{ s.text }}</span>
          <span class="flex shrink-0 gap-1.5">
            <button v-if="agent" @click="ask(s.prompt)" :disabled="!canAsk" :data-testid="`suggestion-ask-${s.key}`"
              class="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50">让助手整理</button>
            <button @click="emit('openCard', s.card)" :data-testid="`suggestion-open-${s.key}`"
              class="rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50">去处理</button>
          </span>
        </li>
      </ul>
      <p v-else class="mt-3 text-xs text-slate-400" data-testid="agent-no-suggestions">今天没有需要马上处理的事项。</p>

      <div v-if="agent?.examples?.length" class="mt-3 flex flex-wrap gap-1.5" data-testid="agent-examples">
        <span class="text-xs leading-6 text-slate-400">可以这样说：</span>
        <button v-for="ex in agent.examples" :key="ex" @click="ask(ex)" :disabled="!canAsk"
          class="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50">{{ ex }}</button>
      </div>
    </div>

    <EmbeddedAgentChatPanel v-if="agent" ref="panel" class="mt-2" :agent-id="agent.id" height="440px" compact-when-empty
      empty-hint="" :placeholder="placeholder" @changed="emit('changed')" @open="(section) => emit('open', section)" />
    <p v-else class="mx-4 mb-4 mt-3 rounded border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-500">
      部门助手尚未发布：企业管理员完成配置并发布后，就可以在这里直接交代要办的事。下面的业务模块可以照常使用。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Sparkles } from 'lucide-vue-next'
import EmbeddedAgentChatPanel from '../EmbeddedAgentChatPanel.vue'
import type { HomeCard, WorkspaceAgent } from '../../api/enterpriseWorkspace'
import { suggestionsFromCards } from '../../utils/agentSuggestions'
import { modelDisplayName } from '../../utils/displayNames'

const props = defineProps<{
  agent: WorkspaceAgent | null
  centralAgent: WorkspaceAgent | null
  cards: HomeCard[]
}>()

const emit = defineEmits<{
  (e: 'changed'): void
  (e: 'open', section: 'office' | 'business'): void
  (e: 'openCard', card: HomeCard): void
}>()

const panel = ref<InstanceType<typeof EmbeddedAgentChatPanel> | null>(null)
const suggestions = computed(() => suggestionsFromCards(props.cards))
const canAsk = computed(() => !!props.agent?.model_configured)
const placeholder = computed(() => (props.agent?.examples?.[0] ? `例如：${props.agent.examples[0]}` : '说说你要办的事…'))

/** 代用户问一句：建议、示例问法、业务记录上的“让助手分析”都走这里 */
function ask(text: string) {
  if (!panel.value || panel.value.busy()) return
  void panel.value.ask(text)
}

defineExpose({ ask })
</script>
