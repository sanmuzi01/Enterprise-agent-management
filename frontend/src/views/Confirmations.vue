<template>
  <div class="h-screen overflow-y-auto bg-transparent p-6">
    <div class="mx-auto max-w-3xl">
      <header class="mb-4 flex items-start justify-between gap-3">
        <div>
          <h1 class="text-base font-semibold text-slate-950">待确认操作</h1>
          <p class="mt-0.5 text-xs text-slate-500">
            助手要替你提交、审批、删除的操作，都要你本人确认后才会执行。在飞书 / 钉钉里没法直接点按钮时，也可以在这里处理；10 分钟内有效。
          </p>
        </div>
        <button @click="load" :disabled="loading"
          class="inline-flex shrink-0 items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300">
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />刷新
        </button>
      </header>

      <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>

      <div v-if="loading && !items.length" class="py-12 text-center text-sm text-slate-400">加载中…</div>
      <p v-else-if="!items.length" class="rounded-lg border border-dashed border-slate-200 py-12 text-center text-sm text-slate-400" data-testid="confirmations-empty">
        没有需要你确认的操作。
      </p>
      <ul v-else class="space-y-3" data-testid="confirmations-list">
        <li v-for="item in items" :key="item.token" class="rounded-lg border border-amber-200 bg-white p-4">
          <div class="flex flex-wrap items-baseline justify-between gap-2">
            <h2 class="text-sm font-semibold text-slate-900">{{ toolDisplayName(item.tool_name) }}</h2>
            <span class="text-xs text-slate-400">{{ minutesLeft(item.expires_at) }}</span>
          </div>
          <dl class="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
            <div v-for="(value, key) in item.tool_args" :key="key" class="min-w-0">
              <dt class="inline text-slate-500">{{ key }}：</dt>
              <dd class="inline break-all text-slate-800">{{ typeof value === 'string' ? value : JSON.stringify(value) }}</dd>
            </div>
          </dl>
          <p v-if="results[item.token]" class="mt-2 text-xs text-slate-600">{{ results[item.token] }}</p>
          <div v-else class="mt-3 flex gap-2">
            <button @click="decide(item, true)" :disabled="busy === item.token"
              class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">确认执行</button>
            <button @click="decide(item, false)" :disabled="busy === item.token"
              class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">不执行</button>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { confirmToolCall, listToolConfirmations, rejectToolCall } from '../api/chat'
import type { PendingToolConfirmation } from '../api/chat'
import { toolDisplayName } from '../utils/displayNames'
import { getErrorMessage } from '../utils/request'

const items = ref<PendingToolConfirmation[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref<string | null>(null)
const results = reactive<Record<string, string>>({})

const minutesLeft = (expires: string) => {
  const ms = new Date(expires.endsWith('Z') ? expires : `${expires}Z`).getTime() - Date.now()
  return ms <= 0 ? '已过期' : `还剩 ${Math.max(1, Math.round(ms / 60000))} 分钟`
}

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    items.value = await listToolConfirmations()
  } catch (e: any) {
    error.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
}

const decide = async (item: PendingToolConfirmation, confirm: boolean) => {
  busy.value = item.token
  error.value = ''
  try {
    if (confirm) {
      const outcome = await confirmToolCall(item.token)
      results[item.token] = `已执行：${String(outcome.result).slice(0, 300)}`
    } else {
      await rejectToolCall(item.token)
      results[item.token] = '已取消，不会执行。'
    }
  } catch (e: any) {
    error.value = getErrorMessage(e, '处理失败')
  } finally {
    busy.value = null
  }
}

onMounted(load)
</script>
