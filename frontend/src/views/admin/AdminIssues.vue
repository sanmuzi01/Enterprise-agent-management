<template>
  <div class="p-6 space-y-4" data-testid="issues-page">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-xs text-slate-500">系统故障按“同一种问题”聚合：发生多少次累计多少次；每个问题有负责人、根因、修复版本，由另一位管理员验收后才算关闭。</p>
      <button @click="load" :disabled="loading" class="rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">刷新</button>
    </div>
    <p v-if="error" class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>

    <div v-if="summary" class="grid grid-cols-2 gap-3 sm:grid-cols-5" data-testid="issues-summary">
      <div class="rounded-lg border border-slate-200 bg-white p-3"><p class="text-xs text-slate-500">未关闭</p><p class="text-xl font-semibold">{{ summary.active }}</p></div>
      <div class="rounded-lg border p-3" :class="summary.regressed ? 'border-red-200 bg-red-50' : 'border-slate-200 bg-white'"><p class="text-xs text-slate-500">复发</p><p class="text-xl font-semibold">{{ summary.regressed }}</p></div>
      <div class="rounded-lg border border-slate-200 bg-white p-3"><p class="text-xs text-slate-500">严重 / 高</p><p class="text-xl font-semibold">{{ (summary.open_by_severity.critical || 0) }} / {{ (summary.open_by_severity.high || 0) }}</p></div>
      <div class="rounded-lg border border-slate-200 bg-white p-3"><p class="text-xs text-slate-500">平均确认时间</p><p class="text-xl font-semibold">{{ minutes(summary.mtta_minutes) }}</p></div>
      <div class="rounded-lg border border-slate-200 bg-white p-3"><p class="text-xs text-slate-500">平均恢复时间</p><p class="text-xl font-semibold">{{ minutes(summary.mttr_minutes) }}</p></div>
    </div>

    <div class="flex flex-wrap items-center gap-2 text-xs">
      <select v-model="status" class="h-8 rounded border border-slate-200 bg-white px-2" data-testid="issues-status" @change="load">
        <option value="active">未关闭</option><option value="">全部</option>
        <option v-for="(label, key) in STATUS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="severity" class="h-8 rounded border border-slate-200 bg-white px-2" @change="load">
        <option value="">全部严重程度</option><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
      </select>
      <input v-model="keyword" type="search" placeholder="问题编号、标题、错误码" class="h-8 w-56 rounded border border-slate-200 bg-white px-2" @keyup.enter="load" />
    </div>

    <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table class="w-full text-left text-sm" data-testid="issues-table">
        <thead class="bg-slate-50 text-xs text-slate-500"><tr>
          <th class="px-3 py-2">编号</th><th class="px-3 py-2">问题</th><th class="px-3 py-2">严重度</th><th class="px-3 py-2">状态</th>
          <th class="px-3 py-2">次数</th><th class="px-3 py-2">最近发生</th></tr></thead>
        <tbody>
          <tr v-for="i in issues" :key="i.id" class="cursor-pointer border-t border-slate-100 hover:bg-slate-50" :data-testid="`issue-row-${i.id}`" @click="open(i.id)">
            <td class="whitespace-nowrap px-3 py-2 text-xs text-slate-500">{{ i.issue_no }}</td>
            <td class="px-3 py-2"><span class="block max-w-md truncate">{{ i.title }}</span><span class="text-xs text-slate-400">{{ i.error_code }} · {{ i.category_label }}</span></td>
            <td class="px-3 py-2"><span class="rounded px-1.5 py-0.5 text-xs" :class="sevClass[i.severity]">{{ i.severity_label }}</span></td>
            <td class="px-3 py-2"><span class="rounded px-1.5 py-0.5 text-xs" :class="i.status === 'REGRESSED' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-600'">{{ i.status_label }}</span></td>
            <td class="px-3 py-2 tabular-nums">{{ i.occurrence_count }}</td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-slate-500">{{ time(i.last_seen_at) }}</td>
          </tr>
          <tr v-if="!issues.length"><td colspan="6" class="px-3 py-8 text-center text-slate-400">没有符合条件的问题</td></tr>
        </tbody>
      </table>
    </div>

    <section v-if="detail" class="space-y-3 rounded-lg border border-indigo-200 bg-white p-4 text-sm" data-testid="issue-detail">
      <div class="flex items-start justify-between gap-3">
        <div><p class="text-xs text-slate-400">{{ detail.issue_no }} · {{ detail.service }} · {{ detail.operation }}</p><h3 class="font-semibold text-slate-900">{{ detail.title }}</h3></div>
        <button class="text-xs text-slate-400 hover:text-slate-700" @click="detail = null">关闭</button>
      </div>
      <dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
        <div><dt class="text-slate-400">状态</dt><dd>{{ detail.status_label }}</dd></div>
        <div><dt class="text-slate-400">负责人</dt><dd>{{ detail.responsible_name || '未指派' }}</dd></div>
        <div><dt class="text-slate-400">首次 / 最近</dt><dd>{{ time(detail.first_seen_at) }} / {{ time(detail.last_seen_at) }}</dd></div>
        <div><dt class="text-slate-400">累计次数 / 复发</dt><dd>{{ detail.occurrence_count }} / {{ detail.regress_count }}</dd></div>
        <div class="col-span-2"><dt class="text-slate-400">最近 trace_id</dt><dd class="break-all font-mono">{{ detail.last_trace_id || '—' }}</dd></div>
        <div class="col-span-2"><dt class="text-slate-400">外部跳转</dt>
          <dd class="flex gap-3"><a v-if="detail.links?.trace" :href="detail.links.trace" target="_blank" rel="noopener noreferrer" class="text-indigo-600">链路追踪</a>
            <a v-if="detail.links?.sentry" :href="detail.links.sentry" target="_blank" rel="noopener noreferrer" class="text-indigo-600">Sentry</a>
            <a v-if="detail.links?.logs" :href="detail.links.logs" target="_blank" rel="noopener noreferrer" class="text-indigo-600">日志</a>
            <span v-if="!detail.links?.trace && !detail.links?.sentry && !detail.links?.logs" class="text-slate-400">未配置（GRAFANA_URL / SENTRY_ISSUE_URL / LOG_SEARCH_URL）</span></dd></div>
      </dl>
      <div v-if="detail.root_cause" class="rounded bg-emerald-50 p-2 text-xs text-emerald-900">
        <p><strong>根因：</strong>{{ detail.root_cause }}</p><p><strong>处理：</strong>{{ detail.resolution }}</p>
        <p><strong>修复版本：</strong>{{ detail.fix_version }} · 处理人 {{ detail.resolved_by_name }} · {{ detail.verified_by_name ? `已由 ${detail.verified_by_name} 验收` : '待另一位管理员验收' }}</p>
      </div>

      <div class="space-y-2 border-t border-slate-100 pt-3" data-testid="issue-actions">
        <div class="flex flex-wrap gap-2">
          <button v-for="a in availableActions" :key="a.value" class="rounded border px-2.5 py-1 text-xs" :class="form === a.value ? 'border-indigo-600 bg-indigo-600 text-white' : 'border-slate-200 hover:bg-slate-50'"
            :data-testid="`issue-action-${a.value}`" @click="form = form === a.value ? '' : a.value">{{ a.label }}</button>
        </div>
        <form v-if="form" class="space-y-2 rounded bg-slate-50 p-3 text-xs" @submit.prevent="submit">
          <template v-if="form === 'resolve'">
            <textarea v-model="values.root_cause" rows="2" maxlength="4000" placeholder="根因（必填）" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="issue-root-cause" />
            <textarea v-model="values.resolution" rows="2" maxlength="4000" placeholder="处理说明（必填）" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="issue-resolution" />
            <input v-model="values.fix_version" maxlength="80" placeholder="修复版本，如 v1.2.3 或 commit（必填）" class="w-full rounded border border-slate-200 px-2 py-1.5" data-testid="issue-fix-version" />
          </template>
          <textarea v-model="values.note" rows="2" maxlength="1000" :placeholder="form === 'note' ? '备注内容（必填）' : '备注（可选）'" class="w-full rounded border border-slate-200 px-2 py-1.5" />
          <button type="submit" :disabled="busy" class="rounded bg-indigo-600 px-3 py-1.5 text-white disabled:opacity-50" data-testid="issue-submit">提交</button>
        </form>
      </div>

      <div><h4 class="mb-1 text-xs font-medium text-slate-500">处理记录</h4>
        <ol class="space-y-1 border-l border-slate-200 pl-3 text-xs"><li v-for="e in detail.events" :key="e.id"><span class="font-medium">{{ ACTION[e.action] || e.action }}</span>
          <span class="text-slate-400"> · {{ e.actor_name }} · {{ time(e.created_at) }}</span><p v-if="e.note" class="text-slate-600">{{ e.note }}</p></li>
          <li v-if="!detail.events.length" class="text-slate-400">还没有处理记录</li></ol></div>
      <div><h4 class="mb-1 text-xs font-medium text-slate-500">最近发生（已脱敏）</h4>
        <ul class="space-y-1 text-xs text-slate-600"><li v-for="o in detail.occurrences.slice(0, 10)" :key="o.id" class="rounded bg-slate-50 px-2 py-1">
          {{ time(o.occurred_at) }} · {{ o.http_status }} · <span class="font-mono">{{ o.trace_id }}</span><br />{{ o.message }}</li></ul></div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import * as api from '../../api/issues'
import type { Issue, IssueDetail, IssueSummary } from '../../api/issues'
import { getErrorMessage } from '../../utils/request'

const STATUS: Record<string, string> = { OPEN: '新发现', ACKNOWLEDGED: '已确认', INVESTIGATING: '排查中', MITIGATED: '已缓解', RESOLVED: '已解决', REGRESSED: '问题复发' }
const ACTION: Record<string, string> = { acknowledge: '确认', investigate: '开始排查', mitigate: '已缓解', resolve: '解决', verify: '验收', assign: '指派', note: '备注', severity: '调整严重度', regressed: '问题复发', dependency_recovered: '依赖恢复' }
const sevClass: Record<string, string> = { critical: 'bg-red-100 text-red-700', high: 'bg-orange-100 text-orange-700', medium: 'bg-amber-100 text-amber-800', low: 'bg-slate-100 text-slate-600' }

const issues = ref<Issue[]>([])
const summary = ref<IssueSummary | null>(null)
const detail = ref<IssueDetail | null>(null)
const status = ref('active'), severity = ref(''), keyword = ref('')
const loading = ref(false), busy = ref(false), error = ref('')
const form = ref('')
const values = reactive({ note: '', root_cause: '', resolution: '', fix_version: '' })

const minutes = (v: number | null) => (v === null ? '—' : v >= 60 ? `${(v / 60).toFixed(1)} 小时` : `${v} 分钟`)
const time = (iso: string) => new Date(iso).toLocaleString()

const availableActions = computed(() => {
  const s = detail.value?.status
  const list: { value: string; label: string }[] = []
  if (s === 'OPEN' || s === 'REGRESSED') list.push({ value: 'acknowledge', label: '确认并接手' })
  if (s && ['ACKNOWLEDGED', 'MITIGATED', 'REGRESSED'].includes(s)) list.push({ value: 'investigate', label: '开始排查' })
  if (s && ['OPEN', 'ACKNOWLEDGED', 'INVESTIGATING', 'REGRESSED'].includes(s)) list.push({ value: 'mitigate', label: '已缓解' })
  if (s && ['ACKNOWLEDGED', 'INVESTIGATING', 'MITIGATED', 'REGRESSED'].includes(s)) list.push({ value: 'resolve', label: '解决并填写根因' })
  if (s === 'RESOLVED' && !detail.value?.verified_by) list.push({ value: 'verify', label: '验收通过' })
  list.push({ value: 'note', label: '添加备注' })
  return list
})

async function load() {
  loading.value = true; error.value = ''
  try {
    ;[issues.value, summary.value] = await Promise.all([api.listIssues({ status: status.value || undefined, severity: severity.value || undefined, keyword: keyword.value || undefined }), api.getIssueSummary()])
  } catch (e) { error.value = getErrorMessage(e, '加载问题列表失败') } finally { loading.value = false }
}

async function open(id: number) {
  form.value = ''
  try { detail.value = await api.getIssue(id) } catch (e) { error.value = getErrorMessage(e, '加载问题详情失败') }
}

async function submit() {
  if (!detail.value || !form.value) return
  busy.value = true; error.value = ''
  try {
    detail.value = await api.actOnIssue(detail.value.id, { action: form.value, note: values.note, root_cause: values.root_cause, resolution: values.resolution, fix_version: values.fix_version })
    form.value = ''; values.note = values.root_cause = values.resolution = values.fix_version = ''
    await load()
  } catch (e) { error.value = getErrorMessage(e, '操作失败') } finally { busy.value = false }
}

onMounted(load)
</script>
