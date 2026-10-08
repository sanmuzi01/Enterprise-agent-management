<template>
  <div class="p-6">
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <p class="max-w-3xl text-xs leading-relaxed text-slate-500">
        在这里按服务商连接一次 API Key，全公司共用，员工不用再各自配置。员工自己填了个人密钥的话，仍然优先用他自己的。
        密钥加密保存，保存后不会再显示，忘了只能重新填。
      </p>
      <button @click="load" :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300">
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />刷新
      </button>
    </div>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>

    <div v-if="loading && !items.length" class="py-12 text-center text-sm text-slate-400">加载中…</div>
    <div v-else class="grid gap-4 lg:grid-cols-2" data-testid="llm-providers">
      <article v-for="item in items" :key="item.provider" class="rounded-lg border border-slate-200 bg-white p-4" :data-testid="`llm-${item.provider}`">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <KeyRound :size="15" class="text-slate-400" />{{ item.label }}
            </h2>
            <p class="mt-1 text-xs text-slate-500">
              聊天 {{ item.chat_models.length }} 个模型<template v-if="item.embedding_models.length">，资料读取 {{ item.embedding_models.length }} 个</template>
              <template v-if="item.default_chat">；默认聊天模型：{{ modelDisplayName(item.default_chat) }}</template>
            </p>
          </div>
          <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="statusClass(item)">{{ statusText(item) }}</span>
        </div>

        <p v-if="item.connected" class="mt-2 text-xs text-slate-500">
          密钥 <code class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ item.key_hint }}</code>
          <span v-if="item.updated_at"> · 更新于 {{ item.updated_at }}</span>
        </p>

        <div v-if="editing === item.provider" class="mt-3 space-y-2">
          <label :for="`key-${item.provider}`" class="block text-xs text-slate-500">{{ item.connected ? '新的 API Key（会替换旧的）' : 'API Key' }}</label>
          <input :id="`key-${item.provider}`" v-model="draftKey" type="password" autocomplete="off" placeholder="粘贴 API Key" data-testid="llm-key-input"
            class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
          <div class="flex items-center gap-2">
            <button @click="save(item)" :disabled="saving || !draftKey.trim()" data-testid="llm-save"
              class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">{{ saving ? '保存中…' : '保存' }}</button>
            <button @click="cancelEdit" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          </div>
        </div>

        <div v-else class="mt-3 flex flex-wrap gap-2 text-xs">
          <button @click="startEdit(item)" data-testid="llm-connect"
            class="rounded border border-indigo-200 px-2.5 py-1 text-indigo-700 hover:bg-indigo-50">{{ item.connected ? '更换密钥' : '连接' }}</button>
          <template v-if="item.connected">
            <button @click="runTest(item)" :disabled="testing === item.provider" data-testid="llm-test"
              class="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              {{ testing === item.provider ? '测试中…' : '测试连接' }}</button>
            <button @click="toggle(item)" class="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50">
              {{ item.is_active ? '停用' : '启用' }}</button>
            <button @click="remove(item)" class="rounded border border-red-200 px-2.5 py-1 text-red-600 hover:bg-red-50">删除</button>
          </template>
        </div>

        <ul v-if="results[item.provider]" class="mt-3 space-y-1 rounded bg-slate-50 p-2.5 text-xs" data-testid="llm-test-result">
          <li v-for="(probe, kind) in results[item.provider].results" :key="kind" class="flex items-start gap-1.5">
            <CheckCircle2 v-if="probe?.ok" :size="14" class="mt-0.5 shrink-0 text-emerald-500" />
            <AlertTriangle v-else :size="14" class="mt-0.5 shrink-0 text-red-500" />
            <span :class="probe?.ok ? 'text-slate-700' : 'text-red-600'">
              {{ kind === 'chat' ? '聊天模型' : '资料读取模型' }}：{{ probe?.ok ? `连接正常（${probe?.elapsed_ms ?? '-'} 毫秒）` : (probe?.error || probe?.message || '连接失败') }}
            </span>
          </li>
        </ul>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { AlertTriangle, CheckCircle2, KeyRound, RefreshCcw } from 'lucide-vue-next'
import * as api from '../../api/adminLlm'
import type { LlmConnection, LlmConnectionTest } from '../../api/adminLlm'
import { modelDisplayName } from '../../utils/displayNames'
import { getErrorMessage } from '../../utils/request'
import { toastSuccess } from '../../utils/toast'

const items = ref<LlmConnection[]>([])
const loading = ref(true)
const error = ref('')
const editing = ref<string | null>(null)
const draftKey = ref('')
const saving = ref(false)
const testing = ref<string | null>(null)
const results = reactive<Record<string, LlmConnectionTest>>({})

const statusText = (item: LlmConnection) => (!item.connected ? '未连接' : item.is_active ? '已连接' : '已停用')
const statusClass = (item: LlmConnection) => (!item.connected ? 'bg-slate-100 text-slate-500'
  : item.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    items.value = await api.listLlmConnections()
  } catch (e: any) {
    error.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

const startEdit = (item: LlmConnection) => { editing.value = item.provider; draftKey.value = ''; error.value = '' }
const cancelEdit = () => { editing.value = null; draftKey.value = '' }

const replace = (updated: LlmConnection) => {
  items.value = items.value.map((i) => (i.provider === updated.provider ? updated : i))
}

const save = async (item: LlmConnection) => {
  saving.value = true
  error.value = ''
  try {
    replace(await api.connectLlm(item.provider, draftKey.value.trim()))
    delete results[item.provider]
    cancelEdit()
    toastSuccess(`已保存「${item.label}」的 API Key`)
  } catch (e: any) {
    error.value = getErrorMessage(e, '保存失败')
  } finally {
    saving.value = false
  }
}

const runTest = async (item: LlmConnection) => {
  testing.value = item.provider
  error.value = ''
  try {
    results[item.provider] = await api.testLlm(item.provider)
  } catch (e: any) {
    error.value = getErrorMessage(e, '测试失败')
  } finally {
    testing.value = null
  }
}

const toggle = async (item: LlmConnection) => {
  if (item.is_active && !confirm(`停用后，没有个人密钥的员工将无法使用「${item.label}」的模型。继续吗？`)) return
  try {
    replace(await api.toggleLlm(item.provider, !item.is_active))
  } catch (e: any) {
    error.value = getErrorMessage(e, '操作失败')
  }
}

const remove = async (item: LlmConnection) => {
  if (!confirm(`删除「${item.label}」的统一连接？没有个人密钥的员工将无法使用它的模型。`)) return
  try {
    await api.removeLlm(item.provider)
    delete results[item.provider]
    await load()
  } catch (e: any) {
    error.value = getErrorMessage(e, '删除失败')
  }
}

onMounted(load)
</script>
