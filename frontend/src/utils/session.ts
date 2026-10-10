// 登录状态：令牌在后端下发的 HttpOnly Cookie 里，页面脚本读不到（这是故意的：脚本里一次 XSS 也拿不走令牌）。
// 页面能看到的只有 csrf_token 这个普通 Cookie：它既是“已经登录”的标记（和会话 Cookie 同时过期），
// 也是会改数据的请求要放进 X-CSRF-Token 请求头的值（见后端 service/session_cookie.py）。

export const CSRF_COOKIE = 'csrf_token'
export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export function readCookie(name: string): string {
  try {
    const prefix = `${encodeURIComponent(name)}=`
    for (const part of document.cookie.split('; ')) {
      if (part.startsWith(prefix)) return decodeURIComponent(part.slice(prefix.length))
    }
  } catch { /* 读不到就当没有 */ }
  return ''
}

export function readCsrfToken(): string {
  return readCookie(CSRF_COOKIE)
}

/** 浏览器里是否还有登录会话（只是界面上的判断，真正的鉴权在后端；过期后第一次请求会 401 并跳到登录页）。 */
export function hasSession(): boolean {
  return readCsrfToken() !== ''
}

/**
 * 清除页面可读的会话标记。
 *
 * session_token 是 HttpOnly Cookie，只能由后端清除；但当它已经过期或失效时，
 * csrf_token 可能仍短暂留在浏览器里。若不清掉，路由守卫会把用户误判成已登录，
 * 在登录页和受保护页面之间反复跳转并持续产生 401。
 */
export function clearSessionMarker(): void {
  try {
    document.cookie = `${CSRF_COOKIE}=; Max-Age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  } catch { /* ignore */ }
}

/** 会改数据的请求需要带上 CSRF 头；读取类请求不需要。 */
export function csrfHeaders(method: string | undefined): Record<string, string> {
  const token = readCsrfToken()
  if (!token || SAFE_METHODS.has((method || 'GET').toUpperCase())) return {}
  return { [CSRF_HEADER]: token }
}

/** 清掉旧版本留在 localStorage 里的令牌（以前把令牌放在这里，现在不再使用）。 */
export function removeLegacyToken(): void {
  try { localStorage.removeItem('token') } catch { /* ignore */ }
}
