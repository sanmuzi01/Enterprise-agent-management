import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CSRF_COOKIE, clearSessionMarker, csrfHeaders, hasSession, readCookie, readCsrfToken, removeLegacyToken } from './session'

function clearCookies() {
  for (const part of document.cookie.split('; ')) {
    const name = part.split('=')[0]
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  }
}

describe('session（令牌在 HttpOnly Cookie 里，页面只看得到 csrf_token）', () => {
  beforeEach(clearCookies)
  afterEach(() => {
    clearCookies()
    localStorage.clear()
  })

  it('没有登录 Cookie 时：没有会话、没有 CSRF 头', () => {
    expect(hasSession()).toBe(false)
    expect(readCsrfToken()).toBe('')
    expect(csrfHeaders('POST')).toEqual({})
  })

  it('有 csrf_token 就视为有会话，并且会改数据的请求带上 X-CSRF-Token', () => {
    document.cookie = `${CSRF_COOKIE}=abc123; path=/`
    expect(hasSession()).toBe(true)
    for (const method of ['POST', 'put', 'PATCH', 'delete']) {
      expect(csrfHeaders(method)).toEqual({ 'X-CSRF-Token': 'abc123' })
    }
  })

  it('读取类请求不带 CSRF 头', () => {
    document.cookie = `${CSRF_COOKIE}=abc123; path=/`
    for (const method of ['GET', 'get', 'HEAD', 'OPTIONS', undefined]) {
      expect(csrfHeaders(method as string | undefined)).toEqual({})
    }
  })

  it('会话失效时会清除页面可读的会话标记', () => {
    document.cookie = `${CSRF_COOKIE}=stale; path=/`
    expect(hasSession()).toBe(true)
    clearSessionMarker()
    expect(hasSession()).toBe(false)
  })

  it('读 Cookie 不会被名字相近的 Cookie 或特殊字符搞混', () => {
    document.cookie = 'my_csrf_token=wrong; path=/'
    document.cookie = `${CSRF_COOKIE}=a%20b; path=/`
    expect(readCookie(CSRF_COOKIE)).toBe('a b')
    expect(readCookie('missing')).toBe('')
  })

  it('会清掉旧版本留在 localStorage 里的令牌，其它内容不动', () => {
    localStorage.setItem('token', 'legacy-secret')
    localStorage.setItem('theme', 'dark')
    removeLegacyToken()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('theme')).toBe('dark')
  })
})
