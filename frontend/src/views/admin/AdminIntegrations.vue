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

    <section v-if="provider === 'feishu'" class="mb-4 rounded-lg border border-indigo-100 bg-indigo-50/50 p-4" data-testid="feishu-setup-guide">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold text-slate-900">按下面 4 步完成飞书接入</h2>
          <ol class="mt-2 grid gap-2 text-xs leading-5 text-slate-600 sm:grid-cols-2 xl:grid-cols-4">
            <li><strong class="text-slate-800">1. 创建应用</strong><br />创建企业自建应用，并开启机器人能力。</li>
            <li><strong class="text-slate-800">2. 填写凭证</strong><br />从“凭证与基础信息”复制 App ID 和 App Secret。</li>
            <li><strong class="text-slate-800">3. 开启回调加密</strong><br />在“事件与回调 → 加密策略”获取 Token 和 Encrypt Key。</li>
            <li><strong class="text-slate-800">4. 配置并验证</strong><br />填写下方 HTTPS 回调地址，保存后测试连接。</li>
          </ol>
        </div>
        <a href="https://open.feishu.cn/app" target="_blank" rel="noopener noreferrer"
          class="shrink-0 text-xs font-medium text-indigo-600 hover:underline">打开飞书开放平台 ↗</a>
      </div>
    </section>

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
            <input id="f-enabled" v-model="form.enabled" type="checkbox" class="h-4 w-4" />启用接入（完整配置后才能启用）
          </label>
          <p v-if="form.enabled && missingForEnable.length" class="rounded bg-amber-50 px-3 py-2 text-xs text-amber-700" role="alert">
            还缺少：{{ missingForEnable.join('、') }}。可以先取消“启用接入”保存草稿。
          </p>
          <div class="flex flex-wrap gap-2">
            <button type="submit" :disabled="saving || !canSave" data-testid="integration-save"
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
        <p v-if="isLocalOrigin" class="mt-2 rounded bg-amber-50 px-2.5 py-2 text-xs text-amber-700">
          当前是本地地址，飞书 / 钉钉无法从公网访问。联调前请使用反向代理或内网穿透生成公网 HTTPS 地址。
        </p>
        <div v-if="health" class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div v-for="cell in healthCells" :key="cell.label" class="rounded bg-slate-50 px-2.5 py-2">
            <p class="text-[11px] text-slate-500">{{ cell.label }}</p>
            <p class="text-lg font-semibold tabular-nums" :class="cell.tone">{{ cell.value }}</p>
          </div>
        </div>
        <p v-if="health?.push" class="mt-3 text-xs text-slate-500" data-testid="integration-push">
          站内通知推送到{{ providerLabel }}：24 小时内处理 {{ health.push.done_24h }} 条 ·
          <span :class="health.push.retrying ? 'text-amber-700' : ''">重试中 {{ health.push.retrying }}</span> ·
          <span :class="health.push.dead ? 'font-medium text-red-600' : ''">死信 {{ health.push.dead }}</span>
          <template v-if="health.push.retrying || health.push.dead">
            （平台恢复后会自动补发；重试用尽的在<RouterLink to="/admin/issues" class="text-indigo-600 hover:underline">问题中心 → 死信</RouterLink>里修好后重新投递）
          </template>
        </p>
        <ul v-if="health?.recent_failures.length" class="mt-3 space-y-1 text-xs text-red-600">
          <li v-for="f in health.recent_failures" :key="f.received_at + f.event_type">{{ f.received_at.slice(0, 16).replace('T', ' ') }} · {{ integrationEventLabel(f.event_type) }} · {{ f.error }}</li>
        </ul>
        <p v-if="current?.last_error" class="mt-3 text-xs text-red-600">最近一次连接测试失败：{{ current.last_error }}</p>
      </section>
    </div>

    <!-- 接入自检：员工为什么还用不了 -->
    <section class="mt-4 rounded-lg border border-slate-200 bg-white p-4" data-testid="integration-readiness">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-slate-900">接入自检</h2>
          <p class="mt-0.5 text-xs text-slate-500">逐项检查员工能不能在{{ providerLabel }}里用上助手；没通过的项按提示处理。</p>
        </div>
        <div class="flex gap-2">
          <button :disabled="!readiness" @click="refreshReadiness" class="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">重新检查</button>
          <button :disabled="inviting || !current?.enabled" @click="invite" data-testid="integration-invite"
            class="rounded border border-indigo-200 px-3 py-1.5 text-sm text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">{{ inviting ? '发送中…' : '提醒未绑定员工' }}</button>
        </div>
      </div>
      <ul v-if="readiness" class="space-y-1.5">
        <li v-for="c in readiness.checks" :key="c.key" class="flex items-start gap-2 rounded px-2 py-1.5 text-xs" :class="checkRow[c.level]">
          <span class="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white" :class="checkDot[c.level]">{{ c.ok ? '✓' : c.level === 'info' ? 'i' : '!' }}</span>
          <div class="min-w-0">
            <p class="font-medium text-slate-800">{{ c.label }}<span class="ml-2 font-normal text-slate-500">{{ c.detail }}</span></p>
            <p v-if="c.fix" class="mt-0.5 text-slate-600">处理：{{ c.fix }}</p>
          </div>
        </li>
      </ul>
      <p v-if="readiness?.oauth_redirect_uri" class="mt-2 text-xs text-slate-500">
        一键授权绑定的回调地址（填到{{ provider === 'feishu' ? '飞书“安全设置 → 重定向 URL”' : '钉钉“登录与分享 → 回调域名”' }}）：
        <code class="break-all rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ readiness.oauth_redirect_uri }}</code>
      </p>
    </section>

    <!-- 人员绑定 -->
    <section class="mt-4 rounded-lg border border-slate-200 bg-white p-4" data-testid="integration-bindings">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-slate-900">人员绑定</h2>
          <p class="mt-0.5 text-xs text-slate-500">按部门列出每位员工对应的{{ providerLabel }}账号（一人一个）。员工可以自己领绑定码绑定；
            也可以点“同步组织架构”按手机号自动对上，对不上的在这里手动选择。已离职 / 不在通讯录里的自动停用。</p>
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
      <div v-if="directory" class="mb-3 flex flex-wrap items-center gap-2 text-xs" data-testid="binding-summary">
        <span class="rounded bg-slate-100 px-2 py-1 text-slate-700">企业成员 {{ directory.summary.members }} 人</span>
        <span class="rounded bg-emerald-50 px-2 py-1 text-emerald-700">已绑定 {{ directory.summary.bound }}</span>
        <span class="rounded px-2 py-1" :class="directory.summary.unbound ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'">未绑定 {{ directory.summary.unbound }}</span>
        <span v-if="directory.summary.disabled" class="rounded bg-red-50 px-2 py-1 text-red-600">已停用 {{ directory.summary.disabled }}</span>
        <span v-if="directory.summary.unlinked" class="rounded bg-indigo-50 px-2 py-1 text-indigo-700">没对应到员工的{{ providerLabel }}账号 {{ directory.summary.unlinked }}</span>
        <span class="grow"></span>
        <label for="binding-filter" class="sr-only">筛选</label>
        <select id="binding-filter" v-model="filter" class="h-8 rounded border border-slate-300 px-2">
          <option value="">全部员工</option>
          <option value="unbound">只看未绑定</option>
          <option value="active">只看已绑定</option>
          <option value="disabled">只看已停用</option>
        </select>
        <label for="binding-search" class="sr-only">搜索姓名</label>
        <input id="binding-search" v-model="search" placeholder="搜索姓名" class="h-8 w-36 rounded border border-slate-300 px-2" />
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[640px] text-left text-sm" data-testid="binding-directory">
          <thead class="text-xs text-slate-500">
            <tr class="border-b border-slate-100">
              <th class="py-2 pr-3 font-medium">员工（平台账号）</th>
              <th class="py-2 pr-3 font-medium">{{ providerLabel }}账号</th>
              <th class="py-2 pr-3 font-medium">状态</th>
              <th class="py-2 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody v-for="d in shownDepartments" :key="d.team_id ?? 0" :data-testid="`binding-dept-${d.team_id ?? 0}`">
            <tr class="bg-slate-50">
              <td colspan="4" class="px-2 py-1.5 text-xs font-medium text-slate-700">
                {{ d.name }}<span class="ml-2 font-normal" :class="d.bound === d.total ? 'text-emerald-600' : 'text-slate-500'">已绑定 {{ d.bound }} / {{ d.total }}</span>
              </td>
            </tr>
            <tr v-for="m in d.members" :key="m.user_id" class="border-b border-slate-50" :data-testid="`binding-member-${m.user_id}`">
              <td class="py-2 pr-3">
                <p class="text-slate-800">{{ m.name }}</p>
                <p v-if="m.role_name" class="text-[11px] text-slate-400">{{ m.role_name }}</p>
              </td>
              <td class="py-2 pr-3">
                <template v-if="m.binding">
                  <p class="text-slate-800">{{ m.binding.external_name || '（未知姓名）' }}</p>
                  <p class="break-all text-[11px] text-slate-400">{{ m.binding.external_user_id }}</p>
                </template>
                <template v-else>
                  <label :for="`assign-ext-${m.user_id}`" class="sr-only">选择{{ providerLabel }}账号</label>
                  <select :id="`assign-ext-${m.user_id}`" :disabled="!directory?.unlinked.length || busyUser === m.user_id"
                    @change="assignExternal(m.user_id, ($event.target as HTMLSelectElement).value)"
                    class="h-8 w-full max-w-[240px] rounded border border-slate-300 px-2 text-xs disabled:bg-slate-50 disabled:text-slate-400">
                    <option value="">{{ directory?.unlinked.length ? `选择${providerLabel}账号…` : '员工自己领绑定码绑定' }}</option>
                    <option v-for="u in directory?.unlinked" :key="u.external_user_id" :value="u.external_user_id">
                      {{ u.external_name || '（未知姓名）' }} · {{ u.external_user_id.slice(-8) }}
                    </option>
                  </select>
                </template>
              </td>
              <td class="py-2 pr-3">
                <span class="rounded px-1.5 py-0.5 text-xs" :class="memberClass(m)">{{ memberText(m) }}</span>
                <p v-if="m.binding?.since" class="mt-0.5 text-[11px] text-slate-400">{{ m.binding.synced ? '同步' : '绑定' }}于 {{ m.binding.since.slice(0, 10) }}</p>
              </td>
              <td class="py-2 text-right">
                <button v-if="m.binding?.status === 'active'" type="button" :disabled="busyUser === m.user_id" @click="unbindMember(m)"
                  class="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">解绑</button>
                <button v-else-if="m.binding?.status === 'disabled'" type="button" :disabled="busyUser === m.user_id"
                  @click="assignExternal(m.user_id, m.binding.external_user_id)"
                  class="rounded border border-emerald-200 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">恢复</button>
              </td>
            </tr>
          </tbody>
          <tbody v-if="directory && !shownDepartments.length">
            <tr><td colspan="4" class="py-8 text-center text-xs text-slate-400">没有符合条件的员工</td></tr>
          </tbody>
        </table>
      </div>
      <details v-if="directory?.unlinked.length" class="mt-3 rounded border border-slate-200 px-3 py-2 text-xs" data-testid="binding-unlinked">
        <summary class="cursor-pointer text-slate-700">没对应到员工的{{ providerLabel }}账号（{{ directory.unlinked.length }}）：同步通讯录或给机器人发过消息、但没对上平台账号的人</summary>
        <ul class="mt-2 divide-y divide-slate-100">
          <li v-for="u in directory.unlinked" :key="u.external_user_id" class="flex flex-wrap items-center justify-between gap-2 py-1.5">
            <span class="text-slate-800">{{ u.external_name || '（未知姓名）' }} <span class="text-[11px] text-slate-400">{{ u.external_user_id }}</span></span>
            <span>
              <label :for="`unlinked-${u.id}`" class="sr-only">对应到员工</label>
              <select :id="`unlinked-${u.id}`" @change="assignExternal(Number(($event.target as HTMLSelectElement).value), u.external_user_id)"
                class="h-7 rounded border border-slate-300 px-2">
                <option value="">对应到员工…</option>
                <option v-for="m in unboundMembers" :key="m.user_id" :value="m.user_id">{{ m.name }}（{{ m.dept }}）</option>
              </select>
            </span>
          </li>
        </ul>
      </details>
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
import type { BindingDirectory, DirectoryMember, IntegrationApp, IntegrationHealth, Provider, SyncSummary } from '../../api/integrations'
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
const directory = ref<BindingDirectory | null>(null)
const search = ref('')
const busyUser = ref<number | null>(null)
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
// 回调地址：配置了 INTEGRATION_PUBLIC_BASE_URL 时用它（飞书 / 钉钉实际访问的地址）；否则按当前页面地址推算（经 nginx / Vite 代理带 /api）
const origin = computed(() => health.value?.public_base_url || `${window.location.origin}/api`)
const form = reactive<Record<FormKey, string> & { enabled: boolean }>({
  app_id: '', app_secret: '', verification_token: '', encrypt_key: '', robot_code: '', card_template_id: '', enabled: false,
})

const FIELDS: Record<Provider, Field[]> = {
  feishu: [
    { key: 'app_id', label: 'App ID（必填）', hint: '在“凭证与基础信息 → 应用凭证”中复制' },
    { key: 'app_secret', label: 'App Secret（首次配置必填）', secret: true, hint: '与 App ID 位于同一页面，属于敏感信息，请勿泄露' },
    { key: 'verification_token', label: 'Verification Token（启用前必填）', secret: true, hint: '在“事件与回调 → 加密策略”中复制，用于确认回调属于当前飞书应用' },
    { key: 'encrypt_key', label: 'Encrypt Key（启用前必填）', secret: true, hint: '在“事件与回调 → 加密策略”中开启加密后复制；本系统只接收加密并签名的回调，以防消息被伪造' },
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
const isLocalOrigin = computed(() => ['localhost', '127.0.0.1'].includes(window.location.hostname))
const hasCredential = (key: FormKey) => {
  if (form[key].trim()) return true
  const app = current.value
  if (!app?.configured) return false
  if (key === 'app_secret') return Boolean(app.app_secret)
  if (key === 'verification_token') return Boolean(app.has_verification_token)
  if (key === 'encrypt_key') return Boolean(app.encrypt_key)
  if (key === 'robot_code') return Boolean(app.robot_code)
  return false
}
const missingForEnable = computed(() => {
  const required: { key: FormKey; label: string }[] = provider.value === 'feishu'
    ? [
        { key: 'app_secret', label: 'App Secret' },
        { key: 'verification_token', label: 'Verification Token' },
        { key: 'encrypt_key', label: 'Encrypt Key' },
      ]
    : [
        { key: 'app_secret', label: 'AppSecret' },
      ]
  return required.filter((item) => !hasCredential(item.key)).map((item) => item.label)
})
const canSave = computed(() => Boolean(
  form.app_id.trim()
  && (current.value?.configured || form.app_secret.trim())
  && (!form.enabled || missingForEnable.value.length === 0),
))
const statusText = computed(() => (!current.value?.configured ? '未配置' : current.value.enabled ? '已启用' : '已停用'))
const statusClass = computed(() => (!current.value?.configured ? 'bg-slate-100 text-slate-500'
  : current.value.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'))
const memberStatus = (m: DirectoryMember) => m.binding?.status === 'active' ? 'active' : m.binding?.status === 'disabled' ? 'disabled' : 'unbound'
const memberText = (m: DirectoryMember) => ({ active: '已绑定', disabled: '已停用', unbound: '未绑定' })[memberStatus(m)]
const memberClass = (m: DirectoryMember) => ({ active: 'bg-emerald-50 text-emerald-700', disabled: 'bg-red-50 text-red-600',
  unbound: 'bg-amber-50 text-amber-700' })[memberStatus(m)]
const shownDepartments = computed(() => (directory.value?.departments || []).map((d) => ({
  ...d,
  members: d.members.filter((m) => (!filter.value || memberStatus(m) === filter.value) && (!search.value.trim() || m.name.includes(search.value.trim()))),
})).filter((d) => d.members.length))
const unboundMembers = computed(() => (directory.value?.departments || []).flatMap((d) =>
  d.members.filter((m) => !m.binding).map((m) => ({ user_id: m.user_id, name: m.name, dept: d.name }))))

const loadDirectory = async () => {
  directory.value = await api.bindingDirectory(provider.value)
}

// 把一个外部账号对应到员工（也用于恢复已停用的绑定）。后端保证一人一个、一个外部账号只对应一人。
const assignExternal = async (userId: number, externalUserId: string) => {
  if (!userId || !externalUserId) return
  busyUser.value = userId
  error.value = ''
  try {
    await api.changeBinding(provider.value, externalUserId, userId)
    toastSuccess('已绑定')
  } catch (e: any) {
    error.value = getErrorMessage(e, '绑定失败')
  } finally {
    busyUser.value = null
    await loadDirectory()
  }
}

const unbindMember = async (m: DirectoryMember) => {
  if (!m.binding || !window.confirm(`解绑 ${m.name} 的${providerLabel.value}账号？解绑后 ta 在${providerLabel.value}里就不能使用助手了。`)) return
  busyUser.value = m.user_id
  error.value = ''
  try {
    await api.changeBinding(provider.value, m.binding.external_user_id, null)
    toastSuccess('已解绑')
  } catch (e: any) {
    error.value = getErrorMessage(e, '解绑失败')
  } finally {
    busyUser.value = null
    await loadDirectory()
  }
}
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

const readiness = ref<Awaited<ReturnType<typeof api.integrationReadiness>> | null>(null)
const inviting = ref(false)
const checkRow: Record<string, string> = { ok: 'bg-emerald-50/50', error: 'bg-red-50', warning: 'bg-amber-50', info: 'bg-slate-50' }
const checkDot: Record<string, string> = { ok: 'bg-emerald-500', error: 'bg-red-500', warning: 'bg-amber-500', info: 'bg-slate-400' }

async function refreshReadiness() {
  try {
    readiness.value = await api.integrationReadiness(provider.value)
  } catch {
    readiness.value = null
  }
}

async function invite() {
  inviting.value = true
  try {
    const result = await api.inviteUnbound(provider.value)
    toastSuccess(result.unbound ? `已提醒 ${result.sent} 位还没绑定的员工（今天已提醒过的不重复发）` : '所有员工都已绑定')
  } catch (e: any) {
    error.value = getErrorMessage(e, '发送失败')
  } finally {
    inviting.value = false
  }
}

const loadProvider = async () => {
  loaded.value = false
  testResult.value = null
  syncSummary.value = null
  fillForm()
  const [h, dir] = await Promise.all([api.integrationHealth(provider.value), api.bindingDirectory(provider.value)])
  health.value = h
  directory.value = dir
  loaded.value = true
  await refreshReadiness()
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
    if (!current.value?.configured && !form.app_secret.trim()) throw new Error('第一次配置需要填写 App Secret')
    if (form.enabled && missingForEnable.value.length) {
      throw new Error(`启用${providerLabel.value}前请先填写：${missingForEnable.value.join('、')}`)
    }
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
    await loadDirectory()
    health.value = await api.integrationHealth(provider.value)
  } catch (e: any) {
    error.value = getErrorMessage(e, '同步失败')
  } finally {
    syncing.value = false
  }
}

const manualBind = async () => {
  if (!manualUser.value) return
  error.value = ''
  try {
    await api.changeBinding(provider.value, manualExternal.value.trim(), manualUser.value)
    manualExternal.value = ''
    manualUser.value = null
    await loadDirectory()
    toastSuccess('已绑定')
  } catch (e: any) {
    error.value = getErrorMessage(e, '绑定失败')
  }
}

onMounted(load)
</script>
