import { defineStore } from 'pinia'
import request from '../utils/request'
import { hasSession, removeLegacyToken } from '../utils/session'

export interface User {
  id: number
  name: string
  selected_agent_id?: number | null
  selected_agent?: any
  roles?: string[]
  is_admin?: boolean
}

const USER_KEY = 'user'

removeLegacyToken()   // 旧版本把令牌存在 localStorage 里，现在令牌只在 HttpOnly Cookie 里

export const useUserStore = defineStore('user', {
  state: () => ({
    user: JSON.parse(localStorage.getItem(USER_KEY) || 'null') as User | null,
  }),
  actions: {
    /**
     * 浏览器里是否还有登录会话（令牌本身在 HttpOnly Cookie 里，页面读不到）。
     * 必须是每次现算的方法，不能做成 getter：Pinia 的 getter 会缓存，而 Cookie 不是响应式的，
     * 会话过期之后缓存的结果还会一直是“已登录”。
     */
    isLoggedIn(): boolean {
      return hasSession() && !!this.user
    },
    async login(name: string, password: string) {
      const { data } = await request.post('/user/login', { name, password })
      // 后端返回：{ user_id, username, roles, ... }，并通过 Set-Cookie 下发 HttpOnly 的登录 Cookie；响应体里的 access_token 前端不保存
      if (data.user_id == null) {
        throw new Error(data.message || '登录失败')
      }
      const user: User = {
        id: data.user_id,
        name: data.username || data.user?.name || name,
        selected_agent_id: data.selected_agent_id ?? data.user?.selected_agent_id ?? null,
        roles: data.roles || [],
        is_admin: Boolean(data.is_admin),
      }
      this.user = user
      localStorage.setItem(USER_KEY, JSON.stringify(user))
      return { user }
    },
    async refreshMe() {
      if (!hasSession()) return null
      const { data } = await request.get('/user/me')
      const user: User = {
        id: data.user_id,
        name: data.username,
        selected_agent_id: data.selected_agent_id ?? null,
        roles: data.roles || [],
        is_admin: Boolean(data.is_admin),
      }
      this.user = user
      localStorage.setItem(USER_KEY, JSON.stringify(user))
      return user
    },
    async sendRegisterSmsCode(phone: string) {
      const { data } = await request.post('/user/register/sms-code', { phone })
      return data
    },
    async register(name: string, password: string, age: number, phone: string, smsCode: string, acceptedTerms: boolean) {
      const { data } = await request.post('/user/register', {
        name,
        password,
        age,
        phone,
        sms_code: smsCode,
        accepted_terms: acceptedTerms,
      })
      return data
    },
    async changePassword(oldPassword: string, newPassword: string) {
      const { data } = await request.post('/user/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      })
      return data
    },
    async sendResetPasswordSmsCode(phone: string) {
      const { data } = await request.post('/user/reset-password/sms-code', { phone })
      return data
    },
    async resetPassword(phone: string, smsCode: string, newPassword: string) {
      const { data } = await request.post('/user/reset-password', {
        phone,
        sms_code: smsCode,
        new_password: newPassword,
      })
      return data
    },
    async logout() {
      // 让后端清掉 HttpOnly 的登录 Cookie（页面脚本自己删不掉它）；失败也要继续清本地状态并回到登录页
      try {
        await request.post('/user/logout', null, { skipErrorToast: true } as any)
      } catch { /* 网络失败时 Cookie 会在到期后自然失效 */ }
      this.user = null
      localStorage.removeItem(USER_KEY)
      if (location.pathname !== '/login') {
        location.href = '/login'
      }
    },
  },
})
