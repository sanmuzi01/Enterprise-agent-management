<template>
  <div class="p-6">
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <p class="max-w-3xl text-xs leading-relaxed text-slate-500">
        员工可以直接在飞书 / 钉钉里给机器人发消息使用助手，用的是和网页同一套助手、权限和额度。
        只有绑定了平台账号的员工能用；需要确认的操作会以卡片发给本人，确认后才执行。密钥加密保存，保存后只显示末四位。
      </p>
      <div class="flex gap-1 rounded-lg bg-slate-100 p-0.5" role="tablist">
        <button v-for="p in providers" :key="p.value" role="tab" :aria-selected="provider === p.value"
          :data-testid="`integration-tab-${p.value}`" @click="switchProvider(p.value)"
          :class="['rounded-md px-3 py-1 text-sm', provider === p.value ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800']">
          {{ p.label }}
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ error }}</p>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <!-- 应用配置 -->
      <section class="rounded-lg border border-slate-200 bg-white p-4" data-testid="integration-config">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h2 class="text-sm font-semibold text-slate-900">应用凭证</h2>
          <span class="rounded px-2 py-0.5 text-xs" :class="statusClass">{{ statusText }}</span>
        </div>
        <form class="space-y-3" @submit.prevent="save">
          <!-- 加载完之前不能填：加载完成时会用已保存的配置覆盖表单 -->
          <fieldset :disabled="!loaded" class="space-y-3" :data-loaded="loaded">
          <div v-for="field in fields" :key="field.key">
            <label :for="`f-${field.key}`" class="block text-xs text-slate-500">{{ field.label }}</label>
            <input :id="`f-${field.key}`" v-model="form[field.key]" :type="field.secret ? 'password' : 'text'" autocomplete="off"
              :placeholder="placeholder(field)"
              class="mt-1 h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
            <p v-if="field.hint" class="mt-1 text-[11px] text-slate-400">{{ field.hint }}</p>
          </div>
          <label class="flex items-center gap-2 text-sm text-slate-700">
            <input id="f-enabled" v-model="form.enabled" type="checkbox" class="h-4 w-4" />启用（启用后才接收回调）
          </label>
          <div class="flex flex-wrap gap-2">
            <button type="submit" :disabled="saving || !form.app_id" data-testid="integration-save"
              class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">{{ saving ? '保存中…' : '保存' }}</button>
            <button type="button" :disabled="!current?.configured || testing" @click="runTest" data-testid="integration-test"
              class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">{{ testing ? '测试中…' : '测试连接' }}</button>
          </div>
          <p v-if="testResult" class="text-xs" :class="testResult.ok ? 'text-emerald-600' : 'text-red-600'">
            {{ testResult.ok ? '连接正常，凭证有效' : `连接失败：${testResult.error}` }}
          </p>
          </fieldset>
        </form>
      </section>

      <!-- 回调地址与运行情况 -->
      <section class="rounded-lg border border-slate-200 bg-white p-4" data-testid="integration-health">
        <h2 class="mb-3 text-sm font-semibold text-slate-900">回调地址与运行情况</h2>
        <dl class="space-y-2 text-xs">
          <div>
            <dt class="text-slate-500">{{ provider === 'feishu' ? '事件订阅请求地址' : '机器人消息接收地址 / 事件订阅地址' }}</dt>
            <dd class="mt-0.5"><code class="break-all rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ origin }}{{ health?.callback_paths.events || `/integrations/${provider}/events` }}</code></dd>
          </div>
          <div>
            <dt class="text-slate-500">卡片回调地址</dt>
            <dd class="mt-0.5"><code class="break-all rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ origin }}{{ health?.callback_paths.card_actions || `/integrations/${provider}/card-actions` }}</code></dd>
          </div>
        </dl>
        <p class="mt-2 text-[11px] text-slate-400">地址必须是公网 HTTPS，填到{{ provider === 'feishu' ? '飞书开放平台“事件与回调”' : '钉钉开放平台“机器人”和“事件订阅”' }}里。</p>
        <div v-if="health" class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div v-for="cell in healthCells" :key="cell.label" class="rounded bg-slate-50 px-2.5 py-2">
            <p class="text-[11px] text-slate-500">{{ cell.label }}</p>
            <p class="text-lg font-semibold tabular-nums" :class="cell.tone">{{ cell.value }}</p>
          </div>
        </div>
        <ul v-if="health?.recent_failures.length" class="mt-3 space-y-1 text-xs text-red-600">
          <li v-for="f in health.recent_failures" :key="f.received_at + f.event_type">{{ f.received_at.slice(0, 16).replace('T', ' ') }} · {{ integrationEventLabel(f.event_type) }} · {{ f.error }}</li>
        </ul>
        <p v-if="current?.last_error" class="mt-3 text-xs text-red-600">最近一次连接测试失败：{{ current.last_error }}</p>
      </section>
    </div>

    <!-- 人员绑定 -->
    <section class="mt-4 rounded-lg border border-slate-200 bg-white p-4" data-testid="integration-bindings">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-slate-900">人员绑定</h2>
          <p class="mt-0.5 text-xs text-slate-500">同步时按手机号自动对上平台账号；对不上的在这里手动选择。已离职 / 不在通讯录里的自动停用。</p>
        </div>
        <button :disabled="!current?.configured || syncing" @click="runSync" data-testid="integration-sync"
          class="inline-flex items-center gap-2 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">
          <RefreshCcw :size="14" :class="syncing ? 'animate-spin' : ''" />{{ syncing ? '同步中…' : '同步组织架构' }}
        </button>
      </div>
      <p v-if="syncSummary" class="mb-3 rounded bg-slate-50 px-3 py-2 text-xs text-slate-600" data-testid="integration-sync-summary">
        通讯录 {{ syncSummary.users }} 人，自动对上 {{ syncSummary.users_matched }} 人，待手动绑定 {{ syncSummary.users_unmatched_total }} 人，
        停用 {{ syncSummary.users_disabled_total }} 人；部门 {{ syncSummary.departments }} 个<template v-if="syncSummary.departments_unmatched.length">，
        其中 {{ syncSummary.departments_unmatched.length }} 个在平台里找不到同名部门</template>。
      </p>
      <div class="mb-3 flex flex-wrap gap-2">
        <label for="binding-filter" class="sr-only">筛选</label>
        <select id="binding-filter" v-model="filter" class="h-8 rounded border border-slate-300 px-2 text-xs">
          <option value="">全部（{{ bindings.length }}）</option>
          <option value="unmatched">待绑定（{{ count('unmatched') }}）</option>
          <option value="active">已绑定（{{ count('active') }}）</option>
          <option value="disabled">已停用（{{ count('disabled') }}）</option>
        </select>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[560px] text-left text-sm">
          <thead class="text-xs text-slate-500">
            <tr class="border-b border-slate-100">
              <th class="py-2 pr-3 font-medium">{{ providerLabel }}成员</th>
              <th class="py-2 pr-3 font-medium">状态</th>
              <th class="py-2 pr-3 font-medium">平台账号</th>
              <th class="py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in shownBindings" :key="b.id" class="border-b border-slate-50">
              <td class="py-2 pr-3">
                <p class="text-slate-800">{{ b.external_name || '（未知姓名）' }}</p>
                <p class="text-[11px] text-slate-400">{{ b.external_user_id }}</p>
              </td>
              <td class="py-2 pr-3"><span class="rounded px-1.5 py-0.5 text-xs" :class="bindingClass[b.status]">{{ bindingText[b.status] }}</span></td>
              <td class="py-2 pr-3">
                <label :for="`bind-${b.id}`" class="sr-only">选择平台账号</label>
                <select :id="`bind-${b.id}`" :value="b.local_user_id ?? ''" @change="rebind(b, ($event.target as HTMLSelectElement).value)"
                  class="h-8 w-full max-w-[220px] rounded border border-slate-300 px-2 text-xs">
                  <option value="">（不绑定）</option>
                  <option v-for="m in members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option>
                </select>
              </td>
              <td class="py-2 text-right text-[11px] text-slate-400">{{ b.last_synced_at ? `同步于 ${b.last_synced_at.slice(0, 10)}` : '手动绑定' }}</td>
            </tr>
            <tr v-if="!shownBindings.length">
              <td colspan="4" class="py-8 text-center text-xs text-slate-400">还没有人员。保存凭证后点“同步组织架构”。</td>
            </tr>
          </tbody>
        </table>
      </div>
      <form class="mt-3 flex flex-wrap items-end gap-2 text-xs" @submit.prevent="manualBind">
        <div>
          <label for="manual-ext" class="block text-slate-500">手动添加：{{ provider === 'feishu' ? '飞书 open_id' : '钉钉 userId' }}</label>
          <input id="manual-ext" v-model="manualExternal" class="mt-1 h-8 w-56 rounded border border-slate-300 px-2" />
        </div>
        <div>
          <label for="manual-user" class="block text-slate-500">平台账号</label>
          <select id="manual-user" v-model="manualUser" class="mt-1 h-8 w-44 rounded border border-slate-300 px-2">
            <option :value="null">请选择</option>
            <option v-for="m in members" :key="m.user_id" :value="m.user_id">{{ m.name }}</option>
          </select>
        </div>
        <button type="submit" :disabled="!manualExternal.trim() || !manualUser || !current?.configured"
          class="h-8 rounded border border-indigo-200 px-3 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">绑定</button>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import * as api from '../../api/integrations'
import type { IntegrationApp, IntegrationBinding, IntegrationHealth, Provider, SyncSummary } from '../../api/integrations'
import { listOrgMembers } from '../../api/organizationAdmin'
import type { OrgMember } from '../../api/organizationAdmin'
import { integrationEventLabel } from '../../utils/displayNames'
import { getErrorMessage } from '../../utils/request'
import { toastSuccess } from '../../utils/toast'

type FormKey = 'app_id' | 'app_secret' | 'verification_token' | 'encrypt_key' | 'robot_code' | 'card_template_id'
interface Field { key: FormKey; label: string; secret?: boolean; hint?: string }

const providers: { value: Provider; label: string }[] = [{ value: 'feishu', label: '飞书' }, { value: 'dingtalk', label: '钉钉' }]
const provider = ref<Provider>('feishu')
const apps = ref<IntegrationApp[]>([])
const health = ref<IntegrationHealth | null>(null)
const bindings = ref<IntegrationBinding[]>([])
const members = ref<OrgMember[]>([])
const error = ref('')
const loaded = ref(false)
const saving = ref(false)
const testing = ref(false)
const syncing = ref(false)
const testResult = ref<{ ok: boolean; error: string | null } | null>(null)
const syncSummary = ref<SyncSummary | null>(null)
const filter = ref('')
const manualExternal = ref('')
const manualUser = ref<number | null>(null)
// 后端接口在 /api 下（nginx / Vite 代理去掉这个前缀再转给后端），平台回调地址也要带上
const origin = `${window.location.origin}/api`
const form = reactive<Record<FormKey, string> & { enabled: boolean }>({
  app_id: '', app_secret: '', verification_token: '', encrypt_key: '', robot_code: '', card_template_id: '', enabled: false,
})

const FIELDS: Record<Provider, Field[]> = {
  feishu: [
    { key: 'app_id', label: 'App ID' },
    { key: 'app_secret', label: 'App Secret', secret: true },
    { key: 'verification_token', label: 'Verification Token', hint: '“事件与回调 → 加密策略”里的 Verification Token' },
    { key: 'encrypt_key', label: 'Encrypt Key（必填）', secret: true, hint: '不配置加密时飞书不给回调签名，所以这里要求必须配置' },
  ],
  dingtalk: [
    { key: 'app_id', label: 'AppKey（Client ID）' },
    { key: 'app_secret', label: 'AppSecret（Client Secret）', secret: true, hint: '机器人消息的签名也用它校验' },
    { key: 'robot_code', label: '机器人 robotCode', hint: '主动发消息（确认结果、卡片）需要' },
    { key: 'verification_token', label: '事件订阅签名 Token', hint: '接收离职事件需要；不订阅事件可留空' },
    { key: 'encrypt_key', label: '事件订阅加密 aes_key', secret: true },
    { key: 'card_template_id', label: '互动卡片模板 ID（可选）', hint: '不填时确认操作发 Markdown，提示到网页工作台确认' },
  ],
}
const fields = computed(() => FIELDS[provider.value])
const current = computed(() => apps.value.find((a) => a.provider === provider.value))
const providerLabel = computed(() => (provider.value === 'feishu' ? '飞书' : '钉钉'))
const statusText = computed(() => (!current.value?.configured ? '未配置' : current.value.enabled ? '已启用' : '已停用'))
const statusClass = computed(() => (!current.value?.configured ? 'bg-slate-100 text-slate-500'
  : current.value.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'))
const bindingText: Record<string, string> = { active: '已绑定', unmatched: '待绑定', disabled: '已停用' }
const bindingClass: Record<string, string> = {
  active: 'bg-emerald-50 text-emerald-700', unmatched: 'bg-amber-50 text-amber-700', disabled: 'bg-slate-100 text-slate-500',
}
const shownBindings = computed(() => (filter.value ? bindings.value.filter((b) => b.status === filter.value) : bindings.value))
const count = (status: string) => bindings.value.filter((b) => b.status === status).length
const healthCells = computed(() => {
  const e = health.value?.events_24h || {}
  const total = Object.values(e).reduce((a, b) => a + b, 0)
  return [
    { label: '24 小时回调', value: total, tone: 'text-slate-900' },
    { label: '处理完成', value: e.done || 0, tone: 'text-emerald-600' },
    { label: '处理失败', value: e.failed || 0, tone: (e.failed || 0) ? 'text-red-600' : 'text-slate-900' },
    { label: '已绑定人数', value: health.value?.bindings.active || 0, tone: 'text-slate-900' },
  ]
})

const placeholder = (field: Field) => {
  const app = current.value
  if (field.key === 'verification_token' && app?.has_verification_token) return '已填写，留空表示不修改'
  if (!field.secret || !app?.configured) return ''
  const tail = field.key === 'app_secret' ? app.app_secret : app.encrypt_key
  return tail ? `已填写（${tail}），留空表示不修改` : '未填写'
}

const fillForm = () => {
  const app = current.value
  form.app_id = app?.app_id || ''
  form.app_secret = ''
  form.encrypt_key = ''
  form.verification_token = ''
  form.robot_code = app?.robot_code || ''
  form.card_template_id = app?.card_template_id || ''
  form.enabled = !!app?.enabled
}

const loadProvider = async () => {
  loaded.value = false
  testResult.value = null
  syncSummary.value = null
  fillForm()
  const [h, b] = await Promise.all([api.integrationHealth(provider.value), api.listBindings(provider.value)])
  health.value = h
  bindings.value = b
  loaded.value = true
}

const load = async () => {
  error.value = ''
  try {
    const [list, orgMembers] = await Promise.all([api.listIntegrations(), listOrgMembers()])
    apps.value = list
    members.value = orgMembers.filter((m) => m.status === 'active')
    await loadProvider()
  } catch (e: any) {
    error.value = getErrorMessage(e, '加载失败')
  }
}

const switchProvider = async (value: Provider) => {
  provider.value = value
  error.value = ''
  try { await loadProvider() } catch (e: any) { error.value = getErrorMessage(e, '加载失败') }
}

const save = async () => {
  saving.value = true
  error.value = ''
  try {
    const payload: api.IntegrationAppForm = { app_id: form.app_id.trim(), enabled: form.enabled,
      robot_code: form.robot_code, card_template_id: form.card_template_id }
    if (form.app_secret.trim()) payload.app_secret = form.app_secret.trim()
    if (form.encrypt_key.trim()) payload.encrypt_key = form.encrypt_key.trim()
    if (form.verification_token.trim()) payload.verification_token = form.verification_token.trim()
    const saved = await api.saveIntegration(provider.value, payload)
    apps.value = apps.value.map((a) => (a.provider === saved.provider ? saved : a))
    fillForm()
    toastSuccess('已保存')
  } catch (e: any) {
    error.value = getErrorMessage(e, '保存失败')
  } finally {
    saving.value = false
  }
}

const runTest = async () => {
  testing.value = true
  try {
    testResult.value = await api.testIntegration(provider.value)
    apps.value = await api.listIntegrations()
  } catch (e: any) {
    error.value = getErrorMessage(e, '测试失败')
  } finally {
    testing.value = false
  }
}

const runSync = async () => {
  syncing.value = true
  error.value = ''
  try {
    syncSummary.value = await api.syncOrganization(provider.value)
    bindings.value = await api.listBindings(provider.value)
    health.value = await api.integrationHealth(provider.value)
  } catch (e: any) {
    error.value = getErrorMessage(e, '同步失败')
  } finally {
    syncing.value = false
  }
}

const rebind = async (b: IntegrationBinding, value: string) => {
  error.value = ''
  try {
    await api.changeBinding(provider.value, b.external_user_id, value ? Number(value) : null)
    bindings.value = await api.listBindings(provider.value)
    toastSuccess(value ? '已绑定' : '已解绑')
  } catch (e: any) {
    error.value = getErrorMessage(e, '绑定失败')
    bindings.value = await api.listBindings(provider.value)
  }
}

const manualBind = async () => {
  if (!manualUser.value) return
  error.value = ''
  try {
    await api.changeBinding(provider.value, manualExternal.value.trim(), manualUser.value)
    manualExternal.value = ''
    manualUser.value = null
    bindings.value = await api.listBindings(provider.value)
    toastSuccess('已绑定')
  } catch (e: any) {
    error.value = getErrorMessage(e, '绑定失败')
  }
}

onMounted(load)
</script>
