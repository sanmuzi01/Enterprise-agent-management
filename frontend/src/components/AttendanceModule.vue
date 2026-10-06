<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="att-module">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">考勤异常</h2>
        <p class="mt-0.5 text-xs text-slate-400">数据来自人事导入的打卡机/钉钉/企业微信考勤文件，并对照已批准的请假和工作日历；系统按规则判断，不使用模型。异常由本人说明，人事或部门负责人认定。</p>
      </div>
      <div class="flex flex-wrap items-center gap-1 text-xs">
        <button v-for="t in tabs" :key="t.value" @click="tab = t.value" class="rounded px-2.5 py-1" :data-testid="`att-tab-${t.value}`"
          :class="tab === t.value ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50'">
          {{ t.label }}<span v-if="t.count" class="ml-1 rounded-full bg-white/80 px-1.5 text-indigo-700">{{ t.count }}</span>
        </button>
      </div>
    </div>
    <p v-if="error" class="mx-4 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{{ error }}</p>
    <p v-if="notice" class="mx-4 mt-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700" data-testid="att-notice">{{ notice }}</p>

    <!-- 我的异常 -->
    <div v-if="tab === 'mine'" class="px-4 py-4" data-testid="att-mine">
      <div class="mb-3 flex items-center gap-2 text-xs">
        <button v-for="f in mineFilters" :key="f.value" class="rounded-full px-3 py-1" @click="setMineFilter(f.value)"
          :class="mineFilter === f.value ? 'bg-slate-800 text-white' : 'border border-slate-200 text-slate-600'">{{ f.label }}</button>
      </div>
      <ul class="divide-y divide-slate-100 rounded border border-slate-200">
        <li v-for="a in mine" :key="a.id" class="px-3 py-3" :data-testid="`att-row-${a.id}`">
          <AnomalyLine :a="a" />
          <p v-if="a.explanation" class="mt-1 text-xs text-slate-600">我的说明：{{ a.explanation }}</p>
          <p v-if="a.decision_note" class="mt-1 text-xs text-slate-500">{{ a.status_label }}<span v-if="a.decided_by_name">（{{ a.decided_by_name }}）</span>：{{ a.decision_note }}</p>
          <div v-if="a.status === 'open' || a.status === 'explained'" class="mt-2 flex gap-2">
            <input :id="`att-explain-${a.id}`" v-model="drafts[a.id]" maxlength="500" placeholder="写明原因，比如忘打卡、外出办事、设备故障" class="min-w-0 flex-1 rounded border border-slate-200 px-2 py-1 text-xs" />
            <button class="shrink-0 rounded bg-indigo-600 px-3 py-1 text-xs text-white disabled:bg-slate-300" :disabled="busy || (drafts[a.id] || '').trim().length < 2" @click="explain(a)"
              :data-testid="`att-explain-btn-${a.id}`">{{ a.status === 'open' ? '提交说明' : '修改说明' }}</button>
          </div>
        </li>
        <li v-if="!mine.length" class="px-3 py-8 text-center text-sm text-slate-400" data-testid="att-mine-empty">{{ mineFilter === 'todo' ? '没有需要你说明的考勤异常' : '没有考勤异常记录' }}</li>
      </ul>
    </div>

    <!-- 待认定（负责人 / 人事） -->
    <div v-else-if="tab === 'team'" class="space-y-3 px-4 py-4" data-testid="att-team">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <label for="att-team-start" class="text-slate-500">从</label><input id="att-team-start" v-model="range.start" type="date" class="rounded border border-slate-200 px-2 py-1" />
        <label for="att-team-end" class="text-slate-500">到</label><input id="att-team-end" v-model="range.end" type="date" class="rounded border border-slate-200 px-2 py-1" />
        <select v-model="teamFilter" aria-label="状态" class="rounded border border-slate-200 px-2 py-1">
          <option value="open,explained">待处理</option><option value="">全部</option><option value="confirmed,dismissed">已认定</option>
        </select>
        <button class="rounded border border-slate-200 px-3 py-1 text-slate-600 hover:bg-slate-50" @click="loadTeam">查询</button>
      </div>
      <p v-if="summary" class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="att-narrative">{{ summary.narrative }}</p>
      <ul class="divide-y divide-slate-100 rounded border border-slate-200">
        <li v-for="a in team" :key="a.id" class="px-3 py-3" :data-testid="`att-team-row-${a.id}`">
          <AnomalyLine :a="a" show-name />
          <p v-if="a.explanation" class="mt-1 text-xs text-slate-600">员工说明：{{ a.explanation }}</p>
          <p v-else-if="a.status === 'open'" class="mt-1 text-xs text-amber-700">员工还没有说明</p>
          <p v-if="a.decision_note" class="mt-1 text-xs text-slate-500">{{ a.status_label }}<span v-if="a.decided_by_name">（{{ a.decided_by_name }}）</span>：{{ a.decision_note }}</p>
          <div v-if="(a.status === 'open' || a.status === 'explained') && a.user_id !== me.user_id" class="mt-2 flex flex-wrap gap-2">
            <input :id="`att-note-${a.id}`" v-model="notes[a.id]" maxlength="500" placeholder="认定理由（必填）" class="min-w-0 flex-1 rounded border border-slate-200 px-2 py-1 text-xs" />
            <button class="rounded border border-red-200 px-3 py-1 text-xs text-red-700 disabled:text-slate-300" :disabled="busy || (notes[a.id] || '').trim().length < 2" @click="decide(a, 'confirm')" :data-testid="`att-confirm-${a.id}`">认定为异常</button>
            <button class="rounded border border-emerald-200 px-3 py-1 text-xs text-emerald-700 disabled:text-slate-300" :disabled="busy || (notes[a.id] || '').trim().length < 2" @click="decide(a, 'dismiss')" :data-testid="`att-dismiss-${a.id}`">认定为正常</button>
          </div>
          <p v-else-if="(a.status === 'open' || a.status === 'explained') && a.user_id === me.user_id" class="mt-1 text-xs text-slate-400">自己的异常需要由人事或其他负责人认定</p>
        </li>
        <li v-if="!team.length" class="px-3 py-8 text-center text-sm text-slate-400">没有符合条件的考勤异常</li>
      </ul>
    </div>

    <!-- 导入与分析（人事） -->
    <div v-else-if="tab === 'import'" class="space-y-5 px-4 py-4" data-testid="att-import">
      <div>
        <h3 class="mb-1 text-xs font-medium text-slate-500">1. 导入考勤文件</h3>
        <p class="mb-2 text-xs text-slate-400">支持 .xlsx / .csv：打卡机、钉钉、企业微信导出的“打卡明细”或“每日汇总”都可以，系统会自动找表头。重复导入同一份不会产生重复记录。</p>
        <input id="att-file" type="file" accept=".xlsx,.csv" class="text-xs" data-testid="att-file" @change="onPick" />
        <button class="ml-2 rounded bg-indigo-600 px-3 py-1 text-xs text-white disabled:bg-slate-300" :disabled="busy || !file" @click="upload" data-testid="att-upload">导入</button>
        <div v-if="imported" class="mt-3 rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700" data-testid="att-import-result">
          <p>{{ imported.format_label }}格式，{{ imported.rows }} 行，识别 {{ imported.matched_people }} 人、{{ imported.punches }} 条打卡，新增 {{ imported.new_punches }} 条（{{ imported.duplicate_punches }} 条已存在）。区间 {{ imported.period[0] || '—' }} 至 {{ imported.period[1] || '—' }}。</p>
          <div v-if="imported.unmatched.length" class="mt-2" data-testid="att-unmatched">
            <p class="font-medium text-amber-700">{{ imported.unmatched.length }} 个名字对不上平台账号，请选择对应的人（以后会自动识别）：</p>
            <ul class="mt-1 space-y-1">
              <li v-for="u in imported.unmatched" :key="u.name" class="flex items-center gap-2">
                <span class="w-28 truncate">{{ u.name }}（{{ u.rows }} 行）</span>
                <select v-model="mapping[u.name]" :aria-label="`${u.name} 对应的账号`" class="rounded border border-slate-200 px-2 py-1"><option :value="undefined">暂不处理</option>
                  <option v-for="m in members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option></select>
              </li>
            </ul>
            <button class="mt-2 rounded border border-indigo-200 px-3 py-1 text-indigo-700 disabled:text-slate-300" :disabled="busy || !Object.values(mapping).some(Boolean)" @click="upload" data-testid="att-reupload">按所选对应重新导入</button>
          </div>
          <details v-if="imported.skipped_total" class="mt-2"><summary class="cursor-pointer text-amber-700">{{ imported.skipped_total }} 行没有导入</summary>
            <ul class="mt-1 list-disc pl-5"><li v-for="s in imported.skipped" :key="s.row">第 {{ s.row }} 行：{{ s.reason }}</li></ul></details>
        </div>
      </div>
      <div>
        <h3 class="mb-1 text-xs font-medium text-slate-500">2. 分析异常</h3>
        <p class="mb-2 text-xs text-slate-400">按规则、工作日历和业务系统里已批准的请假判断；当天及以后不判断，一次最多 93 天。重复分析是安全的：已补录或已批准请假的异常会自动消除，已认定的不会被改动。</p>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <label for="att-an-start" class="text-slate-500">从</label><input id="att-an-start" v-model="anRange.start" type="date" class="rounded border border-slate-200 px-2 py-1" />
          <label for="att-an-end" class="text-slate-500">到</label><input id="att-an-end" v-model="anRange.end" type="date" class="rounded border border-slate-200 px-2 py-1" />
          <button class="rounded bg-indigo-600 px-3 py-1 text-white disabled:bg-slate-300" :disabled="busy" @click="runAnalyze" data-testid="att-analyze">开始分析</button>
        </div>
        <p v-if="analysis" class="mt-2 text-xs text-slate-700" data-testid="att-analysis-result">{{ analysis.from }} 至 {{ analysis.to }}：{{ analysis.people }} 人 {{ analysis.days }} 天，共 {{ analysis.total_anomalies }} 条异常（新增 {{ analysis.created }}，更新 {{ analysis.updated }}，消除 {{ analysis.cleared }}）。</p>
      </div>
      <div v-if="imports.length">
        <h3 class="mb-1 text-xs font-medium text-slate-500">导入记录</h3>
        <ul class="divide-y divide-slate-100 rounded border border-slate-200 text-xs">
          <li v-for="r in imports" :key="r.id" class="flex flex-wrap justify-between gap-2 px-3 py-2"><span class="truncate">{{ r.file_name }}</span>
            <span class="text-slate-400">{{ r.period[0] || '—' }} 至 {{ r.period[1] || '—' }} · 新增 {{ r.new_punches }}/{{ r.punches }} 条<span v-if="r.unmatched" class="text-amber-700"> · {{ r.unmatched }} 个名字未对应</span></span></li>
        </ul>
      </div>
    </div>

    <!-- 规则与日历（人事） -->
    <div v-else-if="tab === 'rules'" class="space-y-5 px-4 py-4" data-testid="att-rules">
      <div>
        <h3 class="mb-1 text-xs font-medium text-slate-500">上下班时间与迟到宽限</h3>
        <ul class="space-y-2 text-xs">
          <li v-for="r in ruleRows" :key="r.key" class="flex flex-wrap items-center gap-2">
            <span class="w-24 truncate text-slate-700">{{ r.label }}</span>
            <input v-model="r.work_start" :aria-label="`${r.label}上班时间`" class="w-16 rounded border border-slate-200 px-2 py-1" /> —
            <input v-model="r.work_end" :aria-label="`${r.label}下班时间`" class="w-16 rounded border border-slate-200 px-2 py-1" />
            <label class="text-slate-500">宽限</label><input v-model.number="r.grace_minutes" type="number" min="0" max="60" :aria-label="`${r.label}宽限分钟`" class="w-14 rounded border border-slate-200 px-2 py-1" />分钟
            <button class="rounded border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50" :disabled="busy" @click="saveRule(r)" :data-testid="`att-rule-save-${r.key}`">保存</button>
          </li>
        </ul>
        <p class="mt-1 text-xs text-slate-400">部门没有单独设置时用「企业默认」。</p>
      </div>
      <div>
        <h3 class="mb-1 text-xs font-medium text-slate-500">工作日历（法定节假日与调休）</h3>
        <p class="mb-2 text-xs text-slate-400">没录入的日期按周一到周五上班、周末休息。节假日和调休每年不同，请按国务院公布的安排录入。每行：日期,类型,备注；类型写 上班（调休补班）/ 休息 / 节假日。</p>
        <textarea id="att-calendar-text" v-model="calendarText" rows="4" placeholder="2026-10-01,节假日,国庆节&#10;2026-10-10,上班,国庆调休补班" class="w-full rounded border border-slate-200 px-2 py-1 text-xs"></textarea>
        <button class="mt-1 rounded bg-indigo-600 px-3 py-1 text-xs text-white disabled:bg-slate-300" :disabled="busy || !calendarText.trim()" @click="saveCalendar" data-testid="att-calendar-save">保存日历</button>
        <ul v-if="calendar.length" class="mt-2 flex flex-wrap gap-2 text-xs">
          <li v-for="c in calendar" :key="c.day" class="rounded border border-slate-200 px-2 py-0.5">{{ c.day }} {{ kindLabel[c.kind] || c.kind }}<span v-if="c.note" class="text-slate-400">（{{ c.note }}）</span></li>
        </ul>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import * as api from '../api/attendance'
import type { Anomaly, AnalyzeResult, AttendanceMe, ImportRecord, ImportResult, Summary } from '../api/attendance'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ teamId: number }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

type Tab = 'mine' | 'team' | 'import' | 'rules'
const tab = ref<Tab>('mine')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const me = ref<AttendanceMe>({ user_id: 0, is_hr: false, is_head: false, open_mine: 0, to_decide: 0 })

const tabs = computed(() => {
  const list: { value: Tab; label: string; count?: number }[] = [{ value: 'mine', label: '我的考勤', count: me.value.open_mine }]
  if (me.value.is_hr || me.value.is_head) list.push({ value: 'team', label: me.value.is_hr ? '全员异常' : '部门异常', count: me.value.to_decide })
  if (me.value.is_hr) list.push({ value: 'import', label: '导入与分析' }, { value: 'rules', label: '规则与日历' })
  return list
})

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const daysAgo = (n: number) => ymd(new Date(Date.now() - n * 86400000))

// 一行异常：日期、类型、严重度、事实描述
const TONE: Record<string, string> = { high: 'bg-red-100 text-red-700', medium: 'bg-amber-100 text-amber-800', low: 'bg-slate-100 text-slate-600' }
const STATUS_TONE: Record<string, string> = { open: 'bg-amber-100 text-amber-800', explained: 'bg-sky-100 text-sky-800', confirmed: 'bg-red-100 text-red-700', dismissed: 'bg-emerald-100 text-emerald-800', cleared: 'bg-slate-100 text-slate-500' }
function describe(a: Anomaly): string {
  const d = a.detail
  const punches = d.punches?.length ? `打卡 ${d.punches.join('、')}` : '当天没有打卡'
  const shift = d.work_start && d.work_end ? `（上班时间 ${d.work_start}–${d.work_end}）` : ''
  if (a.type === 'late' || a.type === 'early_leave') return `${a.type_label} ${d.minutes} 分钟，${punches}${shift}`
  if (a.type === 'overlong') return `首末打卡相隔 ${d.hours} 小时，${punches}`
  if (a.type === 'leave_conflict') return `已批准请假（${d.leave?.from} 至 ${d.leave?.to}）但当天有打卡，${punches}`
  if (a.type === 'rest_day_work') return `休息日/节假日有打卡，${punches}，请核对是否有加班审批`
  return `${punches}${shift}`
}
const AnomalyLine = defineComponent({
  props: { a: { type: Object as () => Anomaly, required: true }, showName: Boolean },
  setup(p) {
    return () => h('div', { class: 'flex flex-wrap items-center justify-between gap-2' }, [
      h('span', { class: 'min-w-0' }, [
        h('span', { class: 'text-sm text-slate-900' }, `${p.showName ? (p.a.user_name || '') + ' · ' : ''}${p.a.work_date} ${p.a.type_label}`),
        h('span', { class: ['ml-2 rounded-full px-2 py-0.5 text-xs', TONE[p.a.severity]] }, `严重度${p.a.severity_label}`),
        h('span', { class: 'block text-xs text-slate-500' }, `${p.showName && p.a.team_name ? p.a.team_name + ' · ' : ''}${describe(p.a)}`)]),
      h('span', { class: ['shrink-0 rounded-full px-2 py-0.5 text-xs', STATUS_TONE[p.a.status]] }, p.a.status_label)])
  },
})

async function run<T>(job: () => Promise<T>, fail: string): Promise<T | undefined> {
  error.value = ''
  busy.value = true
  try { return await job() } catch (e) { error.value = getErrorMessage(e, fail); return undefined } finally { busy.value = false }
}
const flash = (text: string) => { notice.value = text; setTimeout(() => { if (notice.value === text) notice.value = '' }, 6000) }

// 我的异常
const mine = ref<Anomaly[]>([])
const drafts = reactive<Record<number, string>>({})
const mineFilter = ref<'todo' | 'all'>('todo')
const mineFilters = [{ value: 'todo' as const, label: '待处理' }, { value: 'all' as const, label: '全部' }]
async function loadMine() {
  try { mine.value = await api.listAnomalies(props.teamId, 'mine', mineFilter.value === 'todo' ? 'open,explained' : undefined) } catch (e) { error.value = getErrorMessage(e, '加载考勤异常失败') }
}
function setMineFilter(v: 'todo' | 'all') { mineFilter.value = v; void loadMine() }
async function explain(a: Anomaly) {
  const done = await run(() => api.explainAnomaly(props.teamId, a.id, drafts[a.id] || ''), '提交说明失败')
  if (done) { delete drafts[a.id]; flash('说明已提交，等待人事或部门负责人认定'); await refreshMe(); await loadMine(); emit('changed') }
}

// 团队
const team = ref<Anomaly[]>([])
const summary = ref<Summary | null>(null)
const notes = reactive<Record<number, string>>({})
const range = reactive({ start: daysAgo(30), end: ymd(new Date()) })
const teamFilter = ref('open,explained')
async function loadTeam() {
  error.value = ''
  try {
    team.value = await api.listAnomalies(props.teamId, 'team', teamFilter.value || undefined, range.start, range.end)
    summary.value = await api.getSummary(props.teamId, range.start, range.end)
  } catch (e) { error.value = getErrorMessage(e, '加载团队考勤异常失败') }
}
async function decide(a: Anomaly, action: 'confirm' | 'dismiss') {
  const done = await run(() => api.decideAnomaly(props.teamId, a.id, action, notes[a.id] || ''), '认定失败')
  if (done) { delete notes[a.id]; flash(action === 'confirm' ? '已认定为异常' : '已认定为正常'); await refreshMe(); await loadTeam(); emit('changed') }
}

// 导入与分析
const file = ref<File | null>(null)
const imported = ref<ImportResult | null>(null)
const mapping = reactive<Record<string, number | undefined>>({})
const members = ref<{ user_id: number; name: string }[]>([])
const imports = ref<ImportRecord[]>([])
const analysis = ref<AnalyzeResult | null>(null)
const anRange = reactive({ start: daysAgo(31), end: daysAgo(1) })
function onPick(e: Event) { file.value = (e.target as HTMLInputElement).files?.[0] || null; imported.value = null; Object.keys(mapping).forEach((k) => delete mapping[k]) }
async function upload() {
  if (!file.value) return
  const chosen: Record<string, number> = {}
  for (const [name, id] of Object.entries(mapping)) if (id) chosen[name] = id
  const result = await run(() => api.importFile(props.teamId, file.value as File, chosen), '导入失败')
  if (!result) return
  imported.value = result
  Object.keys(mapping).forEach((k) => delete mapping[k])
  if (result.unmatched.length && !members.value.length) members.value = await api.listMembers(props.teamId).catch(() => [])
  imports.value = await api.listImports(props.teamId).catch(() => imports.value)
  flash(`导入完成：新增 ${result.new_punches} 条打卡`)
}
async function runAnalyze() {
  const result = await run(() => api.analyze(props.teamId, anRange.start, anRange.end), '分析失败')
  if (result) { analysis.value = result; await refreshMe(); emit('changed') }
}

// 规则与日历
interface RuleRow { key: string; label: string; team_id: number | null; work_start: string; work_end: string; grace_minutes: number }
const ruleRows = ref<RuleRow[]>([])
const calendarText = ref('')
const calendar = ref<{ day: string; kind: string; note: string | null }[]>([])
const kindLabel: Record<string, string> = { workday: '上班', rest: '休息', holiday: '节假日' }
async function loadRules() {
  try {
    const rules = await api.getRules(props.teamId)
    const byTeam = new Map(rules.teams.map((r) => [r.team_id, r]))
    const rows: RuleRow[] = [{ key: 'default', label: '企业默认', ...rules.default, team_id: null }]
    for (const d of rules.org_teams) {
      const own = byTeam.get(d.id) || rules.default
      rows.push({ key: String(d.id), label: d.name, team_id: d.id, work_start: own.work_start, work_end: own.work_end, grace_minutes: own.grace_minutes })
    }
    ruleRows.value = rows
    calendar.value = await api.getCalendar(props.teamId, daysAgo(60), ymd(new Date(Date.now() + 400 * 86400000)))
  } catch (e) { error.value = getErrorMessage(e, '加载考勤规则失败') }
}
async function saveRule(r: RuleRow) {
  const done = await run(() => api.setRule(props.teamId, r.team_id, r.work_start, r.work_end, Number(r.grace_minutes)), '保存规则失败')
  if (done) flash(`${r.label}的考勤规则已保存`)
}
async function saveCalendar() {
  const done = await run(() => api.importCalendar(props.teamId, calendarText.value), '保存日历失败')
  if (done) { flash(`已保存 ${done.imported} 天`); calendarText.value = ''; await loadRules() }
}

async function refreshMe() {
  try { me.value = await api.getMe(props.teamId) } catch (e) { error.value = getErrorMessage(e, '加载考勤失败') }
}
async function load() {
  error.value = ''
  tab.value = 'mine'
  await refreshMe()
  await loadMine()
}
watch(tab, (value) => {
  notice.value = ''
  if (value === 'mine') void loadMine()
  else if (value === 'team') void loadTeam()
  else if (value === 'import') void api.listImports(props.teamId).then((r) => { imports.value = r }).catch(() => undefined)
  else if (value === 'rules') void loadRules()
})
watch(() => props.teamId, () => { void load() })
onMounted(load)
defineExpose({ setTab: (t: Tab) => { tab.value = t } })
</script>
