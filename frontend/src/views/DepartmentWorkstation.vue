<template>
  <div class="p-6 max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h1 class="text-lg font-semibold text-slate-900">部门工作台</h1>
        <p class="text-xs text-slate-500 mt-0.5">
          <span v-if="currentDept">{{ currentDept.name }} · {{ currentDept.role_name }}</span>
          <span v-else>还没有加入任何部门</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-if="deptStore.departments.length > 1"
          :value="deptStore.currentTeamId ?? ''"
          @change="onSwitchDept(($event.target as HTMLSelectElement).value)"
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
    </div>

    <div v-else class="space-y-5">
      <AutomationWorkPanel :key="deptStore.currentTeamId!" :team-id="deptStore.currentTeamId!"
        @saved="businessRevision++" />
      <div :key="`${deptStore.currentTeamId}-${businessRevision}`" class="space-y-5">
      <LeaveModule :team-id="deptStore.currentTeamId!" />
      <FinanceModule :team-id="deptStore.currentTeamId!" />
      <FinanceVoucherModule v-if="moduleCode === 'finance'" :team-id="deptStore.currentTeamId!" />
      <ProcurementModule v-if="moduleCode === 'procurement'" :team-id="deptStore.currentTeamId!" />
      <CrmModule v-else-if="moduleCode === 'sales'" :team-id="deptStore.currentTeamId!" />
      <div v-else-if="!moduleCode" class="rounded-lg border border-dashed border-slate-300 bg-slate-50 py-10 text-center">
        <p class="text-sm text-slate-500">该部门还没有配置专属业务类型，可使用请假、报销等通用办公事务。</p>
      </div>
      </div>

      <!-- 部门助手 -->
      <section v-if="deptAgent" class="rounded-lg border border-slate-200 bg-white">
        <div class="border-b border-slate-200 px-4 py-3">
          <h2 class="text-sm font-semibold text-slate-900">部门助手 · {{ deptAgent.name }}</h2>
          <p class="text-xs text-slate-400 mt-0.5">{{ deptAgent.description }}</p>
          <p v-if="!deptAgent.model_configured" class="mt-1 text-xs text-amber-700">
            需要先在「设置 → 模型连接」连接 {{ deptAgent.model_name }} 模型，部门助手才能回答。
          </p>
        </div>
        <EmbeddedAgentChatPanel :agent-id="deptAgent.id" />
      </section>
      <p v-else class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-xs text-slate-500">
        部门助手尚未发布：企业管理员完成配置并发布后，会出现在这里。
      </p>
      <RouterLink v-if="centralAgent" :to="`/agents/${centralAgent.id}/chat`" data-testid="central-entry"
        class="flex items-center justify-between rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900 hover:bg-sky-100">
        <span><strong>不确定该找哪个部门？</strong>问「{{ centralAgent.name }}」，它会按问题转交给你有权使用的部门助手。</span>
        <span class="shrink-0 text-xs text-sky-700">去提问 →</span>
      </RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { useCurrentDepartmentStore } from '../stores/currentDepartment'
import { getErrorMessage } from '../utils/request'
import CrmModule from '../components/CrmModule.vue'
import EmbeddedAgentChatPanel from '../components/EmbeddedAgentChatPanel.vue'
import FinanceModule from '../components/FinanceModule.vue'
import FinanceVoucherModule from '../components/FinanceVoucherModule.vue'
import LeaveModule from '../components/LeaveModule.vue'
import AutomationWorkPanel from '../components/AutomationWorkPanel.vue'
import ProcurementModule from '../components/ProcurementModule.vue'

const deptStore = useCurrentDepartmentStore()

const loading = ref(true)
const businessRevision = ref(0)
const errorMsg = ref('')

const currentDept = computed(() => deptStore.currentDepartment)
const centralAgent = computed(() => deptStore.workspace?.agents.find((a) => a.agent_type === 'central') || null)
const deptAgent = computed(() => {
  const teamId = deptStore.currentTeamId
  if (teamId == null) return null
  return deptStore.workspace?.agents.find((a) => a.agent_type === 'department' && a.team_id === teamId) || null
})
// 业务模块显示与否是部门自己的属性（Team.department_code），跟"有没有已发布
// 的部门 Agent"是两个独立的可用性判断——没有发布 Agent 时业务表单仍然可用，
// 只是下方"部门助手"聊天区块不出现（那个区块才依赖 deptAgent）。
const moduleCode = computed(() => currentDept.value?.department_code ?? null)

async function reload(force = false) {
  loading.value = true
  errorMsg.value = ''
  try {
    await deptStore.load(force)
  } catch (e) {
    errorMsg.value = getErrorMessage(e, '加载部门工作台失败')
  } finally {
    loading.value = false
  }
}

function onSwitchDept(value: string) {
  deptStore.selectTeam(value ? Number(value) : null)
}

onMounted(() => reload())
</script>
