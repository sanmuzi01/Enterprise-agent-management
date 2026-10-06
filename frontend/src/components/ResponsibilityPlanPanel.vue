<template>
  <section class="rounded-lg border border-indigo-200 bg-white" data-testid="resp-plan-detail">
    <div class="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
      <div class="min-w-0 flex-1">
        <p class="text-xs text-slate-400">{{ plan?.sourceLabel }} · 由 {{ plan?.createdByName }} 整理</p>
        <input v-if="plan && plan.canEdit" v-model="title" maxlength="160" class="mt-0.5 w-full rounded border border-slate-200 px-2 py-1 text-sm font-semibold" data-testid="resp-plan-title" />
        <h3 v-else class="mt-0.5 text-sm font-semibold text-slate-900">{{ plan?.title }}</h3>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <span v-if="plan" class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600" data-testid="resp-plan-status">{{ plan.statusLabel }}</span>
        <button class="text-xs text-slate-400 hover:text-slate-700" @click="$emit('close')">关闭</button>
      </div>
    </div>

    <p v-if="error" class="mx-4 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{{ error }}</p>
    <div v-if="loading && !plan" class="px-4 py-8 text-center text-sm text-slate-400">加载中…</div>

    <div v-if="plan" class="space-y-4 px-4 py-4 text-sm">
      <div v-if="plan.status === 'DRAFT'" class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="resp-draft-banner">
        这是 AI 整理出的<strong>草稿</strong>，还没有通知任何员工。补全缺口、核对每一项后，由部门负责人正式发布；员工要自己接受，验收人要自己验收。
        <span v-if="plan.missingAtCreation"> 整理时发现 {{ plan.missingAtCreation }} 处必填信息缺失。</span>
      </div>

      <div v-if="plan.summary || plan.decisions.length" class="space-y-1 text-xs text-slate-600">
        <p v-if="plan.summary"><span class="text-slate-400">主要决定：</span>{{ plan.summary }}</p>
      </div>
      <div v-if="plan.unresolved.length" class="rounded bg-slate-50 px-3 py-2 text-xs text-slate-600" data-testid="resp-unresolved">
        <p class="mb-1 font-medium text-slate-500">原文没有说清 / 仅讨论未决定</p>
        <ul class="list-inside list-disc"><li v-for="(u, i) in plan.unresolved" :key="i">{{ u }}</li></ul>
      </div>
      <details v-if="plan.sourceText" class="text-xs text-slate-500">
        <summary class="cursor-pointer">查看原文</summary>
        <pre class="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans">{{ plan.sourceText }}</pre>
      </details>

      <!-- 草稿：逐项补全 -->
      <div v-if="plan.canEdit" class="space-y-3">
        <div v-for="task in plan.tasks.filter((x) => x.status !== 'CANCELLED')" :key="task.id" class="rounded-lg border border-slate-200 p-3" :data-testid="`resp-draft-task-${task.seq}`">
          <div class="mb-2 flex items-center justify-between gap-2">
            <span class="text-xs font-medium text-slate-500">第 {{ task.seq }} 项</span>
            <div class="flex items-center gap-1.5">
              <span v-for="i in task.issues.filter((x) => x.level === 'BLOCK')" :key="i.code" class="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">{{ i.message }}</span>
              <span v-for="i in task.issues.filter((x) => x.level === 'WARN')" :key="i.code" class="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800">{{ i.message }}</span>
            </div>
          </div>
          <div v-if="edits[task.id]" class="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
            <label class="space-y-1 sm:col-span-3"><span class="text-slate-500">责任事项（可执行的动作）</span>
              <input v-model="edits[task.id].title" maxlength="160" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-title-${task.seq}`" /></label>
            <label class="space-y-1"><span class="text-slate-500">主责员工</span>
              <select v-model="edits[task.id].responsible_user_id" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-responsible-${task.seq}`">
                <option :value="null">待补充</option><option v-for="m in candidates.members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option></select></label>
            <label class="space-y-1"><span class="text-slate-500">验收人</span>
              <select v-model="edits[task.id].reviewer_user_id" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-reviewer-${task.seq}`">
                <option :value="null">待补充</option><option v-for="m in candidates.reviewers" :key="m.user_id" :value="m.user_id">{{ m.name }}</option></select></label>
            <label class="space-y-1"><span class="text-slate-500">截止日期</span>
              <input v-model="edits[task.id].due_date" type="date" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-due-${task.seq}`" /></label>
            <label class="space-y-1 sm:col-span-2"><span class="text-slate-500">交付物</span>
              <input v-model="edits[task.id].deliverable" maxlength="300" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-deliverable-${task.seq}`" /></label>
            <label class="space-y-1"><span class="text-slate-500">优先级</span>
              <select v-model="edits[task.id].priority" class="w-full rounded border border-slate-200 px-2 py-1.5">
                <option v-for="(l, k) in PRIORITY" :key="k" :value="k">{{ l }}</option></select></label>
            <label class="space-y-1 sm:col-span-3"><span class="text-slate-500">验收标准</span>
              <input v-model="edits[task.id].acceptance_criteria" maxlength="500" class="w-full rounded border border-slate-200 px-2 py-1.5" :data-testid="`resp-criteria-${task.seq}`" /></label>
            <label class="space-y-1 sm:col-span-2"><span class="text-slate-500">协办人（可多选）</span>
              <select v-model="edits[task.id].collaborator_user_ids" multiple size="3" class="w-full rounded border border-slate-200 px-2 py-1">
                <option v-for="m in candidates.members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option></select></label>
            <label class="space-y-1"><span class="text-slate-500">前置事项（可多选）</span>
              <select v-model="edits[task.id].depends_on_seq" multiple size="3" class="w-full rounded border border-slate-200 px-2 py-1">
                <option v-for="o in plan.tasks.filter((x) => x.id !== task.id && x.status !== 'CANCELLED')" :key="o.id" :value="o.seq">第 {{ o.seq }} 项 {{ o.title.slice(0, 12) }}</option></select></label>
          </div>
          <blockquote v-if="task.sourceEvidence" class="mt-2 border-l-2 border-slate-300 pl-2 text-xs text-slate-500">原文依据：{{ task.sourceEvidence }}</blockquote>
          <div class="mt-2 flex items-center gap-2 text-xs">
            <button class="rounded bg-slate-800 px-2.5 py-1 text-white hover:bg-slate-900 disabled:opacity-50" :disabled="busy" @click="saveTask(task.id)" :data-testid="`resp-save-${task.seq}`">保存此项</button>
            <button class="text-red-600 hover:underline" :disabled="busy" @click="remove(task.id)">删除</button>
          </div>
        </div>
        <button class="rounded border border-dashed border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50" :disabled="busy" @click="add" data-testid="resp-add-task">新增一项责任事项</button>

        <div class="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs" data-testid="resp-publish-box">
          <p v-if="plan.blockerCount" class="text-red-700">还有 {{ plan.blockerCount }} 处必填信息缺失，补全并保存后才能发布。</p>
          <p v-else class="text-emerald-700">所有责任事项信息齐全，可以发布。</p>
          <template v-if="plan.isHead">
            <label class="flex items-start gap-2"><input v-model="confirmed" type="checkbox" class="mt-0.5" data-testid="resp-publish-confirm" />
              我已核对每项责任的主责员工、期限、交付物和验收标准，确认正式指派；员工会收到待接受的通知。</label>
            <div class="flex items-center gap-2">
              <button class="rounded bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-700 disabled:opacity-50" :disabled="busy || !plan.canPublish || !confirmed" @click="publish" data-testid="resp-publish">正式发布并指派</button>
              <button class="text-red-600 hover:underline" :disabled="busy" @click="cancelPlan">取消这份计划</button>
            </div>
          </template>
          <p v-else class="text-slate-500">正式指派只能由部门负责人或企业管理员操作；你可以先补全内容，再请负责人发布。</p>
        </div>
      </div>

      <!-- 已发布 / 已结束：只读列表，点进去看每项责任 -->
      <ul v-else class="divide-y divide-slate-100 rounded border border-slate-200" data-testid="resp-plan-tasks">
        <li v-for="task in plan.tasks" :key="task.id">
          <button class="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-slate-50" @click="$emit('open-task', task.id)" :data-testid="`resp-plan-task-${task.id}`">
            <span class="min-w-0"><span class="block truncate text-sm text-slate-900">第 {{ task.seq }} 项 · {{ task.title }}</span>
              <span class="text-xs text-slate-400">{{ task.responsibleName || '待补充' }} · 验收 {{ task.reviewerName || '待补充' }} · 截止 {{ task.dueDate || '—' }}</span></span>
            <span class="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{{ task.statusLabel }}</span>
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import * as api from '../api/responsibility'
import type { Candidates, RespPlan, RespTask, TaskInput } from '../api/responsibility'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ teamId: number; planId: number; candidates: Candidates }>()
const emit = defineEmits<{ close: []; changed: []; 'open-task': [id: number] }>()
const PRIORITY: Record<string, string> = { LOW: '低', NORMAL: '普通', HIGH: '高', URGENT: '紧急' }

const plan = ref<RespPlan | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const confirmed = ref(false)
const title = ref('')
const edits = reactive<Record<number, TaskInput>>({})

const toInput = (t: RespTask): TaskInput => ({
  title: t.title, responsible_user_id: t.responsibleUserId, collaborator_user_ids: [...t.collaboratorUserIds], reviewer_user_id: t.reviewerUserId,
  due_date: t.dueDate, deliverable: t.deliverable, acceptance_criteria: t.acceptanceCriteria, priority: t.priority,
  evidence: t.sourceEvidence, depends_on_seq: [...t.dependsOnSeq],
})

function accept(next: RespPlan) {
  plan.value = next
  title.value = next.title
  confirmed.value = false
  for (const key of Object.keys(edits)) delete edits[Number(key)]
  if (next.canEdit) for (const t of next.tasks) edits[t.id] = toInput(t)
}

async function run<T>(job: () => Promise<T>, fail: string): Promise<T | undefined> {
  busy.value = true
  error.value = ''
  try {
    return await job()
  } catch (e) {
    error.value = getErrorMessage(e, fail)
    return undefined
  } finally {
    busy.value = false
  }
}

async function load() {
  loading.value = true
  try {
    accept(await api.getPlan(props.teamId, props.planId))
  } catch (e) {
    error.value = getErrorMessage(e, '加载责任计划失败')
  } finally {
    loading.value = false
  }
}

async function saveTask(id: number) {
  if (plan.value && title.value.trim() && title.value !== plan.value.title) {
    const edited = await run(() => api.editPlan(props.teamId, props.planId, { title: title.value, summary: plan.value?.summary || '', unresolved: plan.value?.unresolved || [] }), '保存标题失败')
    if (!edited) return
  }
  const next = await run(() => api.editTask(props.teamId, id, edits[id]), '保存失败')
  if (next) { accept(next); emit('changed') }
}

async function remove(id: number) {
  if (!window.confirm('确定删除这项责任事项？')) return
  const next = await run(() => api.removeTask(props.teamId, id), '删除失败')
  if (next) { accept(next); emit('changed') }
}

async function add() {
  const blank: TaskInput = { title: '新的责任事项', responsible_user_id: null, collaborator_user_ids: [], reviewer_user_id: null, due_date: null,
    deliverable: null, acceptance_criteria: null, priority: 'NORMAL', evidence: null, depends_on_seq: [] }
  const next = await run(() => api.addTask(props.teamId, props.planId, blank), '新增失败')
  if (next) { accept(next); emit('changed') }
}

async function publish() {
  const next = await run(() => api.publishPlan(props.teamId, props.planId), '发布失败')
  if (next) { accept(next); emit('changed') }
}

async function cancelPlan() {
  const reason = window.prompt('取消这份计划的原因（可留空）') ?? null
  if (reason === null) return
  const next = await run(() => api.cancelPlan(props.teamId, props.planId, reason), '取消失败')
  if (next) { accept(next); emit('changed') }
}

watch(() => props.planId, () => { plan.value = null; void load() }, { immediate: true })
</script>
