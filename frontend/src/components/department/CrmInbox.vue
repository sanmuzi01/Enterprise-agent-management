<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="crm-inbox">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">客户沟通收集</h2>
        <p class="mt-0.5 text-xs text-slate-500">
          邮件、会议、飞书聊天记录自动对上客户；对不上的放在下面等你指定。只收你主动转进来的内容和开启了记录的群，不读其他邮件和私聊。
        </p>
      </div>
      <div class="flex flex-wrap gap-2 text-xs">
        <label class="cursor-pointer rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50">
          导入邮件（.eml）
          <input type="file" accept=".eml" multiple class="sr-only" data-testid="crm-upload-eml" @change="onEmail" />
        </label>
        <label class="cursor-pointer rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50">
          导入会议（.ics）
          <input type="file" accept=".ics" class="sr-only" data-testid="crm-upload-ics" @change="onCalendar" />
        </label>
        <button type="button" class="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50"
          :aria-expanded="mailboxOpen" @click="mailboxOpen = !mailboxOpen">
          {{ mailbox ? '邮箱已连接' : '连接邮箱' }}
        </button>
        <button type="button" class="rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50"
          :aria-expanded="groupsOpen" data-testid="crm-chat-groups-toggle" @click="groupsOpen = !groupsOpen">
          飞书群记录{{ activeGroups ? `（${activeGroups}）` : '' }}
        </button>
      </div>
    </div>

    <form v-if="mailboxOpen" class="grid gap-2 border-b border-slate-100 bg-slate-50 px-4 py-3 text-xs sm:grid-cols-2" @submit.prevent="saveBox">
      <p class="sm:col-span-2 text-slate-500">
        支持 Microsoft 365 / Outlook（outlook.office365.com）、Gmail（imap.gmail.com）、企业邮箱的 IMAP。
        只读取下面这个文件夹：把要进 CRM 的邮件转发或移动到这里。密码请用邮箱的“授权码 / 应用专用密码”。
      </p>
      <div>
        <label for="mb-host" class="block text-slate-500">IMAP 服务器</label>
        <input id="mb-host" v-model="box.imap_host" class="mt-1 h-8 w-full rounded border border-slate-300 px-2" placeholder="outlook.office365.com" />
      </div>
      <div>
        <label for="mb-user" class="block text-slate-500">邮箱账号</label>
        <input id="mb-user" v-model="box.username" class="mt-1 h-8 w-full rounded border border-slate-300 px-2" autocomplete="off" />
      </div>
      <div>
        <label for="mb-pass" class="block text-slate-500">授权码</label>
        <input id="mb-pass" v-model="box.password" type="password" autocomplete="new-password"
          :placeholder="mailbox ? '已保存，留空表示不修改' : ''" class="mt-1 h-8 w-full rounded border border-slate-300 px-2" />
      </div>
      <div>
        <label for="mb-folder" class="block text-slate-500">只读取的文件夹</label>
        <input id="mb-folder" v-model="box.folder" class="mt-1 h-8 w-full rounded border border-slate-300 px-2" />
      </div>
      <div class="flex flex-wrap items-center gap-2 sm:col-span-2">
        <button type="submit" :disabled="busy" class="rounded bg-indigo-600 px-3 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">保存</button>
        <button v-if="mailbox" type="button" :disabled="busy" @click="syncBox" class="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-white disabled:opacity-50">立即同步</button>
        <button v-if="mailbox" type="button" :disabled="busy" @click="removeBox" class="rounded border border-red-200 px-3 py-1 text-red-600 hover:bg-red-50 disabled:opacity-50">断开</button>
        <span v-if="mailbox?.last_synced_at" class="text-slate-400">上次同步 {{ formatTime(mailbox.last_synced_at) }}</span>
        <span v-if="mailbox?.last_error" class="text-red-600">{{ mailbox.last_error }}</span>
      </div>
    </form>

    <div v-if="groupsOpen" class="border-b border-slate-100 bg-slate-50 px-4 py-3 text-xs" data-testid="crm-chat-groups">
      <div class="space-y-1 text-slate-600">
        <p><b class="text-slate-800">转发聊天记录：</b>在飞书里把和客户的聊天记录“合并转发”给企业机器人，整段会存成一条客户活动。</p>
        <p><b class="text-slate-800">整群记录：</b>销售负责人或群主在客户群里 @机器人 发“开启CRM记录 客户名称”（客户名可不写），
          机器人会在群里公告，之后群里的聊天每段对话结束 30 分钟后整理进 CRM；发“关闭CRM记录”随时关闭。没开启的群不会保存任何消息。</p>
      </div>
      <ul v-if="groups.length" class="mt-3 space-y-2">
        <li v-for="g in groups" :key="g.id" class="rounded border border-slate-200 bg-white px-3 py-2" :data-testid="`crm-chat-group-${g.id}`">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="font-medium text-slate-800">
              {{ g.chat_name }}
              <span class="ml-1 rounded px-1.5 py-0.5 font-normal" :class="g.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'">
                {{ g.status === 'active' ? '记录中' : '已关闭' }}
              </span>
            </p>
            <button v-if="g.status === 'active' && canManageGroups" type="button" :disabled="busy" @click="closeGroup(g)"
              class="rounded border border-red-200 px-2.5 py-1 text-red-600 hover:bg-red-50 disabled:opacity-50">关闭记录</button>
          </div>
          <p class="mt-1 text-slate-500">
            {{ g.customer_name ? `记到「${g.customer_name}」` : '按内容自动对应客户' }} · {{ g.enabled_by }} {{ formatTime(g.enabled_at) }} 开启 ·
            已记录 {{ g.message_count }} 条<span v-if="g.pending_messages">（{{ g.pending_messages }} 条等对话结束后整理）</span>
            <span v-if="g.last_message_at"> · 最近一条 {{ formatTime(g.last_message_at) }}</span>
          </p>
          <p v-if="g.status === 'closed' && g.close_reason" class="mt-0.5 text-slate-400">{{ formatTime(g.closed_at!) }} {{ g.close_reason }}</p>
          <p v-if="silent(g)" class="mt-1 text-amber-700">
            开启超过 10 分钟还没收到消息：请管理员在飞书开放平台给应用开通“获取群组中所有消息”权限并发布新版本。
          </p>
        </li>
      </ul>
      <p v-else class="mt-3 text-slate-400">还没有群开启记录。</p>
    </div>

    <ul class="divide-y divide-slate-100" data-testid="crm-pending">
      <li v-for="a in pending" :key="a.id" class="px-4 py-3 text-xs">
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <p class="font-medium text-slate-800">{{ a.type_label }} · {{ a.title || '（无标题）' }}</p>
          <span class="text-slate-400">{{ formatTime(a.occurred_at) }}</span>
        </div>
        <p v-if="a.participants.length" class="mt-0.5 text-slate-500">
          {{ a.participants.map((p) => p.name || p.email).join('、') }}
        </p>
        <p class="mt-1 line-clamp-2 text-slate-600">{{ a.content }}</p>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <label :for="`assign-${a.id}`" class="text-slate-500">
            {{ a.match_candidates.length ? '可能是：' : '属于哪个客户：' }}
          </label>
          <select :id="`assign-${a.id}`" v-model="choice[a.id]" class="h-7 max-w-[220px] rounded border border-slate-300 px-1.5">
            <option :value="undefined" disabled>请选择客户</option>
            <option v-for="c in a.match_candidates" :key="`c-${c.customer_id}`" :value="c.customer_id">
              {{ c.name }}（相似度 {{ Math.round(c.confidence * 100) }}%）
            </option>
            <option v-for="c in otherCustomers(a)" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select>
          <button :disabled="!choice[a.id] || busy" @click="assign(a)" class="rounded bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">归属</button>
          <button :disabled="busy" @click="ignore(a)" class="rounded border border-slate-300 px-2.5 py-1 text-slate-600 hover:bg-slate-50">不是客户沟通</button>
        </div>
      </li>
      <li v-if="!pending.length" class="px-4 py-6 text-center text-xs text-slate-400">没有等待指定客户的沟通记录</li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import * as api from '../../api/crmCopilot'
import type { ChatGroup, CrmActivity, MailAccount } from '../../api/crmCopilot'
import type { CustomerDto } from '../../api/departmentCrm'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'

const props = defineProps<{ teamId: number; customers: CustomerDto[] }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const pending = ref<CrmActivity[]>([])
const choice = reactive<Record<number, number | undefined>>({})
const mailbox = ref<MailAccount | null>(null)
const mailboxOpen = ref(false)
const busy = ref(false)
const groups = ref<ChatGroup[]>([])
const groupsOpen = ref(false)
const canManageGroups = ref(false)
const activeGroups = computed(() => groups.value.filter((g) => g.status === 'active').length)
// 开启 10 分钟后还一条都没收到：多半是应用没开通“获取群组中所有消息”权限
const silent = (g: ChatGroup) => g.status === 'active' && !g.message_count && Date.now() - new Date(g.enabled_at).getTime() > 10 * 60 * 1000
const box = reactive({ imap_host: '', imap_port: 993, username: '', password: '', folder: 'CRM', enabled: true })

const formatTime = (iso: string) => new Date(iso).toLocaleString('zh-CN', {
  hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
})
const otherCustomers = (a: CrmActivity) => props.customers.filter((c) => !a.match_candidates.some((m) => m.customer_id === c.id))

async function load() {
  try {
    pending.value = (await api.listActivities(props.teamId)).items
    for (const a of pending.value) if (choice[a.id] === undefined && a.match_candidates[0]) choice[a.id] = a.match_candidates[0].customer_id
    mailbox.value = await api.getMailbox(props.teamId)
    if (mailbox.value) Object.assign(box, { ...mailbox.value, password: '' })
  } catch (e) {
    toastError(getErrorMessage(e, '加载失败'))
  }
  await loadGroups()
}

async function loadGroups() {
  try {
    const chat = await api.listChatGroups(props.teamId)
    groups.value = chat.items
    canManageGroups.value = chat.can_manage
  } catch (e) {
    toastError(getErrorMessage(e, '飞书群记录加载失败'))
  }
}

async function run<T>(fn: () => Promise<T>, fallback: string): Promise<T | undefined> {
  busy.value = true
  try {
    return await fn()
  } catch (e) {
    toastError(getErrorMessage(e, fallback))
    return undefined
  } finally {
    busy.value = false
  }
}

function describe(result: api.IngestResult) {
  if (result.duplicate) return '这封已经导入过了'
  return result.activity.customer_name ? `已归到「${result.activity.customer_name}」` : '已导入，等你指定客户'
}

async function onEmail(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  for (const file of files) {
    const result = await run(() => api.uploadEmail(props.teamId, file), `「${file.name}」导入失败`)
    if (result) toastSuccess(`${file.name}：${describe(result)}`)
  }
  await load()
  emit('changed')
}

async function onCalendar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const result = await run(() => api.uploadCalendar(props.teamId, file), '会议导入失败')
  if (result) toastSuccess(`导入 ${result.created} 个会议${result.duplicates ? `，${result.duplicates} 个已导入过` : ''}`)
  await load()
  emit('changed')
}

async function assign(a: CrmActivity) {
  const customerId = choice[a.id]
  if (!customerId) return
  const result = await run(() => api.assignActivity(props.teamId, a.id, customerId), '归属失败')
  if (!result) return
  const extra = result.rematched ? `，另有 ${result.rematched} 条也自动对上了` : ''
  toastSuccess(`已归属${result.learned.length ? '，以后同一个人的邮件会自动对上' : ''}${extra}`)
  await load()
  emit('changed')
}

async function ignore(a: CrmActivity) {
  if (await run(() => api.ignoreActivity(props.teamId, a.id), '操作失败') !== undefined) await load()
}

async function closeGroup(g: ChatGroup) {
  if (!window.confirm(`关闭「${g.chat_name}」的 CRM 记录？已收到的消息会先整理进 CRM，之后群里的聊天不再记录。`)) return
  const result = await run(() => api.closeChatGroup(props.teamId, g.id), '关闭失败')
  if (!result) return
  toastSuccess(result.saved ? `已关闭，剩下的消息整理成 ${result.saved} 条客户活动` : '已关闭')
  await load()
  emit('changed')
}

async function saveBox() {
  const saved = await run(() => api.saveMailbox(props.teamId, { ...box, password: box.password || undefined }), '保存失败')
  if (saved) {
    mailbox.value = saved
    box.password = ''
    toastSuccess('邮箱已连接')
  }
}

async function syncBox() {
  const result = await run(() => api.syncMailbox(props.teamId), '同步失败')
  if (result) toastSuccess(`读取 ${result.fetched} 封，新增 ${result.created} 条${result.duplicates ? `，${result.duplicates} 封已存在` : ''}`)
  await load()
  emit('changed')
}

async function removeBox() {
  if (await run(() => api.deleteMailbox(props.teamId), '断开失败') !== undefined) {
    mailbox.value = null
    Object.assign(box, { imap_host: '', username: '', password: '', folder: 'CRM' })
  }
}

watch(() => props.teamId, load)
onMounted(load)
defineExpose({ load })
</script>
