<template>
  <div class="mx-auto max-w-4xl space-y-5 p-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold text-slate-900">我的待办</h1>
        <p class="mt-0.5 text-xs text-slate-500">审批提醒、客户跟进、报销缺票、AI 成果里的后续事项和你自己记的事，都在这里。</p>
      </div>
      <button class="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        :disabled="loading" @click="load">
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />刷新
      </button>
    </div>

    <div class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <div class="rounded-lg border border-red-100 bg-red-50 p-3 text-red-700">已逾期 <strong>{{ counts.overdue }}</strong></div>
      <div class="rounded-lg border border-amber-100 bg-amber-50 p-3 text-amber-800">今天到期 <strong>{{ counts.due_today }}</strong></div>
      <div class="rounded-lg border border-slate-200 bg-white p-3 text-slate-700">高优先级 <strong>{{ counts.high }}</strong></div>
      <div class="rounded-lg border border-slate-200 bg-white p-3 text-slate-700">全部待办 <strong>{{ counts.open }}</strong></div>
    </div>

    <p v-if="error" role="alert" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ error }}</p>

    <form class="flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 bg-white p-3" @submit.prevent="add">
      <label class="min-w-[12rem] flex-1 text-sm">新待办
        <input id="todo-title" v-model="draft.title" maxlength="200" placeholder="例如：周五前给客户回电话"
          class="mt-1 h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
      </label>
      <label class="text-sm">截止日期
        <input id="todo-due" v-model="draft.due" type="date" class="mt-1 h-9 rounded border border-slate-300 px-2 text-sm" />
      </label>
      <label class="text-sm">优先级
        <select id="todo-priority" v-model="draft.priority" class="mt-1 h-9 rounded border border-slate-300 px-2 text-sm">
          <option value="high">高</option><option value="normal">普通</option><option value="low">低</option>
        </select>
      </label>
      <button type="submit" :disabled="busy || !draft.title.trim()"
        class="h-9 rounded bg-indigo-600 px-4 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">添加</button>
    </form>

    <div class="inline-flex gap-1 rounded bg-slate-100 p-1 text-sm">
      <button v-for="t in tabs" :key="t.value" class="h-8 rounded px-4"
        :class="status === t.value ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
        @click="status = t.value; load()">{{ t.label }}</button>
    </div>

    <div v-if="loading && !items.length" class="py-10 text-center text-sm text-slate-400">加载中…</div>
    <p v-else-if="!items.length" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 py-10 text-center text-sm text-slate-500">
      {{ status === 'open' ? '没有待办，干得漂亮。' : '这里还没有记录。' }}
    </p>
    <section v-for="group in groups" v-else :key="group.label" class="space-y-2">
      <h2 class="text-xs font-semibold uppercase tracking-wide" :class="group.tone">{{ group.label }} · {{ group.items.length }}</h2>
      <ul class="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
        <li v-for="item in group.items" :key="item.id" class="flex items-start gap-3 px-4 py-3">
          <input type="checkbox" class="mt-1" :checked="item.status === 'done'" :disabled="busy || item.status === 'dismissed'"
            :aria-label="`完成：${item.title}`" @change="toggle(item, ($event.target as HTMLInputElement).checked)" />
          <div class="min-w-0 flex-1">
            <p class="text-sm" :class="item.status === 'open' ? 'text-slate-900' : 'text-slate-400 line-through'">
              <span v-if="item.priority === 'high'" class="mr-1 rounded bg-red-50 px-1.5 py-0.5 text-[11px] text-red-700">高</span>{{ item.title }}
            </p>
            <p v-if="item.detail" class="mt-0.5 text-xs text-slate-500">{{ item.detail }}</p>
            <p class="mt-1 text-[11px] text-slate-400">
              {{ sourceLabel(item) }}<span v-if="item.due_at"> · 截止 {{ formatDue(item.due_at) }}</span>
              <span v-if="item.resolved_by === 'rule'"> · 条件已解除，系统自动关闭</span>
            </p>
          </div>
          <div class="flex shrink-0 gap-2 text-xs">
            <RouterLink v-if="item.link && item.status === 'open'" :to="item.link" class="text-indigo-600 hover:underline">去处理</RouterLink>
            <button v-if="item.status === 'open'" class="text-slate-500 hover:text-slate-700" :disabled="busy" @click="setStatus(item, 'dismissed')">忽略</button>
            <button v-else class="text-slate-500 hover:text-slate-700" :disabled="busy" @click="setStatus(item, 'open')">重新打开</button>
          </div>
        </li>
      </ul>
    </section>

    <details class="rounded-lg border border-slate-200 bg-white p-4 text-sm" @toggle="loadPreference">
      <summary class="cursor-pointer font-medium text-slate-700">通知设置</summary>
      <div v-if="pref" class="mt-3 space-y-3">
        <p class="text-xs text-slate-500">静音的类别不再产生通知（待办仍会出现）；免打扰时段内不推送到外部群机器人，站内通知照常保留。</p>
        <div class="flex flex-wrap gap-3">
          <label v-for="c in pref.categories" :key="c.value" class="flex items-center gap-1.5">
            <input type="checkbox" :checked="pref.muted_categories.includes(c.value)" @change="toggleMute(c.value)" />静音{{ c.label }}
          </label>
        </div>
        <label class="flex items-center gap-1.5"><input v-model="pref.push_external" type="checkbox" />同时推送到我配置的外部通知通道</label>
        <div class="flex flex-wrap items-center gap-2">
          <span>免打扰（北京时间）</span>
          <input v-model="quietStart" type="time" aria-label="免打扰开始" class="h-8 rounded border border-slate-300 px-2" />
          <span>至</span>
          <input v-model="quietEnd" type="time" aria-label="免打扰结束" class="h-8 rounded border border-slate-300 px-2" />
        </div>
        <button class="rounded bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-700 disabled:opacity-50" :disabled="busy" @click="savePreference">保存通知设置</button>
        <span v-if="prefSaved" class="ml-2 text-xs text-emerald-700">已保存</span>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import * as api from '../api/workCenter'
import { useWorkCenterStore } from '../stores/workCenter'
import { getErrorMessage } from '../utils/request'

const store = useWorkCenterStore()
const tabs = [{ value: 'open', label: '待处理' }, { value: 'done', label: '已完成' }, { value: 'dismissed', label: '已忽略' }] as const
const status = ref<api.WorkItemStatus>('open')
const items = ref<api.WorkItem[]>([])
const counts = ref<api.WorkItemCounts>({ open: 0, overdue: 0, due_today: 0, high: 0 })
const loading = ref(false), busy = ref(false), error = ref('')
const draft = ref({ title: '', due: '', priority: 'normal' as api.Priority })
const pref = ref<api.NotificationPreference | null>(null)
const quietStart = ref(''), quietEnd = ref(''), prefSaved = ref(false)

const SOURCES: Record<string, string> = { reminder: '业务提醒', automation: 'AI 成果后续事项', manual: '我记的' }
const sourceLabel = (item: api.WorkItem) => SOURCES[item.source_type] || item.source_type
const formatDue = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

const groups = computed(() => {
  if (status.value !== 'open') return [{ label: tabs.find(t => t.value === status.value)!.label, tone: 'text-slate-500', items: items.value }]
  const now = Date.now()
  // 截止于次日零点（如请假当天开始前）也算今天要处理。
  const endOfToday = new Date(); endOfToday.setHours(24, 0, 0, 0)
  const buckets = [
    { label: '已逾期', tone: 'text-red-600', items: [] as api.WorkItem[] },
    { label: '今天到期', tone: 'text-amber-700', items: [] as api.WorkItem[] },
    { label: '之后', tone: 'text-slate-500', items: [] as api.WorkItem[] },
    { label: '没有截止时间', tone: 'text-slate-500', items: [] as api.WorkItem[] },
  ]
  for (const item of items.value) {
    const due = item.due_at ? new Date(item.due_at).getTime() : null
    const bucket = due === null ? 3 : due < now ? 0 : due <= endOfToday.getTime() ? 1 : 2
    buckets[bucket].items.push(item)
  }
  return buckets.filter(b => b.items.length)
})

async function load() {
  loading.value = true; error.value = ''
  try {
    const data = await api.listWorkItems(status.value)
    items.value = data.items; counts.value = data.counts
    store.counts = data.counts
  } catch (e) { error.value = getErrorMessage(e, '加载待办失败') } finally { loading.value = false }
}
async function run(action: () => Promise<unknown>, fallback: string) {
  busy.value = true; error.value = ''
  try { await action(); await load() } catch (e) { error.value = getErrorMessage(e, fallback) } finally { busy.value = false }
}
const add = () => run(async () => {
  await api.createWorkItem({ title: draft.value.title.trim(), due_at: draft.value.due || null, priority: draft.value.priority })
  draft.value = { title: '', due: '', priority: 'normal' }
}, '添加失败')
const setStatus = (item: api.WorkItem, next: api.WorkItemStatus) => run(() => api.setWorkItemStatus(item.id, next), '更新失败')
const toggle = (item: api.WorkItem, done: boolean) => setStatus(item, done ? 'done' : 'open')

async function loadPreference() {
  if (pref.value) return
  try {
    pref.value = await api.getNotificationPreference()
    quietStart.value = pref.value.quiet_start || ''; quietEnd.value = pref.value.quiet_end || ''
  } catch (e) { error.value = getErrorMessage(e, '加载通知设置失败') }
}
function toggleMute(category: string) {
  if (!pref.value) return
  const muted = new Set(pref.value.muted_categories)
  muted.has(category) ? muted.delete(category) : muted.add(category)
  pref.value.muted_categories = [...muted]
}
async function savePreference() {
  if (!pref.value) return
  busy.value = true; error.value = ''; prefSaved.value = false
  try {
    pref.value = await api.updateNotificationPreference({
      muted_categories: pref.value.muted_categories, push_external: pref.value.push_external,
      quiet_start: quietStart.value || null, quiet_end: quietEnd.value || null,
    })
    prefSaved.value = true
  } catch (e) { error.value = getErrorMessage(e, '保存通知设置失败') } finally { busy.value = false }
}

onMounted(load)
</script>
