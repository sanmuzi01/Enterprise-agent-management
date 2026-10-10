import type { InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./toast', () => ({ toastError: vi.fn() }))

import request from './request'

type Seen = InternalAxiosRequestConfig

// 不真的发网络请求：用一个假的适配器记下最终发出去的配置
function captureRequests() {
  const seen: Seen[] = []
  request.defaults.adapter = async (config: Seen) => {
    seen.push(config)
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
  }
  return seen
}

describe('request（统一的 Axios 实例）', () => {
  beforeEach(() => {
    document.cookie = 'csrf_token=tok-1; path=/'
    localStorage.setItem('token', 'legacy-should-not-be-sent')
  })
  afterEach(() => {
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
    localStorage.clear()
  })

  it('带 Cookie 发送，并且从来不发 Authorization 头（令牌不在页面能读到的地方）', async () => {
    const seen = captureRequests()
    await request.get('/user/me')
    expect(seen[0].withCredentials).toBe(true)
    expect(seen[0].headers.get('Authorization')).toBeFalsy()
  })

  it('会改数据的请求带 X-CSRF-Token，读取请求不带', async () => {
    const seen = captureRequests()
    await request.get('/user/me')
    await request.post('/user/profile', {})
    await request.put('/user/profile', {})
    await request.delete('/something/1')
    expect(seen.map((c) => c.headers.get('X-CSRF-Token') || '')).toEqual(['', 'tok-1', 'tok-1', 'tok-1'])
  })

  it('每个请求都有 X-Request-ID', async () => {
    const seen = captureRequests()
    await request.get('/a')
    await request.get('/b')
    const ids = seen.map((c) => String(c.headers.get('X-Request-ID')))
    expect(ids[0]).toMatch(/^[\w.:-]{8,80}$/)
    expect(ids[0]).not.toBe(ids[1])
  })

  it('401 会清掉本地的用户信息并回到登录页（登录接口自己的 401 不算）', async () => {
    localStorage.setItem('user', '{"id":1}')
    const assign = vi.fn()
    vi.stubGlobal('location', { pathname: '/agents', set href(v: string) { assign(v) } })
    request.defaults.adapter = async (config: Seen) => {
      const error: any = new Error('401')
      error.config = config
      error.response = { status: 401, data: { detail: '登录状态已失效' }, config }
      throw error
    }
    await expect(request.get('/user/me', { skipErrorToast: true } as any)).rejects.toBeTruthy()
    expect(localStorage.getItem('user')).toBeNull()
    expect(document.cookie).not.toContain('csrf_token=')
    expect(assign).toHaveBeenCalledWith('/login')

    localStorage.setItem('user', '{"id":1}')
    assign.mockClear()
    await expect(request.post('/user/login', {}, { skipErrorToast: true } as any)).rejects.toBeTruthy()
    expect(localStorage.getItem('user')).not.toBeNull()
    expect(assign).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
