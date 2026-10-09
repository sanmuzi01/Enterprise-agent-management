<template>
  <div class="h-screen flex flex-col bg-transparent">
    <header class="min-h-16 border-b border-sky-200/70 ui-glass px-5 py-3 text-slate-900 backdrop-blur-xl flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex items-center gap-3">
        <button
          @click="router.push('/agents')"
          class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-500 hover:bg-black/[.06] hover:text-slate-900"
          title="返回工作台"
        >
          <ArrowLeft :size="16" />
        </button>
        <div>
          <h1 class="text-base font-semibold text-slate-900">Agent 流水线</h1>
          <p class="text-xs text-slate-500">把多个助手串成固定顺序：上一步的回答自动作为下一步的输入。</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button @click="load" :disabled="loading" class="inline-flex items-center gap-2 rounded border border-sky-200 bg-white/80 px-3 py-1.5 text-sm text-slate-700 hover:bg-sky-50 disabled:text-slate-300">
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
        <button @click="openCreate" class="inline-flex items-center gap-2 rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-700">
          <Plus :size="14" />
          新建流水线
        </button>
      </div>
    </header>

    <div class="flex-1 overflow-y-auto p-5">
      <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

      <div v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</div>
      <div v-else-if="!pipelines.length" class="rounded-lg border border-dashed border-sky-200 bg-white/60 py-16 text-center text-sm text-slate-400">
        还没有流水线。点右上角"新建流水线"，挑 2 个以上的助手按顺序串起来。
      </div>
      <div v-else class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <article v-for="p in pipelines" :key="p.id" class="rounded-lg border border-sky-200 bg-white/80 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-slate-900">{{ p.name }}</p>
              <p v-if="p.description" class="mt-0.5 truncate text-xs text-slate-500">{{ p.description }}</p>
            </div>
            <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="p.is_enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'">
              {{ p.is_enabled ? '启用中' : '已停用' }}
            </span>
          </div>
          <div class="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
            <template v-for="(s, i) in p.steps" :key="i">
              <span class="rounded border border-sky-100 bg-sky-50 px-2 py-1">{{ s.label || agentName(s.agent_id) }}</span>
              <ChevronRight v-if="i < p.steps.length - 1" :size="12" class="text-slate-300" />
            </template>
          </div>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <button @click="openRun(p)" :disabled="!p.is_enabled" class="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-40">跑一次</button>
            <button @click="openEdit(p)" class="rounded border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50">编辑</button>
            <button @click="toggleEnabled(p)" class="rounded border px-2.5 py-1.5 text-xs transition-colors"
              :class="p.is_enabled ? 'border-red-200 text-red-700 hover:bg-red-50' : 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'">
              {{ p.is_enabled ? '停用' : '启用' }}
            </button>
            <button @click="remove(p)" class="rounded border border-red-200 px-2.5 py-1.5 text-xs text-red-700 hover:bg-red-50">删除</button>
          </div>
        </article>
      </div>
    </div>

    <!-- 新建/编辑弹窗 -->
    <div v-if="dialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="closeDialog">
      <div class="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ dialog.editing ? '编辑流水线' : '新建流水线' }}</h3>
        <div class="space-y-3">
          <div>
            <label class="mb-1 block text-xs text-slate-500">名称</label>
            <input v-model="dialog.form.name" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-sky-500" placeholder="如：先总结再润色" />
          </div>
          <div>
            <label class="mb-1 block text-xs text-slate-500">说明（可选）</label>
            <input v-model="dialog.form.description" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-sky-500" />
          </div>
          <div>
            <label class="mb-1 flex items-center justify-between text-xs text-slate-500">
              <span>步骤（按顺序执行，2~10 个）</span>
              <button @click="addStep" :disabled="dialog.form.steps.length >= 10" class="text-sky-600 hover:underline disabled:text-slate-300">+ 添加步骤</button>
            </label>
            <div class="space-y-2">
              <div v-for="(step, i) in dialog.form.steps" :key="i" class="flex items-center gap-2">
                <span class="w-5 shrink-0 text-center text-xs text-slate-400">{{ i + 1 }}</span>
                <select v-model.number="step.agent_id" class="h-9 flex-1 rounded border border-slate-300 px-2 text-sm outline-none focus:border-sky-500">
                  <option :value="0" disabled>选择助手</option>
                  <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.name }}</option>
                </select>
                <input v-model="step.label" placeholder="步骤名（可选）" class="h-9 w-28 rounded border border-slate-300 px-2 text-xs outline-none focus:border-sky-500" />
                <button @click="removeStep(i)" :disabled="dialog.form.steps.length <= 2" class="shrink-0 rounded border border-red-200 px-2 py-1.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-30">删</button>
              </div>
            </div>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="closeDialog" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button @click="submitDialog" :disabled="submitting || !canSubmit" class="rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-700 disabled:opacity-50">保存</button>
        </div>
      </div>
    </div>

    <!-- 运行弹窗 -->
    <div v-if="runDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="closeRun">
      <div class="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-1 text-base font-semibold text-slate-800">跑一次：{{ runDialog.pipeline?.name }}</h3>
        <p class="mb-3 text-xs text-slate-500">第一步的问题从这里发，之后每一步自动用上一步的回答作为输入。</p>
        <div class="flex gap-2">
          <input v-model="runDialog.message" :disabled="runDialog.running" class="h-9 flex-1 rounded border border-slate-300 px-3 text-sm outline-none focus:border-sky-500" placeholder="输入给第一步助手的问题" @keydown.enter="!isImeEnter($event) && submitRun()" />
          <button @click="submitRun" :disabled="runDialog.running || !runDialog.message.trim()" class="shrink-0 rounded bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50">
            {{ runDialog.running ? '跑一次…' : '开始' }}
          </button>
        </div>

        <div v-if="runDialog.result" class="mt-4 space-y-3">
          <p class="text-xs" :class="runDialog.result.completed ? 'text-emerald-600' : 'text-amber-600'">
            {{ runDialog.result.completed ? '全部步骤跑完了' : '在某一步停下来了（往下看具体原因）' }}
          </p>
          <div v-for="s in runDialog.result.steps" :key="s.step" class="rounded border p-3" :class="s.ok ? 'border-slate-200' : 'border-red-200 bg-red-50'">
            <p class="text-xs font-medium text-slate-700">第 {{ s.step }} 步 · {{ s.label || s.agent_name }}</p>
            <p v-if="s.ok" class="mt-1.5 whitespace-pre-wrap text-sm text-slate-700">{{ s.answer }}</p>
            <p v-else class="mt-1.5 text-sm text-red-700">失败：{{ s.error }}</p>
          </div>
        </div>
        <div class="mt-4 flex justify-end">
          <button @click="closeRun" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ChevronRight, Plus, RefreshCcw } from 'lucide-vue-next'
import * as pipelineApi from '../api/agentPipeline'
import type { AgentPipeline, PipelineRunResult } from '../api/agentPipeline'
import * as agentApi from '../api/agent'
import type { AgentInfo } from '../api/agent'
import { getErrorMessage } from '../utils/request'

const router = useRouter()

const pipelines = ref<AgentPipeline[]>([])
const agents = ref<AgentInfo[]>([])
const loading = ref(true)
const errorMsg = ref('')
const submitting = ref(false)

const agentName = (id: number) => agents.value.find((a) => a.id === id)?.name || `#${id}`

const dialog = ref<{
  visible: boolean
  editing: AgentPipeline | null
  form: { name: string; description: string; steps: { agent_id: number; label: string }[] }
}>({
  visible: false,
  editing: null,
  form: { name: '', description: '', steps: [{ agent_id: 0, label: '' }, { agent_id: 0, label: '' }] },
})

const canSubmit = computed(() =>
  dialog.value.form.name.trim().length > 0 &&
  dialog.value.form.steps.length >= 2 &&
  dialog.value.form.steps.every((s) => s.agent_id > 0),
)

const runDialog = ref<{
  visible: boolean
  pipeline: AgentPipeline | null
  message: string
  running: boolean
  result: PipelineRunResult | null
}>({ visible: false, pipeline: null, message: '', running: false, result: null })

const load = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const [p, a] = await Promise.all([pipelineApi.listPipelines(), agentApi.listAgents()])
    pipelines.value = p
    agents.value = a
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  dialog.value = {
    visible: true, editing: null,
    form: { name: '', description: '', steps: [{ agent_id: 0, label: '' }, { agent_id: 0, label: '' }] },
  }
}

const openEdit = (p: AgentPipeline) => {
  dialog.value = {
    visible: true, editing: p,
    form: {
      name: p.name, description: p.description || '',
      steps: p.steps.map((s) => ({ agent_id: s.agent_id, label: s.label || '' })),
    },
  }
}

const closeDialog = () => { dialog.value.visible = false }

const addStep = () => {
  if (dialog.value.form.steps.length < 10) dialog.value.form.steps.push({ agent_id: 0, label: '' })
}
const removeStep = (i: number) => {
  if (dialog.value.form.steps.length > 2) dialog.value.form.steps.splice(i, 1)
}

const submitDialog = async () => {
  submitting.value = true
  try {
    const payload = {
      name: dialog.value.form.name.trim(),
      description: dialog.value.form.description.trim() || undefined,
      steps: dialog.value.form.steps.map((s) => ({ agent_id: s.agent_id, label: s.label.trim() || undefined })),
    }
    if (dialog.value.editing) {
      await pipelineApi.updatePipeline(dialog.value.editing.id, payload)
    } else {
      await pipelineApi.createPipeline(payload)
    }
    closeDialog()
    await load()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '保存失败')
  } finally {
    submitting.value = false
  }
}

const toggleEnabled = async (p: AgentPipeline) => {
  try {
    await pipelineApi.updatePipeline(p.id, { is_enabled: !p.is_enabled })
    await load()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '操作失败')
  }
}

const remove = async (p: AgentPipeline) => {
  if (!confirm(`确认删除流水线「${p.name}」？`)) return
  try {
    await pipelineApi.deletePipeline(p.id)
    await load()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '删除失败')
  }
}

const openRun = (p: AgentPipeline) => {
  runDialog.value = { visible: true, pipeline: p, message: '', running: false, result: null }
}
const closeRun = () => { runDialog.value.visible = false }

const submitRun = async () => {
  if (!runDialog.value.pipeline || !runDialog.value.message.trim()) return
  runDialog.value.running = true
  runDialog.value.result = null
  try {
    runDialog.value.result = await pipelineApi.runPipeline(runDialog.value.pipeline.id, runDialog.value.message.trim())
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '运行失败')
    closeRun()
  } finally {
    runDialog.value.running = false
  }
}

onMounted(load)
</script>
