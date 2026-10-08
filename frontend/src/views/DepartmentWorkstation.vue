<template>
  <div class="h-screen overflow-y-auto">
    <!-- 外壳不允许滚动，每个页面自己负责滚动。注意：根元素前面不能写注释，会让页面切换动画卡住（页面空白） -->
  <div class="p-6 max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h1 class="text-lg font-semibold text-slate-900">部门工作台</h1>
        <p class="text-xs text-slate-500 mt-0.5">
          <span v-if="currentDept">{{ currentDept.name }} · {{ home?.department.department_label || '未设置业务类型' }} · {{ currentDept.role_name }}<span v-if="home?.identity.org_admin"> · 企业管理员</span></span>
          <span v-else>还没有加入任何部门</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-if="deptStore.departments.length > 1"
          :value="deptStore.currentTeamId ?? ''"
          @change="onSwitchDept(($event.target as HTMLSelectElement).value)"
          aria-label="切换部门"
          class="rounded border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700"
        >
          <option v-for="d in deptStore.departments" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
        <button
          @click="reload(true)"
          :disabled="loading"
          class="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
        >
          <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <div v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</div>

    <div v-else-if="!currentDept" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 py-16 text-center">
      <p class="text-sm text-slate-500">你还不属于任何部门，请联系企业管理员分配部门。</p>
      <RouterLink to="/agents" class="mt-2 inline-block text-sm text-indigo-600 hover:text-indigo-700">先去个人工作台 →</RouterLink>
    </div>

    <div v-else class="space-y-5">
      <!-- 概览：按部门业务和我的身份汇总，点卡片直达对应工作区 -->
      <div v-if="home" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="home-cards">
        <button v-for="c in home.cards" :key="c.key" @click="openCard(c)" :data-testid="`home-card-${c.key}`"
          class="rounded-lg border bg-white px-3 py-3 text-left transition hover:border-indigo-300 hover:shadow-sm"
          :class="c.tone === 'danger' ? 'border-red-200' : c.tone === 'warn' ? 'border-amber-200' : 'border-slate-200'">
          <p class="text-xs text-slate-500">{{ c.label }}</p>
          <p class="mt-1 text-xl font-semibold tabular-nums"
            :class="c.tone === 'danger' ? 'text-red-600' : c.tone === 'warn' ? 'text-amber-600' : 'text-slate-900'">{{ c.value }}</p>
          <p class="mt-0.5 truncate text-xs text-slate-400" :title="c.hint">{{ c.hint }}</p>
        </button>
      </div>

      <nav class="flex flex-wrap gap-1 border-b border-slate-200" data-testid="dept-sections">
        <button v-for="s in sections" :key="s.value" @click="setSection(s.value)" :data-testid="`dept-section-${s.value}`"
          class="-mb-px border-b-2 px-3 py-2 text-sm"
          :class="section === s.value ? 'border-indigo-600 font-medium text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-800'">{{ s.label }}</button>
      </nav>

      <OrchestrationPanel v-if="section === 'overview'" :key="`orch-${deptStore.currentTeamId}`" :team-id="deptStore.currentTeamId!"
        :revision="businessRevision" @open-work="openWork" />
      <AutomationWorkPanel ref="workPanel" v-show="section === 'overview'" :key="deptStore.currentTeamId!" :team-id="deptStore.currentTeamId!"
        @saved="businessRevision++" @open-result="openResponsibilityPlan" />

      <ResponsibilityModule v-if="section === 'collab'" ref="collabModule" :key="`collab-${deptStore.currentTeamId}`"
        :team-id="deptStore.currentTeamId!" :initial-tab="collabTab" />

      <AttendanceModule v-if="section === 'attendance'" :key="`attendance-${deptStore.currentTeamId}`" :team-id="deptStore.currentTeamId!" @changed="businessRevision++" />

      <div :key="`${deptStore.currentTeamId}-${businessRevision}`" class="space-y-5">
        <template v-if="section === 'business'">
          <FinanceVoucherModule v-if="moduleCode === 'finance'" :team-id="deptStore.currentTeamId!" />
          <ItDeskModule v-else-if="moduleCode === 'it'" :team-id="deptStore.currentTeamId!" />
          <HrCaseModule v-else-if="moduleCode === 'hr'" :team-id="deptStore.currentTeamId!" />
          <CrmModule v-else-if="moduleCode === 'sales'" :team-id="deptStore.currentTeamId!" />
          <ProcurementModule v-else-if="moduleCode === 'procurement'" :team-id="deptStore.currentTeamId!" />
        </template>
        <template v-if="section === 'office'">
          <LeaveModule :team-id="deptStore.currentTeamId!" />
          <FinanceModule :team-id="deptStore.currentTeamId!" />
          <TicketModule :team-id="deptStore.currentTeamId!" />
          <!-- 人事部门成员在“人事办理”区处理；其他部门在这里看分给自己的入转调离任务 -->
          <HrCaseModule v-if="!(hasBusiness && moduleCode === 'hr')" :team-id="deptStore.currentTeamId!" />
        </template>
      </div>

      <template v-if="section === 'assistant'">
        <section v-if="deptAgent" class="rounded-lg border border-slate-200 bg-white">
          <div class="border-b border-slate-200 px-4 py-3">
            <h2 class="text-sm font-semibold text-slate-900">部门助手 · {{ deptAgent.name }}</h2>
            <p class="text-xs text-slate-400 mt-0.5">{{ deptAgent.description }}</p>
            <p v-if="!deptAgent.model_configured" class="mt-1 text-xs text-amber-700">
              需要先在「设置 → 模型连接」连接 {{ modelDisplayName(deptAgent.model_name) }} 模型，部门助手才能回答。
            </p>
          </div>
          <EmbeddedAgentChatPanel :agent-id="deptAgent.id" />
        </section>
        <p v-else class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-xs text-slate-500">
          部门助手尚未发布：企业管理员完成配置并发布后，会出现在这里。
        </p>
      </template>
      <RouterLink v-if="centralAgent && (section === 'assistant' || section === 'overview')" :to="`/agents/${centralAgent.id}/chat`" data-testid="central-entry"
        class="flex items-center justify-between rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900 hover:bg-sky-100">
        <span><strong>不确定该找哪个部门？</strong>问「{{ centralAgent.name }}」，它会按问题转交给你有权使用的部门助手。</span>
        <span class="shrink-0 text-xs text-sky-700">去提问 →</span>
      </RouterLink>
    </div>
  </div>
  </div>
</template>

<script setup lang="ts">
import { modelDisplayName } from '../utils/displayNames'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshCcw } from 'lucide-vue-next'
import { useCurrentDepartmentStore } from '../stores/currentDepartment'
import { getDepartmentHome } from '../api/enterpriseWorkspace'
import type { DepartmentHome, HomeCard } from '../api/enterpriseWorkspace'
import { getErrorMessage } from '../utils/request'
import CrmModule from '../components/CrmModule.vue'
import EmbeddedAgentChatPanel from '../components/EmbeddedAgentChatPanel.vue'
import FinanceModule from '../components/FinanceModule.vue'
import FinanceVoucherModule from '../components/FinanceVoucherModule.vue'
import HrCaseModule from '../components/HrCaseModule.vue'
import ItDeskModule from '../components/ItDeskModule.vue'
import TicketModule from '../components/TicketModule.vue'
import LeaveModule from '../components/LeaveModule.vue'
import AutomationWorkPanel from '../components/AutomationWorkPanel.vue'
import OrchestrationPanel from '../components/OrchestrationPanel.vue'
import ProcurementModule from '../components/ProcurementModule.vue'
import ResponsibilityModule from '../components/ResponsibilityModule.vue'
import AttendanceModule from '../components/AttendanceModule.vue'

const deptStore = useCurrentDepartmentStore()
const router = useRouter()

const loading = ref(true)
const businessRevision = ref(0)
const errorMsg = ref('')
const home = ref<DepartmentHome | null>(null)

type Section = 'overview' | 'business' | 'collab' | 'attendance' | 'office' | 'assistant'
const section = ref<Section>('overview')

const currentDept = computed(() => deptStore.currentDepartment)
const centralAgent = computed(() => deptStore.workspace?.agents.find((a) => a.agent_type === 'central') || null)
const deptAgent = computed(() => {
  const teamId = deptStore.currentTeamId
  if (teamId == null) return null
  return deptStore.workspace?.agents.find((a) => a.agent_type === 'department' && a.team_id === teamId) || null
})
// 业务模块显示与否是部门自己的属性（Team.department_code），跟"有没有已发布
// 的部门 Agent"是两个独立的可用性判断——没有发布 Agent 时业务表单仍然可用。
const moduleCode = computed(() => currentDept.value?.department_code ?? null)
// 专业业务区随部门业务类型和我的身份变化：后端只在有权限时给出 business_label（人事办理只给人事部门成员）
const hasBusiness = computed(() => !!home.value?.business_label)
const sections = computed(() => {
  const list: { value: Section; label: string }[] = [{ value: 'overview', label: '概览' }]
  if (home.value?.business_label) list.push({ value: 'business', label: home.value.business_label })
  list.push({ value: 'collab', label: '责任协同' }, { value: 'attendance', label: '考勤' }, { value: 'office', label: '办公事务' }, { value: 'assistant', label: '部门助手' })
  return list
})
const sectionKey = (teamId: number) => `dept_section_${teamId}`

function setSection(value: Section) {
  section.value = value
  try {
    if (deptStore.currentTeamId != null) localStorage.setItem(sectionKey(deptStore.currentTeamId), value)
  } catch { /* 记不住上次打开的分区不影响使用 */ }
}

const collabTab = ref<string | undefined>(undefined)
const collabModule = ref<InstanceType<typeof ResponsibilityModule> | null>(null)

function openCard(card: HomeCard) {
  if (card.section === 'todos') router.push('/todos')
  else {
    collabTab.value = card.section === 'collab' ? card.tab : undefined
    setSection(card.section as Section)
    if (card.section === 'collab' && card.tab) void nextTick(() => collabModule.value?.setTab(card.tab as never))
  }
}

// 在概览里整理出责任计划草稿后，直接去责任协同里补全并发布
async function openResponsibilityPlan(planId: number) {
  collabTab.value = 'plans'
  setSection('collab')
  await nextTick()
  await nextTick()
  collabModule.value?.openPlan(planId)
}

async function loadHome() {
  const teamId = deptStore.currentTeamId
  if (teamId == null) {
    home.value = null
    return
  }
  try {
    home.value = await getDepartmentHome(teamId)
  } catch (e) {
    home.value = null
    errorMsg.value = getErrorMessage(e, '加载部门概览失败')
  }
  let saved: string | null = null
  try { saved = localStorage.getItem(sectionKey(teamId)) } catch { saved = null }
  section.value = sections.value.some((s) => s.value === saved) ? (saved as Section) : 'overview'
}

async function reload(force = false) {
  loading.value = true
  errorMsg.value = ''
  try {
    await deptStore.load(force)
    await loadHome()
  } catch (e) {
    errorMsg.value = getErrorMessage(e, '加载部门工作台失败')
  } finally {
    loading.value = false
  }
}

const workPanel = ref<InstanceType<typeof AutomationWorkPanel> | null>(null)
// 协同办理的某一步整理好后，在下方工作成果面板里直接打开它核对
async function openWork(id: string) {
  await workPanel.value?.refresh()
  await workPanel.value?.open(id)
  document.querySelector('[data-testid="automation-panel"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onSwitchDept(value: string) {
  deptStore.selectTeam(value ? Number(value) : null)
}

watch(() => deptStore.currentTeamId, (value, old) => {
  if (value !== old && !loading.value) {
    errorMsg.value = ''
    void loadHome()
  }
})
// AI 整理保存为业务草稿后，概览数字也跟着刷新
watch(businessRevision, () => { void loadHome() })

onMounted(() => reload())
</script>
