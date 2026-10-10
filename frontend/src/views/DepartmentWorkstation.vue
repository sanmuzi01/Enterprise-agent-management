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
        <RouterLink v-if="home?.identity.is_head || home?.identity.org_admin" to="/department/productivity" data-testid="dept-productivity-link"
          class="rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">部门提效</RouterLink>
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
      <!-- 部门助手是工作台的入口：常驻顶部，主动给出今天该处理的事；下面的业务模块是它的执行与核对界面 -->
      <DepartmentAgentHub ref="agentHub" :agent="deptAgent" :central-agent="centralAgent" :cards="home?.cards || []"
        :department-code="moduleCode" :department-name="currentDept.name"
        :is-head="!!(home?.identity.is_head || home?.identity.org_admin)" :team-id="deptStore.currentTeamId!"
        @changed="onAgentChanged" @open="openSection" @open-card="openCard" />

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

      <nav ref="sectionNav" class="flex flex-wrap gap-1 border-b border-slate-200" data-testid="dept-sections">
        <button v-for="s in sections" :key="s.value" @click="setSection(s.value)" :data-testid="`dept-section-${s.value}`"
          class="-mb-px border-b-2 px-3 py-2 text-sm"
          :class="section === s.value ? 'border-indigo-600 font-medium text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-800'">{{ s.label }}</button>
      </nav>

      <!-- 单件事交给顶部的部门助手；一段话里有几个部门的事、或者成批的材料，在这里拆分 / 批量整理 -->
      <section v-show="section === 'overview'" class="rounded-lg border border-slate-200 bg-white" data-testid="batch-tools">
        <button @click="toggleBatchTools" class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left" data-testid="batch-tools-toggle"
          :aria-expanded="batchToolsOpen">
          <span>
            <span class="block text-sm font-semibold text-slate-900">批量与跨部门办理</span>
            <span class="block text-xs text-slate-500">一段话里有几个部门的事（请假 + 报销 + 报修），或者一批报销单据、会议纪要要整理时用这里；单件事直接问上面的部门助手。</span>
          </span>
          <ChevronDown :size="16" class="shrink-0 text-slate-400 transition-transform" :class="batchToolsOpen ? 'rotate-180' : ''" />
        </button>
        <div v-show="batchToolsOpen" class="space-y-5 border-t border-slate-100 p-4">
          <OrchestrationPanel v-if="section === 'overview' && batchToolsOpen" :key="`orch-${deptStore.currentTeamId}`" :team-id="deptStore.currentTeamId!"
            :revision="businessRevision" @open-work="openWork" />
          <AutomationWorkPanel ref="workPanel" :key="deptStore.currentTeamId!" :team-id="deptStore.currentTeamId!"
            @saved="businessRevision++" @open-result="openResponsibilityPlan" />
        </div>
      </section>

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

    </div>
  </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, provide, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronDown, RefreshCcw } from 'lucide-vue-next'
import { useCurrentDepartmentStore } from '../stores/currentDepartment'
import { getDepartmentHome } from '../api/enterpriseWorkspace'
import type { DepartmentHome, HomeCard } from '../api/enterpriseWorkspace'
import { getErrorMessage } from '../utils/request'
import CrmModule from '../components/CrmModule.vue'
import DepartmentAgentHub from '../components/department/DepartmentAgentHub.vue'
import { ASK_DEPT_AGENT, FOCUS_RECORD, type FocusRequest } from '../components/department/askAgent'
import type { CardTarget } from '../utils/agentCards'
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

type Section = 'overview' | 'business' | 'collab' | 'attendance' | 'office'
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
  list.push({ value: 'collab', label: '责任协同' }, { value: 'attendance', label: '考勤' }, { value: 'office', label: '办公事务' })
  return list
})

// ---- 部门助手：入口在页面顶部，业务模块是它的执行与核对界面 ----
const agentHub = ref<InstanceType<typeof DepartmentAgentHub> | null>(null)
const sectionNav = ref<HTMLElement | null>(null)

// 助手建了草稿 / 提交了单子：刷新业务模块和概览数字
function onAgentChanged() {
  businessRevision.value++
}

// 卡片上的“在工作台中查看”：切到对应分区（必要时打开责任协同里的某个视图 / 某份计划）并滚过去。
// 该分区对当前身份不存在时（比如人事办理只给人事部门成员）依次退到办公事务、概览。
async function openSection(target: CardTarget) {
  if (target.planId != null) {
    await openResponsibilityPlan(target.planId)
  } else {
    const has = (v: string) => sections.value.some((s) => s.value === v)
    const section = (has(target.section) ? target.section : has('office') ? 'office' : 'overview') as Section
    collabTab.value = section === 'collab' ? target.tab : undefined
    setSection(section)
    if (section === 'collab' && target.tab) void nextTick(() => collabModule.value?.setTab(target.tab as never))
  }
  await nextTick()
  sectionNav.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (target.record) {
    // 分区切好后广播：正在显示、认识这种记录的模块打开详情或高亮那一行；过一会儿清掉，免得以后切回来又被打开
    const request = { ...target.record, nonce: Date.now() }
    focusRecord.value = request
    setTimeout(() => { if (focusRecord.value === request) focusRecord.value = null }, 3000)
  }
}

const focusRecord = ref<FocusRequest | null>(null)
provide(FOCUS_RECORD, focusRecord)

// 业务模块里的“让助手分析”：把记录交给顶部的助手，并滚回顶部看结果
provide(ASK_DEPT_AGENT, (prompt: string) => {
  if (!deptAgent.value?.model_configured) return
  agentHub.value?.ask(prompt)
  agentHub.value?.$el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
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

// “批量与跨部门办理”默认收起（顶部助手是主入口）；展开过的人下次还展开
const BATCH_TOOLS_KEY = 'dept_batch_tools_open'
const batchToolsOpen = ref(false)
try { batchToolsOpen.value = localStorage.getItem(BATCH_TOOLS_KEY) === '1' } catch { /* 记不住不影响使用 */ }
function toggleBatchTools() {
  batchToolsOpen.value = !batchToolsOpen.value
  try { localStorage.setItem(BATCH_TOOLS_KEY, batchToolsOpen.value ? '1' : '0') } catch { /* ignore */ }
}

const workPanel = ref<InstanceType<typeof AutomationWorkPanel> | null>(null)
// 协同办理的某一步整理好后，在下方工作成果面板里直接打开它核对
async function openWork(id: string) {
  batchToolsOpen.value = true
  await nextTick()
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
