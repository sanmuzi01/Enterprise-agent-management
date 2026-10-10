<template>
  <div class="h-screen overflow-y-auto bg-transparent p-6">
    <div class="mx-auto max-w-5xl">
      <header class="mb-4">
        <div class="mb-3">
          <h1 class="text-base font-semibold text-slate-950">设置</h1>
          <p class="text-xs text-slate-500">绑定飞书 / 钉钉账号后，可以直接在飞书 / 钉钉里给企业机器人发消息办事，用的是和网页一样的助手和权限。</p>
        </div>
        <SectionTabs :tabs="[
          { label: '模型连接', path: '/llm-configs' },
          { label: '个性化与系统状态', path: '/settings' },
          { label: '飞书 / 钉钉', path: '/settings/integrations' },
        ]" />
      </header>

      <p v-if="banner" class="mb-4 rounded border px-3 py-2 text-sm" :class="banner.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-700'"
        role="status" data-testid="integration-banner">{{ banner.text }}</p>
      <div v-if="loading" class="py-12 text-center text-sm text-slate-400">加载中…</div>

      <div v-else class="grid gap-4 lg:grid-cols-2" data-testid="my-integrations">
        <article v-for="item in items" :key="item.provider" class="ui-card rounded-lg p-5" :data-testid="`my-integration-${item.provider}`">
          <div class="flex items-start justify-between gap-3">
            <h2 class="text-sm font-semibold text-slate-900">{{ item.label }}</h2>
            <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="badge[item.status]">{{ statusText[item.status] }}</span>
          </div>

          <p v-if="item.status === 'unavailable'" class="mt-3 text-sm text-slate-500">企业还没有开通{{ item.label }}接入。需要的话请联系管理员。</p>

          <template v-else-if="item.status === 'bound'">
            <p class="mt-3 text-sm text-slate-700">已绑定{{ item.label }}账号<b v-if="item.external_name">「{{ item.external_name }}」</b>。在{{ item.label }}里私聊企业机器人，或在群里 @ 它就能办事。</p>
            <div class="mt-4 flex flex-wrap gap-2">
              <a v-if="item.bot_link" :href="item.bot_link" target="_blank" rel="noopener noreferrer"
                class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">打开机器人对话</a>
              <button :disabled="busy" @click="unbind(item)" class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">解绑</button>
            </div>
          </template>

          <p v-else-if="item.status === 'disabled'" class="mt-3 text-sm text-slate-600">
            你的{{ item.label }}绑定已被管理员停用（可能是离职或账号调整）。如有疑问请联系管理员。
          </p>

          <template v-else>
            <ol class="mt-3 list-decimal space-y-1 pl-5 text-sm text-slate-600">
              <li>点下面的“获取绑定码”（10 分钟内有效）；</li>
              <li>在{{ item.label }}里<b>私聊</b>企业机器人，发送“绑定 绑定码”。</li>
            </ol>
            <div v-if="codes[item.provider]" class="mt-3 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3" :data-testid="`bind-code-${item.provider}`">
              <p class="text-xs text-indigo-700">发给机器人：</p>
              <p class="mt-1 font-mono text-2xl font-semibold tracking-widest text-indigo-900">{{ codes[item.provider]!.command }}</p>
              <p class="mt-1 text-xs text-indigo-700">{{ remaining(item.provider) }}。绑定后刷新本页查看结果。</p>
            </div>
            <div class="mt-4 flex flex-wrap gap-2">
              <button :disabled="busy" @click="getCode(item)" :data-testid="`get-code-${item.provider}`"
                class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">{{ codes[item.provider] ? '重新获取绑定码' : '获取绑定码' }}</button>
              <a v-if="item.bot_link" :href="item.bot_link" target="_blank" rel="noopener noreferrer"
                class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">打开机器人对话</a>
              <button v-if="item.oauth_available" :disabled="busy" @click="authorize(item)"
                class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">用{{ item.label }}授权绑定</button>
              <button :disabled="busy" @click="load" class="rounded px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-100">刷新</button>
            </div>
            <p v-if="item.provider === 'dingtalk'" class="mt-2 text-xs text-slate-400">在钉钉里找不到机器人：搜索企业机器人的名称，或在工作台里打开企业应用后找到“机器人”。</p>
          </template>
        </article>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SectionTabs from '../components/SectionTabs.vue'
import * as api from '../api/integrations'
import type { MyIntegration, Provider } from '../api/integrations'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'

const route = useRoute()
const router = useRouter()
const items = ref<MyIntegration[]>([])
const loading = ref(true)
const busy = ref(false)
const banner = ref<{ ok: boolean; text: string } | null>(null)
const codes = reactive<Partial<Record<Provider, { command: string; expiresAt: number }>>>({})
const now = ref(Date.now())
const timer = window.setInterval(() => { now.value = Date.now() }, 1000)

const statusText = { unavailable: '企业未开通', unbound: '未绑定', bound: '已绑定', disabled: '已停用' }
const badge = { unavailable: 'bg-slate-100 text-slate-500', unbound: 'bg-amber-50 text-amber-700',
  bound: 'bg-emerald-50 text-emerald-700', disabled: 'bg-red-50 text-red-600' }

function remaining(provider: Provider) {
  const left = Math.max(0, Math.round(((codes[provider]?.expiresAt ?? 0) - now.value) / 1000))
  return left ? `还剩 ${Math.floor(left / 60)} 分 ${left % 60} 秒` : '已过期，请重新获取'
}

async function load() {
  try {
    items.value = await api.myIntegrations()
    for (const item of items.value) if (item.status !== 'unbound') delete codes[item.provider]
  } catch (e) {
    toastError(getErrorMessage(e, '加载失败'))
  } finally {
    loading.value = false
  }
}

async function getCode(item: MyIntegration) {
  busy.value = true
  try {
    const result = await api.getBindCode(item.provider)
    codes[item.provider] = { command: result.command, expiresAt: Date.now() + result.expires_in * 1000 }
  } catch (e) {
    toastError(getErrorMessage(e, '获取绑定码失败'))
  } finally {
    busy.value = false
  }
}

async function authorize(item: MyIntegration) {
  busy.value = true
  try {
    window.location.href = await api.oauthStart(item.provider)
  } catch (e) {
    toastError(getErrorMessage(e, '无法发起授权'))
    busy.value = false
  }
}

async function unbind(item: MyIntegration) {
  if (!window.confirm(`解绑后就不能在${item.label}里使用助手了，确定解绑？`)) return
  busy.value = true
  try {
    await api.unbindMine(item.provider)
    toastSuccess('已解绑')
    await load()
  } catch (e) {
    toastError(getErrorMessage(e, '解绑失败'))
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  // 一键授权回来时带着结果
  const { result, message } = route.query
  if (result) {
    banner.value = { ok: result === 'ok', text: String(message || (result === 'ok' ? '绑定成功' : '绑定失败')) }
    void router.replace({ path: route.path })
  }
  await load()
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>
