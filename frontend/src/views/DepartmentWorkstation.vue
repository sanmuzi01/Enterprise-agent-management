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
      <LeaveModule v-if="moduleCode === 'hr'" :team-id="deptStore.currentTeamId!" />
      <ProcurementModule v-else-if="moduleCode === 'procurement'" :team-id="deptStore.currentTeamId!" />
      <CrmModule v-else-if="moduleCode === 'sales'" :team-id="deptStore.currentTeamId!" />
      <div v-else class="rounded-lg border border-dashed border-slate-300 bg-slate-50 py-10 text-center">
        <p class="text-sm text-slate-500">该部门暂无可用的业务模块。</p>
      </div>

      <!-- 部门助手 -->
      <section v-if="deptAgent" class="rounded-lg border border-slate-200 bg-white">
        <div class="border-b border-slate-200 px-4 py-3">
          <h2 class="text-sm font-semibold text-slate-900">部门助手 · {{ deptAgent.name }}</h2>
          <p class="text-xs text-slate-400 mt-0.5">{{ deptAgent.description }}</p>
        </div>
        <EmbeddedAgentChatPanel :agent-id="deptAgent.id" />
      </section>
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
import LeaveModule from '../components/LeaveModule.vue'
import ProcurementModule from '../components/ProcurementModule.vue'

const deptStore = useCurrentDepartmentStore()

const loading = ref(true)
const errorMsg = ref('')

const currentDept = computed(() => deptStore.currentDepartment)
const deptAgent = computed(() => {
  const teamId = deptStore.currentTeamId
  if (teamId == null) return null
  return deptStore.workspace?.agents.find((a) => a.agent_type === 'department' && a.team_id === teamId) || null
})
// 业务模块靠哪个部门 Agent 已发布来判断——department_code 只存在于 Agent 上，
// 部门（Team）本身没有这个字段。没有已发布部门 Agent 的部门自然落进"暂无
// 业务模块"的兜底分支，不当错误处理。
const moduleCode = computed(() => deptAgent.value?.department_code ?? null)

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
