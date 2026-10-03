<template>
  <div class="app-canvas h-dvh min-w-0">
    <div class="flex h-full min-w-0 flex-col md:flex-row">
      <!-- 手机：顶栏 + 抽屉式导航 -->
      <header class="ui-glass flex h-[calc(3rem+env(safe-area-inset-top))] shrink-0 items-center gap-2 border-b px-3 pt-[env(safe-area-inset-top)] md:hidden">
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-full text-slate-700 hover:bg-black/[.06]"
          aria-label="打开导航"
          @click="mobileOpen = true"
        >
          <Menu :size="20" :stroke-width="1.8" />
        </button>
        <p class="min-w-0 flex-1 truncate text-[16px] font-semibold tracking-[-0.016em]">{{ mobileTitle }}</p>
        <NotificationBell />
        <ThemeToggle />
      </header>
      <Transition name="fade">
        <div v-if="mobileOpen" class="fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] md:hidden" @click="mobileOpen = false"></div>
      </Transition>

      <aside
        :class="[
          'ui-glass fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] shrink-0 flex-col overflow-y-auto border-r px-2.5 pb-4 pt-[max(1rem,env(safe-area-inset-top))] transition-[width,transform] duration-500 ease-[var(--ease)] md:static md:z-auto md:max-w-none md:translate-x-0 md:py-4',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          rail ? 'md:w-16' : 'md:w-62',
        ]"
      >
        <div :class="['mb-3 flex items-center gap-2.5', rail ? 'justify-center' : 'px-2']">
          <span class="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-gradient-to-b from-[#3d3d40] to-[#0e0e10] text-white shadow-[inset_0_1px_0_rgba(255,255,255,.26),0_2px_6px_rgba(0,0,0,.25)]">
            <Sparkles :size="15" :stroke-width="1.9" />
          </span>
          <p v-if="!rail" class="truncate text-[14px] font-semibold tracking-[-0.012em]">AI 助手工作台</p>
        </div>

        <nav class="space-y-0.5">
          <RouterLink
            v-for="item in primaryItems"
            :key="item.path"
            :to="item.path"
            :title="item.label"
            :class="navClass(item.active)"
          >
            <component :is="item.icon" :size="18" :stroke-width="1.7" :class="iconClass(item.active)" />
            <span v-if="!rail" class="truncate">{{ item.label }}</span>
            <span v-if="item.badge" :class="['rounded-full bg-red-500 px-1.5 text-[10px] font-semibold leading-4 text-white', rail ? 'absolute ml-5 -mt-5' : 'ml-auto']"
              :aria-label="`${item.badge} 项逾期或今天到期`">{{ item.badge > 99 ? '99+' : item.badge }}</span>
          </RouterLink>
        </nav>

        <p v-if="!rail" class="px-2.5 pb-1.5 pt-5 text-[11px] font-semibold tracking-wide text-slate-400">当前助手</p>
        <div v-else class="my-2 h-px bg-black/[.08]"></div>
        <RouterLink
          :to="agentSpace.disabled ? route.fullPath : agentSpace.path"
          :title="agentSpace.label"
          :class="navClass(agentSpace.active, agentSpace.disabled)"
        >
          <MessageSquare :size="18" :stroke-width="1.7" :class="iconClass(agentSpace.active)" />
          <span v-if="!rail" class="truncate">{{ agentSpace.label }}</span>
        </RouterLink>
        <p v-if="agentSpace.disabled && !rail" class="px-2.5 pt-1 text-[11px] text-slate-400">先在工作台选一个助手</p>

        <template v-if="!rail">
          <button
            type="button"
            @click="toggleMore"
            class="mt-3 flex h-8 w-full items-center gap-1.5 rounded-lg px-2.5 text-[11px] font-semibold tracking-wide text-slate-400 hover:bg-black/[.045]"
          >
            <ChevronRight :size="13" :class="['transition-transform duration-300', moreOpen ? 'rotate-90' : '']" />
            <span>更多</span>
            <span v-if="!moreOpen && moreHasActive" class="ml-auto h-1.5 w-1.5 rounded-full bg-[var(--accent)]"></span>
          </button>
          <div :class="['grid transition-[grid-template-rows] duration-500 ease-[var(--ease)]', moreOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]']">
            <nav class="min-h-0 space-y-0.5 overflow-hidden">
              <RouterLink v-for="item in moreItems" :key="item.path" :to="item.path" :class="navClass(item.active)">
                <component :is="item.icon" :size="18" :stroke-width="1.7" :class="iconClass(item.active)" />
                <span class="truncate">{{ item.label }}</span>
              </RouterLink>
              <button type="button" :class="navClass(false)" class="w-full" @click="openGuide">
                <Compass :size="18" :stroke-width="1.7" class="text-slate-500" />
                <span class="truncate">新手指引</span>
              </button>
            </nav>
          </div>
        </template>
        <nav v-else class="mt-2 space-y-0.5">
          <RouterLink v-for="item in moreItems" :key="item.path" :to="item.path" :title="item.label" :class="navClass(item.active)">
            <component :is="item.icon" :size="18" :stroke-width="1.7" :class="iconClass(item.active)" />
          </RouterLink>
        </nav>

        <div v-if="rail" class="mt-auto flex flex-col items-center gap-1 pb-2">
          <NotificationBell placement="up" />
          <ThemeToggle />
        </div>
        <div :class="[rail ? 'mt-1' : 'mt-auto', 'flex items-center gap-2.5 border-t border-black/[.08] pt-3', rail ? 'justify-center' : 'px-2']">
          <span
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-[#b9b9c0] to-[#86868c] text-[13px] font-semibold text-white"
            :title="userStore.user?.name || '当前用户'"
          >
            {{ userInitial }}
          </span>
          <div v-if="!rail" class="min-w-0 flex-1">
            <p class="truncate text-[13px] font-semibold tracking-[-0.008em]">{{ userStore.user?.name || '当前用户' }}</p>
            <p class="text-[12px] text-slate-500">个人账号</p>
          </div>
          <NotificationBell v-if="!rail" placement="up" />
          <ThemeToggle v-if="!rail" />
          <button
            v-if="!rail"
            @click="userStore.logout"
            class="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-black/[.06] hover:text-slate-900"
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut :size="16" :stroke-width="1.7" />
          </button>
        </div>
        <IcpFooter v-if="!rail" class="mt-3 px-2" />
      </aside>

      <main class="min-h-0 min-w-0 flex-1 overflow-hidden">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Building2, ChevronRight, Compass, Layers3, LayoutGrid, Library, ListChecks, ListTodo, LogOut, Menu, MessageSquare, Settings, Sparkles, Workflow, Zap } from 'lucide-vue-next'
import { useUserStore } from '../stores/user'
import { useAgentSessionStore } from '../stores/agentSession'
import ThemeToggle from './ThemeToggle.vue'
import NotificationBell from './NotificationBell.vue'
import { useWorkCenterStore } from '../stores/workCenter'
import IcpFooter from './IcpFooter.vue'
import { useOnboardingStore } from '../stores/onboarding'

const route = useRoute()
const userStore = useUserStore()
const agentSession = useAgentSessionStore()
const onboarding = useOnboardingStore()
const workCenter = useWorkCenterStore()
onMounted(() => workCenter.startPolling())
onBeforeUnmount(() => workCenter.stopPolling())
watch(() => route.fullPath, () => { void workCenter.refresh() })

const openGuide = () => {
  mobileOpen.value = false
  void onboarding.restart()
}

const routeAgentId = computed(() => {
  const value = route.params.agentId
  if (Array.isArray(value)) return Number(value[0])
  return value ? Number(value) : null
})

const activeAgentId = computed(() => {
  return routeAgentId.value || agentSession.selectedAgent?.id || userStore.user?.selected_agent_id || null
})

const primaryItems = computed(() => [
  { label: '工作台', path: '/agents', icon: Layers3, active: route.path === '/agents', badge: 0 },
  { label: '部门工作台', path: '/department', icon: Building2, active: route.path.startsWith('/department'), badge: 0 },
  { label: '我的待办', path: '/todos', icon: ListTodo, active: route.path.startsWith('/todos'), badge: workCenter.urgent },
  { label: '知识库中心', path: '/knowledge-spaces', icon: Library, active: route.path.startsWith('/knowledge-spaces'), badge: 0 },
])

const moreItems = computed(() => [
  { label: '小窗口与监控', path: '/widgets', icon: LayoutGrid, active: route.path.startsWith('/widgets') || route.path.startsWith('/web-monitor') },
  { label: '技能中心', path: '/skills', icon: Zap, active: route.path.startsWith('/skills') },
  { label: 'Agent 流水线', path: '/pipelines', icon: Workflow, active: route.path.startsWith('/pipelines') },
  { label: '任务中心', path: '/tasks', icon: ListChecks, active: route.path.startsWith('/tasks') },
  { label: '设置', path: '/settings', icon: Settings, active: route.path.startsWith('/settings') || route.path.startsWith('/llm-configs') },
])

const mobileTitle = computed(() => {
  const hit = [...primaryItems.value, ...moreItems.value].find((i) => i.active)
  if (hit) return hit.label
  if (agentSpace.value.active) return '助手空间'
  return 'AI 助手工作台'
})

const moreHasActive = computed(() => moreItems.value.some((i) => i.active))

// 对话页自带会话栏，全局侧栏收成图标栏，把横向空间让给正文
const mobileOpen = ref(false)
const desktopQuery = window.matchMedia('(min-width: 768px)')
const isDesktop = ref(desktopQuery.matches)
const onDesktopChange = (e: MediaQueryListEvent) => { isDesktop.value = e.matches; if (e.matches) mobileOpen.value = false }
desktopQuery.addEventListener('change', onDesktopChange)
onBeforeUnmount(() => desktopQuery.removeEventListener('change', onDesktopChange))
watch(() => route.fullPath, () => { mobileOpen.value = false })

// 仅桌面宽度收成图标栏；手机上抽屉永远展开完整导航
const rail = computed(() => isDesktop.value && /^\/agents\/\d+\/chat/.test(route.path))
const userInitial = computed(() => (userStore.user?.name || '我').trim().charAt(0).toUpperCase())

const MORE_KEY = 'appshell.moreOpen'
const moreOpen = ref((() => {
  try { return localStorage.getItem(MORE_KEY) === '1' } catch { return false }
})())
const toggleMore = () => {
  moreOpen.value = !moreOpen.value
  try { localStorage.setItem(MORE_KEY, moreOpen.value ? '1' : '0') } catch { /* ignore */ }
}
// 当前在「更多」里的页面时自动展开，避免看不到高亮项
watch(moreHasActive, (v) => { if (v) moreOpen.value = true }, { immediate: true })

const agentSpace = computed(() => {
  const agentId = activeAgentId.value
  const inSpace = agentId != null && route.path.startsWith(`/agents/${agentId}/`)
  return {
    label: inSpace || agentId != null
      ? `助手空间${agentSession.selectedAgent?.name ? ' · ' + agentSession.selectedAgent.name : ''}`
      : '助手空间',
    path: agentId != null ? `/agents/${agentId}/chat` : route.fullPath,
    active: inSpace,
    disabled: agentId == null,
  }
})

const navClass = (active: boolean, disabled = false) => [
  'flex h-9 items-center rounded-[10px] text-[14px] font-medium tracking-[-0.006em] transition-colors',
  rail.value ? 'justify-center' : 'gap-2.5 px-2.5',
  active ? 'bg-black/[.07] text-slate-900' : 'text-slate-900 hover:bg-black/[.045]',
  disabled ? 'pointer-events-none cursor-not-allowed opacity-40' : '',
]

const iconClass = (active: boolean) => (active ? 'text-[var(--accent)]' : 'text-slate-500')

const loadSelectedAgent = async () => {
  try {
    if (userStore.user?.is_admin || route.path.startsWith('/admin')) {
      agentSession.remember(null)
      return
    }
    await agentSession.loadSelected()
  } catch {
    agentSession.remember(null)
  }
}

onMounted(loadSelectedAgent)
watch(() => userStore.user?.selected_agent_id, () => loadSelectedAgent())
</script>
