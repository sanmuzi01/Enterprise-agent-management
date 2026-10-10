import { defineStore } from 'pinia'
import * as workspaceApi from '../api/enterpriseWorkspace'
import type { Workspace, WorkspaceDepartment } from '../api/enterpriseWorkspace'

const LAST_DEPARTMENT_KEY = 'last_selected_department_id'

export const useCurrentDepartmentStore = defineStore('currentDepartment', {
  state: () => ({
    workspace: null as Workspace | null,
    currentTeamId: JSON.parse(localStorage.getItem(LAST_DEPARTMENT_KEY) || 'null') as number | null,
    loading: false,
    loaded: false,
  }),
  getters: {
    departments: (state): WorkspaceDepartment[] => state.workspace?.departments || [],
    currentDepartment(state): WorkspaceDepartment | null {
      const list = state.workspace?.departments || []
      return list.find((d) => d.id === state.currentTeamId) || null
    },
  },
  actions: {
    selectTeam(teamId: number | null) {
      this.currentTeamId = teamId
      if (teamId != null) {
        localStorage.setItem(LAST_DEPARTMENT_KEY, JSON.stringify(teamId))
      } else {
        localStorage.removeItem(LAST_DEPARTMENT_KEY)
      }
    },
    async load(force = false) {
      if (!force && this.loaded) return this.workspace
      this.loading = true
      try {
        const workspace = await workspaceApi.getWorkspace()
        this.workspace = workspace
        // 记住的部门已经不在成员列表里了（被移出/停用），或者还没选过——
        // 只有一个部门时自动选中，省掉没必要的切换步骤。
        const stillValid = workspace.departments.some((d) => d.id === this.currentTeamId)
        if (!stillValid) {
          this.selectTeam(workspace.departments.length === 1 ? workspace.departments[0].id : null)
        }
        this.loaded = true
        return workspace
      } finally {
        this.loading = false
      }
    },
  },
})
