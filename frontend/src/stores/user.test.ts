import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn()
const get = vi.fn()
vi.mock('../utils/request', () => ({ default: { post: (...a: unknown[]) => post(...a), get: (...a: unknown[]) => get(...a) } }))

import { useUserStore } from './user'

function setCsrfCookie(value: string) {
  document.cookie = `csrf_token=${value}; path=/`
}
function clearCsrfCookie() {
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
}

describe('user store（登录状态）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    post.mockReset()
    get.mockReset()
    localStorage.clear()
    clearCsrfCookie()
  })
  afterEach(() => {
    clearCsrfCookie()
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('登录：只保存用户信息，不保存令牌（响应体里的 access_token 也不存）', async () => {
    post.mockResolvedValue({ data: { user_id: 7, username: 'li', roles: ['user'], is_admin: false, access_token: 'SECRET-JWT' } })
    const store = useUserStore()
    await store.login('li', 'pw')
    expect(store.user).toMatchObject({ id: 7, name: 'li' })
    expect(JSON.stringify(Object.entries(localStorage))).not.toContain('SECRET-JWT')
    expect(localStorage.getItem('token')).toBeNull()
    expect((store as any).token).toBeUndefined()
  })

  it('登录响应里没有用户编号就当失败', async () => {
    post.mockResolvedValue({ data: { message: '登录失败' } })
    await expect(useUserStore().login('li', 'pw')).rejects.toThrow('登录失败')
  })

  it('isLoggedIn：要同时有用户信息和会话标记（csrf_token Cookie）', () => {
    const store = useUserStore()
    expect(store.isLoggedIn()).toBe(false)
    store.user = { id: 1, name: 'a' }
    expect(store.isLoggedIn()).toBe(false) // 会话 Cookie 过期了：不能再当作已登录
    setCsrfCookie('tok')
    expect(store.isLoggedIn()).toBe(true)
    clearCsrfCookie()
    expect(store.isLoggedIn()).toBe(false) // 之前算过一次也不能缓存：Cookie 过期后必须立刻变成未登录
  })

  it('refreshMe：没有会话就不发请求', async () => {
    expect(await useUserStore().refreshMe()).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('退出登录：先让后端清掉 HttpOnly Cookie，再清本地信息并回到登录页', async () => {
    const hrefs: string[] = []
    vi.stubGlobal('location', { pathname: '/agents', set href(v: string) { hrefs.push(v) } })
    post.mockResolvedValue({ data: {} })
    localStorage.setItem('user', '{"id":1}')
    const store = useUserStore()
    store.user = { id: 1, name: 'a' }
    await store.logout()
    expect(post).toHaveBeenCalledWith('/user/logout', null, expect.anything())
    expect(store.user).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
    expect(hrefs).toEqual(['/login'])
  })

  it('退出登录：后端暂时连不上也要清本地信息', async () => {
    const hrefs: string[] = []
    vi.stubGlobal('location', { pathname: '/agents', set href(v: string) { hrefs.push(v) } })
    post.mockRejectedValue(new Error('Network Error'))
    const store = useUserStore()
    store.user = { id: 1, name: 'a' }
    await store.logout()
    expect(store.user).toBeNull()
    expect(hrefs).toEqual(['/login'])
  })
})
