<template>
  <div ref="root" class="relative">
    <button ref="button" type="button" class="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800"
      :aria-label="store.unread ? `通知（${store.unread} 条未读）` : '通知'" @click="toggle">
      <Bell :size="16" :stroke-width="1.7" />
      <span v-if="store.unread" class="absolute -right-0.5 -top-0.5 min-w-[16px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold leading-4 text-white">
        {{ store.unread > 99 ? '99+' : store.unread }}
      </span>
    </button>
    <!-- 挂到 body 上固定定位：侧栏有滚动裁剪，放在按钮内部会被截断。 -->
    <Teleport to="body">
    <div v-if="open" ref="panel" class="fixed z-[60] w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-slate-200 bg-white shadow-lg"
      :style="panelStyle" role="dialog" aria-label="通知">
      <div class="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span class="text-sm font-semibold text-slate-800">通知</span>
        <button v-if="store.unread" type="button" class="text-xs text-indigo-600" @click="readAll">全部标为已读</button>
      </div>
      <p v-if="loading" class="px-3 py-6 text-center text-xs text-slate-400">加载中…</p>
      <p v-else-if="!items.length" class="px-3 py-6 text-center text-xs text-slate-400">暂无通知</p>
      <ul v-else class="max-h-96 divide-y divide-slate-100 overflow-y-auto">
        <li v-for="n in items" :key="n.id">
          <button type="button" class="w-full px-3 py-2 text-left hover:bg-slate-50" @click="openItem(n)">
            <p class="text-[11px] text-slate-400">{{ n.category_label }} · {{ new Date(n.created_at).toLocaleString('zh-CN') }}</p>
            <p class="text-sm" :class="n.read ? 'text-slate-500' : 'font-medium text-slate-900'">{{ n.title }}</p>
            <p v-if="n.body" class="mt-0.5 line-clamp-2 text-xs text-slate-500">{{ n.body }}</p>
          </button>
        </li>
      </ul>
      <RouterLink to="/todos" class="block border-t border-slate-100 px-3 py-2 text-center text-xs text-indigo-600" @click="open = false">查看我的待办</RouterLink>
    </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell } from 'lucide-vue-next'
import * as api from '../api/workCenter'
import { useWorkCenterStore } from '../stores/workCenter'

const props = withDefaults(defineProps<{ placement?: 'down' | 'up' }>(), { placement: 'down' })
const store = useWorkCenterStore()
const router = useRouter()
const open = ref(false), loading = ref(false)
const items = ref<api.AppNotification[]>([])
const root = ref<HTMLElement | null>(null)
const button = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const panelStyle = ref<Record<string, string>>({})

function place() {
  const rect = button.value?.getBoundingClientRect()
  if (!rect) return
  const width = Math.min(320, window.innerWidth - 32)
  const left = Math.max(16, Math.min(rect.left, window.innerWidth - width - 16))
  panelStyle.value = props.placement === 'up'
    ? { left: `${left}px`, bottom: `${window.innerHeight - rect.top + 8}px` }
    : { left: `${Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16))}px`, top: `${rect.bottom + 8}px` }
}

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  place()
  loading.value = true
  try { const data = await api.listNotifications(); items.value = data.items; store.unread = data.unread }
  catch { items.value = [] }
  finally { loading.value = false }
}
async function readAll() {
  store.unread = (await api.markNotificationsRead()).unread
  items.value = items.value.map(n => ({ ...n, read: true }))
}
async function openItem(n: api.AppNotification) {
  if (!n.read) { store.unread = (await api.markNotificationsRead([n.id])).unread; n.read = true }
  open.value = false
  if (n.link) router.push(n.link)
}
const onDocClick = (e: MouseEvent) => {
  const target = e.target as Node
  if (open.value && !root.value?.contains(target) && !panel.value?.contains(target)) open.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>
