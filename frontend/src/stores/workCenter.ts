import { defineStore } from 'pinia'
import * as api from '../api/workCenter'
import { hasSession } from '../utils/session'

let timer: ReturnType<typeof setInterval> | null = null

export const useWorkCenterStore = defineStore('workCenter', {
  state: () => ({
    counts: { open: 0, overdue: 0, due_today: 0, high: 0 } as api.WorkItemCounts,
    unread: 0,
  }),
  getters: {
    // 侧栏角标：逾期 + 今天到期，没有紧急的时候不打扰。
    urgent: (state) => state.counts.overdue + state.counts.due_today,
  },
  actions: {
    /** background：定时轮询发起的（不是用户操作），不让服务端因此续期登录 */
    async refresh(background = false) {
      if (!hasSession()) return
      try {
        const [counts, unread] = await Promise.all([api.getWorkItemCounts({ background }), api.getUnreadCount({ background })])
        this.counts = counts
        this.unread = unread
      } catch {
        // 计数失败不影响页面，下一轮轮询再试。
      }
    },
    startPolling(intervalMs = 60000) {
      if (!hasSession()) return
      this.refresh()
      if (timer) return
      timer = setInterval(() => {
        if (document.visibilityState === 'visible') this.refresh(true)
      }, intervalMs)
    },
    stopPolling() {
      if (timer) clearInterval(timer)
      timer = null
    },
  },
})
