<template>
  <div class="app-canvas h-dvh min-w-0">
    <div class="flex h-full min-w-0">
      <Transition name="fade">
        <div v-if="navOpen" class="fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] md:hidden" @click="navOpen = false"></div>
      </Transition>
      <aside
        :class="[
          'ui-glass fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] shrink-0 flex-col border-r px-2.5 pb-4 pt-[max(1rem,env(safe-area-inset-top))] transition-transform duration-500 ease-[var(--ease)] md:static md:z-auto md:w-62 md:max-w-none md:translate-x-0 md:py-4',
          navOpen ? 'translate-x-0' : '-translate-x-full',
        ]"
      >
        <div class="mb-3 flex items-center gap-2.5 px-2">
          <span class="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-gradient-to-b from-[#3d3d40] to-[#0e0e10] text-white shadow-[inset_0_1px_0_rgba(255,255,255,.26),0_2px_6px_rgba(0,0,0,.25)]">
            <ShieldCheck :size="15" :stroke-width="1.9" />
          </span>
          <div class="min-w-0">
            <p class="truncate text-[14px] font-semibold tracking-[-0.012em]">管理后台</p>
            <p class="truncate text-[12px] text-slate-500">{{ userStore.user?.name || '管理员' }}</p>
          </div>
        </div>

        <nav class="flex-1 space-y-3 overflow-y-auto">
          <div v-for="group in navGroups" :key="group.label" class="space-y-0.5">
            <p v-if="group.label" class="px-2.5 pb-0.5 pt-1 text-[11px] font-medium tracking-wide text-slate-400">{{ group.label }}</p>
            <RouterLink
              v-for="item in group.items"
              :key="item.path"
              :to="item.path"
              :class="navClass(isActive(item.path))"
            >
              <component :is="item.icon" :size="18" :stroke-width="1.7" :class="isActive(item.path) ? 'text-[var(--accent)]' : 'text-slate-500'" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </div>
        </nav>

        <div class="border-t border-black/[.08] pt-2">
          <button
            @click="logout"
            class="flex h-9 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-[14px] font-medium text-slate-600 hover:bg-black/[.045] hover:text-slate-900"
          >
            <LogOut :size="18" :stroke-width="1.7" class="text-slate-500" />
            <span>退出登录</span>
          </button>
        </div>
        <IcpFooter class="mt-3 px-2" />
      </aside>

      <!-- 主体 -->
      <div class="flex min-w-0 flex-1 flex-col">
        <header class="ui-glass relative z-10 flex h-[calc(3.5rem+env(safe-area-inset-top))] shrink-0 items-center justify-between border-b px-3 pt-[env(safe-area-inset-top)] md:h-14 md:px-6 md:pt-0 lg:px-10">
          <div class="flex min-w-0 items-center gap-1.5">
            <button
              type="button"
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-slate-700 hover:bg-black/[.06] md:hidden"
              aria-label="打开导航"
              @click="navOpen = true"
            >
              <Menu :size="20" :stroke-width="1.8" />
            </button>
            <h1 class="truncate text-[17px] font-semibold tracking-[-0.018em]">{{ currentTitle }}</h1>
          </div>
          <div class="flex items-center gap-2.5 text-[13px] text-slate-500">
            <span class="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">管理员</span>
            <span class="hidden sm:inline">{{ userStore.user?.name }}</span>
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>

        <main class="min-h-0 flex-1 overflow-y-auto">
          <RouterView v-slot="{ Component, route: r }">
            <Transition name="page" mode="out-in">
              <component :is="Component" :key="String(r.name ?? r.path)" />
            </Transition>
          </RouterView>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, RouterLink, RouterView } from 'vue-router'
import {
  LayoutDashboard, Users, ListChecks, BarChart3, ScrollText,
  Stethoscope, ShieldCheck, LogOut, Library, Wallet, Menu, Zap, Building2, KeyRound,
  Bug, Bot, MessagesSquare, Receipt, TrendingUp,
} from 'lucide-vue-next'
import { useUserStore } from '../../stores/user'
import ThemeToggle from '../../components/ThemeToggle.vue'
import NotificationBell from '../../components/NotificationBell.vue'
import { useWorkCenterStore } from '../../stores/workCenter'
import IcpFooter from '../../components/IcpFooter.vue'

const route = useRoute()
const userStore = useUserStore()
const workCenter = useWorkCenterStore()
onMounted(() => workCenter.startPolling())
onBeforeUnmount(() => workCenter.stopPolling())

const navOpen = ref(false)
watch(() => route.fullPath, () => { navOpen.value = false })

const navGroups = [
  { label: '', items: [
    { path: '/admin/overview', label: '系统概览', icon: LayoutDashboard },
  ] },
  { label: '人员与组织', items: [
    { path: '/admin/users', label: '用户管理', icon: Users },
    { path: '/admin/organization', label: '组织架构', icon: Building2 },
    { path: '/admin/integrations', label: '飞书 / 钉钉接入', icon: MessagesSquare },
  ] },
  { label: 'AI 能力', items: [
    { path: '/admin/agents', label: '企业智能体', icon: Bot },
    { path: '/admin/knowledge-spaces', label: '企业知识库', icon: Library },
    { path: '/admin/llm', label: '模型连接', icon: KeyRound },
    { path: '/admin/skills', label: '技能管理', icon: Zap },
  ] },
  { label: '运营', items: [
    { path: '/admin/usage', label: '使用情况', icon: BarChart3 },
    { path: '/admin/productivity', label: '提效统计', icon: TrendingUp },
    { path: '/admin/plans', label: '套餐配额', icon: Wallet },
    { path: '/admin/expense-policies', label: '费用标准', icon: Receipt },
    { path: '/admin/tasks', label: '后台任务', icon: ListChecks },
  ] },
  { label: '运维', items: [
    { path: '/admin/logs', label: '操作日志', icon: ScrollText },
    { path: '/admin/issues', label: '问题中心', icon: Bug },
    { path: '/admin/diagnose', label: '系统诊断', icon: Stethoscope },
  ] },
]
const navItems = navGroups.flatMap(g => g.items)

const currentTitle = computed(() => {
  const item = navItems.find(i => route.path === i.path)
  if (item) return item.label
  if (route.path === '/admin') return '系统概览'
  return '管理后台'
})

const isActive = (path: string) => route.path === path || (path === '/admin/overview' && route.path === '/admin')

const navClass = (active: boolean) => [
  'flex h-9 items-center gap-2.5 rounded-[10px] px-2.5 text-[14px] font-medium tracking-[-0.006em] transition-colors',
  active ? 'bg-black/[.07] text-slate-900' : 'text-slate-900 hover:bg-black/[.045]',
]

const logout = () => {
  userStore.logout()
}
</script>
