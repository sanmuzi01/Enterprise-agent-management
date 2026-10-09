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

      <!-- 负责人的今日摘要：把工作台上的数字交给助手排出今天的处理顺序 -->
      <div v-if="isHead && agent" class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-indigo-50/60 px-3 py-2">
        <span class="text-xs text-indigo-900">作为负责人，让助手把今天的待审批、逾期和风险排个先后。</span>
        <button @click="ask(dailyBriefPrompt(cards, departmentName))" :disabled="!canAsk" data-testid="agent-daily-brief"
          class="rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700 disabled:opacity-50">生成今日摘要</button>
      </div>

      <!-- 今日发现：来自首页已经算好的数字（不调模型）；点了才交给助手，部分可以直接一键处理 -->
      <p v-if="found.total" class="mt-3 text-xs text-slate-600" data-testid="agent-found">
        今天发现 <strong class="text-slate-900">{{ found.total }}</strong> 件需要处理的事<span v-if="found.risky">，其中
        <strong class="text-red-600">{{ found.risky }}</strong> 件已逾期或有风险</span><span v-if="found.total > suggestions.length">，先列最急的 {{ suggestions.length }} 件</span>。
      </p>
      <p v-if="quickResult" class="mt-2 rounded px-3 py-1.5 text-xs" data-testid="agent-quick-result"
        :class="quickResult.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'">{{ quickResult.text }}</p>
      <ul v-if="suggestions.length" class="mt-2 grid gap-2 sm:grid-cols-2" data-testid="agent-suggestions">
        <li v-for="s in suggestions" :key="s.key"
          class="flex items-center justify-between gap-2 rounded-lg border px-3 py-2"
          :class="s.tone === 'danger' ? 'border-red-200 bg-red-50/40' : s.tone === 'warn' ? 'border-amber-200 bg-amber-50/40' : 'border-slate-200'">
          <span class="min-w-0 truncate text-sm" :class="s.tone === 'danger' ? 'text-red-700' : s.tone === 'warn' ? 'text-amber-800' : 'text-slate-700'"
            :title="s.hint">{{ s.text }}</span>
          <span class="flex shrink-0 gap-1.5">
            <button v-if="s.quickAction" @click="runQuick(s.quickAction)" :disabled="quickBusy" :data-testid="`suggestion-quick-${s.key}`"
              class="rounded bg-emerald-600 px-2 py-0.5 text-xs text-white hover:bg-emerald-700 disabled:opacity-50">
              {{ quickBusy ? '处理中…' : QUICK_ACTION_LABELS[s.quickAction] }}</button>
            <button v-else-if="agent" @click="ask(s.prompt)" :disabled="!canAsk" :data-testid="`suggestion-ask-${s.key}`"
              class="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50">让助手整理</button>
            <button @click="emit('openCard', s.card)" :data-testid="`suggestion-open-${s.key}`"
              class="rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50">去处理</button>
          </span>
        </li>
      </ul>
      <p v-else class="mt-3 text-xs text-slate-400" data-testid="agent-no-suggestions">今天没有需要马上处理的事项。</p>

      <!-- 招牌场景：粘贴原始材料，一次办到草稿为止 -->
      <div v-if="agent" class="mt-3 rounded-lg border border-slate-200 px-3 py-2.5" data-testid="agent-flagship">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h3 class="text-sm font-medium text-slate-900">{{ scenario.title }}</h3>
          <ol class="flex flex-wrap items-center gap-1 text-[11px] text-slate-500">
            <li v-for="(s, i) in scenario.steps" :key="s" class="flex items-center gap-1">
              <span v-if="i" class="text-slate-300">→</span><span class="rounded bg-slate-100 px-1.5 py-0.5">{{ s }}</span>
            </li>
          </ol>
        </div>
        <div class="mt-2 flex flex-col gap-2 sm:flex-row sm:items-end">
          <textarea id="agent-flagship-input" v-model="scenarioInput" rows="2" :placeholder="scenario.placeholder" data-testid="agent-flagship-input"
            class="min-h-[3rem] flex-1 resize-y rounded border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-400"></textarea>
          <button @click="runScenario" :disabled="!canAsk || !scenarioInput.trim()" data-testid="agent-flagship-run"
            class="shrink-0 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">交给助手办理</button>
        </div>
      </div>

      <div v-if="agent?.examples?.length" class="mt-3 flex flex-wrap gap-1.5" data-testid="agent-examples">
        <span class="text-xs leading-6 text-slate-400">可以这样说：</span>
        <button v-for="ex in agent.examples" :key="ex" @click="ask(ex)" :disabled="!canAsk"
          class="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50">{{ ex }}</button>
      </div>
    </div>

    <EmbeddedAgentChatPanel v-if="agent" ref="panel" class="mt-2" :agent-id="agent.id" height="440px" compact-when-empty
      empty-hint="" :placeholder="placeholder" @changed="emit('changed')" @open="(target) => emit('open', target)" />
    <p v-else class="mx-4 mb-4 mt-3 rounded border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-500">
      部门助手尚未发布：企业管理员完成配置并发布后，就可以在这里直接交代要办的事。下面的业务模块可以照常使用。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Sparkles } from 'lucide-vue-next'
import EmbeddedAgentChatPanel from '../EmbeddedAgentChatPanel.vue'
import type { HomeCard, WorkspaceAgent } from '../../api/enterpriseWorkspace'
import { QUICK_ACTION_LABELS, allSuggestions, suggestionsFromCards, type QuickAction } from '../../utils/agentSuggestions'
import { generateFromClaim, listUnbooked } from '../../api/financeVouchers'
import { deskAssign, deskTickets } from '../../api/itService'
import { getErrorMessage } from '../../utils/request'
import type { CardTarget } from '../../utils/agentCards'
import { modelDisplayName } from '../../utils/displayNames'
import { dailyBriefPrompt, scenarioFor } from '../../utils/flagshipScenarios'

const props = defineProps<{
  agent: WorkspaceAgent | null
  centralAgent: WorkspaceAgent | null
  cards: HomeCard[]
  /** 部门业务类型，决定招牌场景 */
  departmentCode: string | null
  departmentName: string
  /** 部门负责人或企业管理员：显示“生成今日摘要” */
  isHead: boolean
  teamId: number
}>()

const emit = defineEmits<{
  (e: 'changed'): void
  (e: 'open', target: CardTarget): void
  (e: 'openCard', card: HomeCard): void
}>()

const panel = ref<InstanceType<typeof EmbeddedAgentChatPanel> | null>(null)
const suggestions = computed(() => suggestionsFromCards(props.cards))
const found = computed(() => {
  const all = allSuggestions(props.cards)
  return { total: all.length, risky: all.filter((s) => s.tone === 'danger').length }
})

// ---- 一键处理：不经过助手，调用工作台里同样的接口；点按钮本身就是人的决定 ----
const quickBusy = ref(false)
const quickResult = ref<{ ok: boolean; text: string } | null>(null)
// 任务令牌：每次开始一键处理、每次切换部门都换一个。处理途中切走了部门，旧任务看到令牌变了就停下：
// 不再对旧部门继续调用，也不把旧部门的结果显示到新部门里。
let quickToken = 0

async function runQuick(action: QuickAction) {
  if (quickBusy.value) return
  const token = ++quickToken
  const teamId = props.teamId   // 整个任务只用开始时的部门，不在途中读 props（切换后它已经是新部门）
  const stale = () => token !== quickToken
  quickBusy.value = true
  quickResult.value = null
  try {
    if (action === 'generate_vouchers') {
      const claims = await listUnbooked(teamId)
      let ok = 0
      const failed: string[] = []
      for (const c of claims) {
        if (stale()) return
        try {
          await generateFromClaim(teamId, c.id)
          ok += 1
        } catch (e) {
          failed.push(`报销单 #${c.id}：${getErrorMessage(e, '生成失败')}`)
        }
      }
      if (stale()) return
      quickResult.value = failed.length
        ? { ok: ok > 0, text: `已为 ${ok} 笔报销生成凭证草稿；${failed.length} 笔没有成功（${failed.join('；')}）` }
        : { ok: true, text: ok ? `已为 ${ok} 笔报销生成凭证草稿，请到「财务记账」逐张核对后入账` : '没有需要生成凭证的报销了' }
      if (ok) {
        emit('changed')
        emit('open', { section: 'business' })
      }
    } else {
      const queue = await deskTickets(teamId, { assignee: 'unassigned' })
      if (stale()) return
      const ticket = queue[0]   // 服务台队列按处理时限排序，最急的在前
      if (!ticket) {
        quickResult.value = { ok: true, text: '现在没有待接单的工单了' }
      } else {
        await deskAssign(teamId, ticket.id, { take: true })
        if (stale()) return
        quickResult.value = { ok: true, text: `已接单：工单 #${ticket.id}「${ticket.title}」，已为你打开` }
        emit('changed')
        emit('open', { section: 'business', record: { kind: 'ticket', id: ticket.id } })
      }
    }
  } catch (e) {
    if (!stale()) quickResult.value = { ok: false, text: getErrorMessage(e, '处理失败，请到对应分区里手动处理') }
  } finally {
    if (!stale()) quickBusy.value = false
  }
}
const canAsk = computed(() => !!props.agent?.model_configured)
const placeholder = computed(() => (props.agent?.examples?.[0] ? `例如：${props.agent.examples[0]}` : '说说你要办的事…'))

const scenario = computed(() => scenarioFor(props.departmentCode))
const scenarioInput = ref('')
// 切换部门（以部门本身为准：同为销售类型的“销售一部 → 销售二部”也算）时，清掉上一个部门留下的一切：
// 招牌场景里粘贴的材料、一键处理的结果和“处理中”状态；正在跑的一键处理作废（见 quickToken）。
watch(() => props.teamId, () => {
  quickToken += 1
  scenarioInput.value = ''
  quickResult.value = null
  quickBusy.value = false
})

/** 代用户问一句：建议、示例问法、业务记录上的“让助手分析”都走这里 */
function ask(text: string): boolean {
  if (!panel.value || panel.value.busy()) return false
  void panel.value.ask(text)
  return true
}

function runScenario() {
  const input = scenarioInput.value.trim()
  if (input && ask(scenario.value.buildPrompt(input))) scenarioInput.value = ''
}

defineExpose({ ask })
</script>
