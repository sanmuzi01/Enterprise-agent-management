<template>
  <div class="rounded border border-slate-200" data-testid="customer-copilot">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
      <div class="flex gap-1" role="tablist">
        <button v-for="t in tabs" :key="t.key" role="tab" :aria-selected="tab === t.key" @click="tab = t.key"
          :class="['rounded px-2 py-1 text-xs', tab === t.key ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-500 hover:text-slate-800']">
          {{ t.label }}<span v-if="t.key === 'risks' && risks.length" class="ml-1 rounded bg-red-50 px-1 text-red-600">{{ risks.length }}</span>
        </button>
      </div>
      <div class="flex items-center gap-1.5 text-xs">
        <label for="copilot-model" class="text-slate-500">模型</label>
        <select id="copilot-model" v-model="modelName" class="h-7 max-w-[160px] rounded border border-slate-300 px-1.5">
          <option value="">{{ models.length ? '请选择' : '没有可用模型' }}</option>
          <option v-for="m in models" :key="m.model_name" :value="m.model_name">{{ modelDisplayName(m.model_name) }}</option>
        </select>
      </div>
    </div>

    <!-- 时间线 -->
    <div v-if="tab === 'timeline'" class="p-3" data-testid="copilot-timeline">
      <form class="mb-3 grid gap-1.5 rounded bg-slate-50 p-2 text-xs sm:grid-cols-[110px_1fr]" @submit.prevent="addActivity">
        <label for="act-type" class="sr-only">类型</label>
        <select id="act-type" v-model="form.type" class="h-8 rounded border border-slate-300 px-1.5">
          <option v-for="(label, key) in MANUAL_TYPES" :key="key" :value="key">{{ label }}</option>
        </select>
        <label for="act-title" class="sr-only">标题</label>
        <input id="act-title" v-model="form.title" placeholder="标题（例：电话确认预算）" class="h-8 rounded border border-slate-300 px-2" />
        <label for="act-content" class="sr-only">内容</label>
        <textarea id="act-content" v-model="form.content" rows="2" placeholder="内容" class="rounded border border-slate-300 px-2 py-1 sm:col-span-2" />
        <div class="sm:col-span-2">
          <button type="submit" :disabled="busy || (!form.title.trim() && !form.content.trim())"
            class="rounded bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">记录</button>
        </div>
      </form>
      <ol class="space-y-2.5 border-l border-slate-200 pl-3">
        <li v-for="item in timeline" :key="item.id" class="relative text-xs">
          <span class="absolute -left-[17px] top-1 h-2 w-2 rounded-full" :class="dotClass(item.kind)" aria-hidden="true"></span>
          <p class="flex flex-wrap items-baseline gap-x-2">
            <span class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{{ item.type_label }}</span>
            <span class="font-medium text-slate-800">{{ item.title }}</span>
            <span class="text-slate-400">{{ formatTime(item.occurred_at) }}</span>
            <span v-if="item.status" class="text-slate-400">{{ item.status }}</span>
          </p>
          <p v-if="item.content" class="mt-0.5 line-clamp-3 whitespace-pre-line text-slate-600">{{ item.content }}</p>
        </li>
        <li v-if="!timeline.length" class="text-xs text-slate-400">还没有沟通记录。导入邮件、会议，或在上面手工记录。</li>
      </ol>
    </div>

    <!-- 摘要 -->
    <div v-else-if="tab === 'summary'" class="space-y-2 p-3 text-xs" data-testid="copilot-summary">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-slate-500">
          <template v-if="snapshot">更新于 {{ formatTime(snapshot.generated_at) }}<span v-if="hasNew" class="ml-1 text-amber-600">· 有新的沟通记录</span></template>
          <template v-else>还没有摘要</template>
        </p>
        <button :disabled="busy || !modelName" @click="refreshSummary" class="rounded bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">
          {{ snapshot ? '用新记录更新摘要' : '生成摘要' }}
        </button>
      </div>
      <template v-if="snapshot">
        <p class="whitespace-pre-line leading-relaxed text-slate-800">{{ snapshot.summary }}</p>
        <div v-for="part in summaryParts" :key="part.label">
          <h4 class="font-semibold text-slate-500">{{ part.label }}</h4>
          <ul class="mt-0.5 list-disc space-y-0.5 pl-4 text-slate-700">
            <li v-for="(line, i) in part.items" :key="i">{{ line }}</li>
          </ul>
        </div>
      </template>
    </div>

    <!-- 风险与建议 -->
    <div v-else class="space-y-3 p-3 text-xs" data-testid="copilot-risks">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-slate-500">规则检查每天自动跑；选了模型还会从沟通原文里找预算、决策人、竞争对手等风险。</p>
        <button :disabled="busy" @click="scan" class="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:opacity-50">现在检查</button>
      </div>
      <ul class="space-y-2">
        <li v-for="r in risks" :key="r.id" class="rounded border px-2.5 py-2" :class="levelClass[r.level]">
          <p class="flex flex-wrap items-center gap-2">
            <span class="font-semibold">{{ r.label }}</span>
            <span class="rounded bg-white/70 px-1">{{ levelText[r.level] }}</span>
            <span v-if="r.source === 'model'" class="text-slate-500">模型从原文识别</span>
            <button class="ml-auto text-slate-500 hover:text-slate-800" @click="dismiss(r)">关闭</button>
          </p>
          <p class="mt-1 text-slate-700">依据：{{ r.evidence }}</p>
        </li>
        <li v-if="!risks.length" class="text-slate-400">没有发现风险</li>
      </ul>
      <div>
        <h4 class="mb-1 font-semibold text-slate-500">建议的下一步（确认后才会进你的待办，不会自动修改商机或联系客户）</h4>
        <ul class="space-y-2">
          <li v-for="s in suggestions" :key="s.id" class="rounded border border-slate-200 px-2.5 py-2">
            <template v-if="editing === s.id">
              <label :for="`sg-title-${s.id}`" class="sr-only">任务内容</label>
              <input :id="`sg-title-${s.id}`" v-model="edit.title" class="h-8 w-full rounded border border-slate-300 px-2" />
              <div class="mt-1.5 flex flex-wrap items-center gap-2">
                <label :for="`sg-due-${s.id}`" class="text-slate-500">截止</label>
                <input :id="`sg-due-${s.id}`" v-model="edit.due" type="date" class="h-7 rounded border border-slate-300 px-1.5" />
                <button :disabled="busy || !edit.title.trim()" @click="decide(s, 'edit_create')" class="rounded bg-indigo-600 px-2 py-1 text-white disabled:opacity-50">创建任务</button>
                <button @click="editing = null" class="rounded px-2 py-1 text-slate-500 hover:bg-slate-100">取消</button>
              </div>
            </template>
            <template v-else>
              <p class="font-medium text-slate-800">{{ s.title }}</p>
              <p v-if="s.detail" class="mt-0.5 text-slate-500">{{ s.detail }}</p>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <button :disabled="busy" @click="decide(s, 'create')" class="rounded bg-indigo-600 px-2 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">确认创建任务</button>
                <button :disabled="busy" @click="startEdit(s)" class="rounded border border-slate-300 px-2 py-1 text-slate-700 hover:bg-slate-50">修改后创建</button>
                <button :disabled="busy" @click="decide(s, 'ignore')" class="rounded border border-slate-300 px-2 py-1 text-slate-700 hover:bg-slate-50">忽略</button>
                <button :disabled="busy" @click="decide(s, 'snooze')" class="rounded border border-slate-300 px-2 py-1 text-slate-700 hover:bg-slate-50">3 天后提醒</button>
              </div>
            </template>
          </li>
          <li v-if="!suggestions.length" class="text-slate-400">没有待处理的建议</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import * as api from '../../api/crmCopilot'
import type { RiskFinding, Suggestion, SummarySnapshot, TimelineItem } from '../../api/crmCopilot'
import { modelDisplayName } from '../../utils/displayNames'
import { loadUsableChatModels } from '../../utils/loadUsableModels'
import type { UsableModel } from '../../utils/usableModels'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'

const props = defineProps<{ teamId: number; customerId: number }>()

const tabs = [{ key: 'timeline', label: '时间线' }, { key: 'summary', label: '客户摘要' }, { key: 'risks', label: '风险与建议' }] as const
const tab = ref<'timeline' | 'summary' | 'risks'>('timeline')
const MANUAL_TYPES: Record<string, string> = { call: '电话纪要', followup: '人工跟进', quote: '报价', meeting: '会议', todo: '待办' }
const levelText = { high: '高', medium: '中', low: '低' }
const levelClass = { high: 'border-red-200 bg-red-50 text-red-800', medium: 'border-amber-200 bg-amber-50 text-amber-900', low: 'border-slate-200 bg-slate-50 text-slate-700' }

const timeline = ref<TimelineItem[]>([])
const snapshot = ref<SummarySnapshot | null>(null)
const hasNew = ref(false)
const risks = ref<RiskFinding[]>([])
const suggestions = ref<Suggestion[]>([])
const models = ref<UsableModel[]>([])
const modelName = ref('')
const busy = ref(false)
const form = reactive({ type: 'call', title: '', content: '' })
const editing = ref<number | null>(null)
const edit = reactive({ title: '', due: '' })

const summaryParts = computed(() => snapshot.value ? [
  { label: '客户需求', items: snapshot.value.needs },
  { label: '关键人', items: snapshot.value.stakeholders },
  { label: '风险', items: snapshot.value.risks },
  { label: '下一步', items: snapshot.value.next_actions },
].filter((p) => p.items.length) : [])

const formatTime = (iso: string) => new Date(iso).toLocaleString('zh-CN', { hour12: false }).slice(0, 16)
const dotClass = (kind: string) => ({
  email: 'bg-sky-500', meeting: 'bg-violet-500', chat: 'bg-teal-500', call: 'bg-emerald-500', quote: 'bg-amber-500',
  stage_change: 'bg-indigo-500', todo: 'bg-slate-500',
} as Record<string, string>)[kind] || 'bg-slate-400'

async function run<T>(fn: () => Promise<T>, fallback: string): Promise<T | undefined> {
  busy.value = true
  try {
    return await fn()
  } catch (e) {
    toastError(getErrorMessage(e, fallback))
    return undefined
  } finally {
    busy.value = false
  }
}

async function load() {
  const [t, s, r, sg] = await Promise.all([
    api.getTimeline(props.teamId, props.customerId).catch(() => ({ items: [] })),
    api.getSummary(props.teamId, props.customerId).catch(() => ({ snapshot: null, has_new_activities: false })),
    api.listRisks(props.teamId, props.customerId).catch(() => []),
    api.listSuggestions(props.teamId, props.customerId).catch(() => []),
  ])
  timeline.value = t.items
  snapshot.value = s.snapshot
  hasNew.value = s.has_new_activities
  risks.value = r
  suggestions.value = sg
}

async function addActivity() {
  const result = await run(() => api.addActivity(props.teamId, props.customerId, form.type, form.title, form.content), '记录失败')
  if (!result) return
  form.title = ''
  form.content = ''
  await load()
}

async function refreshSummary() {
  const result = await run(() => api.refreshSummary(props.teamId, props.customerId, modelName.value), '生成摘要失败')
  if (!result) return
  toastSuccess(result.unchanged ? '没有新的沟通记录，摘要不用更新' : '摘要已更新')
  await load()
}

async function scan() {
  const result = await run(() => api.scanRisks(props.teamId, props.customerId, modelName.value || undefined), '检查失败')
  if (result) await load()
}

async function dismiss(r: RiskFinding) {
  if (await run(() => api.dismissRisk(props.teamId, r.id), '操作失败') !== undefined) await load()
}

function startEdit(s: Suggestion) {
  editing.value = s.id
  edit.title = s.title
  edit.due = s.due_date || ''
}

async function decide(s: Suggestion, decision: 'create' | 'edit_create' | 'ignore' | 'snooze') {
  const extra = decision === 'edit_create' ? { title: edit.title.trim(), due_date: edit.due || undefined } : decision === 'snooze' ? { remind_days: 3 } : {}
  const result = await run(() => api.decideSuggestion(props.teamId, s.id, decision, extra), '操作失败')
  if (!result) return
  editing.value = null
  toastSuccess({ create: '已加入待办', edit_create: '已加入待办', ignore: '已忽略', snooze: '3 天后再提醒' }[decision])
  await load()
}

watch(() => [props.teamId, props.customerId], load)
onMounted(async () => {
  await load()
  models.value = await loadUsableChatModels()
  modelName.value = models.value[0]?.model_name || ''
})
defineExpose({ load })
</script>
