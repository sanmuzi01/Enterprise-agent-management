import { defineStore } from 'pinia'
import * as agentApi from '../api/agent'
import type { AgentInfo } from '../api/agent'
import { useUserStore } from './user'

const LAST_AGENT_KEY = 'last_selected_agent'

export const useAgentSessionStore = defineStore('agentSession', {
  state: () => ({
    selectedAgent: JSON.parse(localStorage.getItem(LAST_AGENT_KEY) || 'null') as AgentInfo | null,
    loading: false,
    loaded: false,
  }),
  actions: {
    remember(agent: AgentInfo | null) {
      this.selectedAgent = agent
      this.loaded = true
      if (agent) {
        localStorage.setItem(LAST_AGENT_KEY, JSON.stringify(agent))
      } else {
        localStorage.removeItem(LAST_AGENT_KEY)
      }
    },
    async loadSelected(force = false) {
      const userStore = useUserStore()
      if (!userStore.isLoggedIn() || userStore.user?.is_admin) {
        this.remember(null)
        return null
      }
      if (!force && this.loaded) return this.selectedAgent
      this.loading = true
      try {
        const agent = await agentApi.getSelectedAgent()
        this.remember(agent)
        return agent
      } finally {
        this.loading = false
      }
    },
    async loadAgent(agentId: number) {
      if (!agentId) return null
      if (this.selectedAgent?.id === agentId) return this.selectedAgent
      this.loading = true
      try {
        return await agentApi.getAgent(agentId)
      } finally {
        this.loading = false
      }
    },
    setSelectedFromList(agents: AgentInfo[]) {
      const selected = agents.find((agent) => agent.is_selected) || null
      this.remember(selected)
    },
  },
})
