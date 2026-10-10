<template>
  <details class="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm" :open="open" @toggle="open = ($event.target as HTMLDetailsElement).open" data-testid="batch-panel">
    <summary class="cursor-pointer font-medium text-slate-700">批量整理多份文件（后台依次处理，可离开页面）</summary>
    <div class="mt-3 space-y-3">
      <p class="text-xs text-slate-500">一次最多 10 份、合计 60,000 字。每份是一份独立的整理成果：单独核对、单独保存，一份失败不影响其他份。{{ hint }}</p>
      <p v-if="error" role="alert" class="rounded bg-red-50 p-2 text-xs text-red-700">{{ error }}</p>

      <div v-if="!batch" class="space-y-2">
        <label class="inline-block cursor-pointer rounded border border-blue-300 px-3 py-1.5 text-blue-600 hover:bg-blue-50">
          {{ extracting ? `正在提取文字（${extracted}/${pending}）…` : '选择多份文件' }}
          <input class="sr-only" type="file" multiple data-testid="batch-files" :disabled="busy"
            accept=".txt,.md,.csv,.pdf,.docx,.xlsx,.eml,.png,.jpg,.jpeg" @change="pick" />
        </label>
        <ul v-if="staged.length" class="space-y-1" data-testid="batch-staged">
          <li v-for="(s, i) in staged" :key="i" class="flex items-center justify-between gap-2 rounded bg-white px-2 py-1.5">
            <span class="truncate">{{ s.name }} <span class="text-xs text-slate-400">{{ s.text.length.toLocaleString() }} 字</span></span>
            <button type="button" class="text-xs text-red-600" :disabled="busy" @click="staged.splice(i, 1)">移除</button>
          </li>
        </ul>
        <button class="rounded-lg bg-blue-600 px-4 py-2 text-white disabled:opacity-40" :disabled="busy || !staged.length || disabled" data-testid="batch-start" @click="start">
          开始批量整理{{ staged.length ? `（${staged.length} 份）` : '' }}</button>
      </div>

      <div v-else class="space-y-2" data-testid="batch-progress">
        <div class="flex items-center justify-between gap-2">
          <span data-testid="batch-summary">{{ batch.finished ? '整理完成' : '后台整理中' }}：{{ batch.done }}/{{ batch.total }} 份已处理<span v-if="batch.counts.failed">，{{ batch.counts.failed }} 份失败</span></span>
          <button v-if="batch.finished" class="text-xs text-blue-600" @click="reset" data-testid="batch-new">开始新的批次</button>
        </div>
        <div class="h-2 overflow-hidden rounded bg-slate-200"><div class="h-full bg-blue-500 transition-all" :style="{ width: `${batch.total ? (batch.done / batch.total) * 100 : 0}%` }" /></div>
        <ul class="space-y-1">
          <li v-for="item in batch.items" :key="item.id" class="flex flex-wrap items-center justify-between gap-2 rounded bg-white px-2 py-1.5" :data-testid="`batch-item-${item.batch_index}`">
            <span class="truncate">{{ item.batch_name }}</span>
            <span class="flex items-center gap-2 text-xs">
              <span :class="tone[item.status] || 'text-slate-500'">{{ label[item.status] || item.status }}</span>
              <span v-if="item.status === 'failed'" class="max-w-xs truncate text-slate-400" :title="item.error_message || ''">{{ item.error_message }}</span>
              <button v-if="['ready', 'retry', 'applied'].includes(item.status)" class="text-blue-600" @click="$emit('open', item.id)">{{ item.status === 'applied' ? '查看' : '核对并保存' }}</button>
              <button v-if="item.status === 'failed'" class="text-blue-600" :disabled="busy" @click="retry(item.id)">重试</button>
              <button v-if="item.status === 'failed'" class="text-slate-600" @click="takeover(item.id)">带回原文手动处理</button>
            </span>
          </li>
        </ul>
      </div>
    </div>
  </details>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import * as api from '../api/automationWork'
import { getErrorMessage } from '../utils/request'

const props = defineProps<{ teamId: number; kind: string; modelName: string; sensitivity: string; disabled?: boolean; unsupported?: string }>()
const emit = defineEmits<{ open: [id: string]; takeover: [text: string]; changed: [] }>()

const label: Record<string, string> = { queued: '排队中', processing: '整理中', ready: '待核对', applying: '保存中', retry: '保存待重试', applied: '已保存草稿', failed: '失败' }
const tone: Record<string, string> = { queued: 'text-slate-400', processing: 'text-blue-600', ready: 'text-emerald-700', applied: 'text-emerald-700', failed: 'text-red-600', retry: 'text-amber-700' }
const open = ref(false)
const staged = ref<{ name: string; text: string }[]>([])
const batch = ref<api.Batch | null>(null)
const error = ref('')
const extracting = ref(false), extracted = ref(0), pending = ref(0), starting = ref(false)
const busy = computed(() => extracting.value || starting.value)
const hint = computed(() => props.unsupported || '')
let batchId = ''
let timer: ReturnType<typeof setTimeout> | null = null

async function pick(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return
  error.value = ''
  if (staged.value.length + files.length > 10) { error.value = '一个批次最多 10 份材料'; return }
  extracting.value = true; pending.value = files.length; extracted.value = 0
  const problems: string[] = []
  for (const file of files) {
    try {
      const result = await api.importFile(props.teamId, file, props.sensitivity)
      if (result.text.length > 15000) problems.push(`「${file.name}」提取了 ${result.chars.toLocaleString()} 字，超过单份 15,000 字上限，请拆分后再导入`)
      else staged.value.push({ name: file.name, text: result.text })
    } catch (e) { problems.push(`「${file.name}」：${getErrorMessage(e, '提取失败')}`) }
    extracted.value++
  }
  extracting.value = false
  if (problems.length) error.value = problems.join('；')
  if (staged.value.reduce((n, s) => n + s.text.length, 0) > 60000) error.value = '材料合计超过 60,000 字，请移除几份后分批整理'
}

async function start() {
  starting.value = true; error.value = ''
  // 提交失败（网络超时）后重试沿用同一个批次编号：服务端按它去重，不会重复整理
  if (!batchId) batchId = crypto.randomUUID()
  try {
    batch.value = await api.createBatch({ batch_id: batchId, team_id: props.teamId, kind: props.kind, model_name: props.modelName,
      sensitivity: props.sensitivity, items: staged.value })
    staged.value = []
    poll()
  } catch (e) { error.value = getErrorMessage(e, '创建批次失败') } finally { starting.value = false }
}

function poll() {
  if (timer) clearTimeout(timer)
  if (!batch.value || batch.value.finished) { emit('changed'); return }
  timer = setTimeout(async () => {
    try { batch.value = await api.getBatch(batch.value!.batch_id) } catch { /* 网络抖动：下一轮再试 */ }
    poll()
  }, 2000)
}

async function retry(workId: string) {
  if (!batch.value) return
  try { batch.value = await api.retryBatchItem(batch.value.batch_id, workId); poll() } catch (e) { error.value = getErrorMessage(e, '重试失败') }
}

async function takeover(workId: string) {
  try { emit('takeover', (await api.getWork(workId)).source_text || '') } catch (e) { error.value = getErrorMessage(e, '读取原文失败') }
}

function reset() { batch.value = null; batchId = ''; error.value = '' }

// 离开页面再回来：还有没整理完的批次就接着看进度
onMounted(async () => {
  try {
    const recent = (await api.listBatches(props.teamId)).find(b => !b.finished)
    if (recent) { batch.value = await api.getBatch(recent.batch_id); open.value = true; poll() }
  } catch { /* 没有权限或业务暂不可用：不影响单份整理 */ }
})
onBeforeUnmount(() => { if (timer) clearTimeout(timer) })
defineExpose({ refresh: async () => { if (batch.value) batch.value = await api.getBatch(batch.value.batch_id) } })
</script>
