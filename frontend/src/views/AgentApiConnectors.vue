<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-sky-200/70 ui-glass px-5 py-3 shadow-sm backdrop-blur-xl lg:px-8">
      <AgentSubnav :agent-id="agentId" :agent-name="agent?.name" active="tools" />
    </header>

    <main class="mx-auto w-full max-w-5xl flex-1 overflow-y-auto p-5 lg:p-8">
      <!-- 新建默认只留给管理员——单企业部署下接入外部系统应由管理员统一审核配置，
           不是员工自助接的（后端 FasdtApi/agent.py 的 create_api_connector 同步做了强制校验，
           这里只是不显示入口，不是唯一防线）。企业智能体在后台「企业智能体 → 编辑 → 接口工具」里配。 -->
      <ApiConnectorManager :agent-id="agentId" mode="personal" :can-create="isAdmin" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AgentSubnav from '../components/agent/AgentSubnav.vue'
import ApiConnectorManager from '../components/agent/ApiConnectorManager.vue'
import { getAgent, type AgentInfo } from '../api/agent'
import { useUserStore } from '../stores/user'

const props = defineProps<{ agentId: string | number }>()
const agentId = computed(() => Number(props.agentId))
const userStore = useUserStore()
const isAdmin = computed(() => Boolean(userStore.user?.is_admin))

const agent = ref<AgentInfo | null>(null)

onMounted(async () => {
  try {
    agent.value = await getAgent(agentId.value)
  } catch { agent.value = null }
})
</script>
