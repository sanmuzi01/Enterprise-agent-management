import type { App } from 'vue'

// 页面脚本错误上报到问题中心（后端 POST /client-errors）。
// 约束：绝不影响页面（所有失败都吞掉）；不带用户身份和页面内容；同一个错误一次页面打开只报一次，最多报 5 个；
// 请求失败（axios 错误）已经由请求拦截器提示并由后端记录，这里不重复报。
const MAX_PER_PAGE = 5
const sent = new Set<string>()

interface Reportable { name?: string; message?: string; stack?: string; isAxiosError?: boolean }

export function reportClientError(error: unknown, source = ''): void {
  try {
    const err: Reportable = typeof error === 'string' ? { message: error } : ((error as Reportable) || {})
    if (err.isAxiosError || !navigator.onLine) return
    const message = String(err.message || '').slice(0, 500)
    if (!message) return
    const stack = String(err.stack || '')
    const key = `${err.name}|${message}|${stack.split('\n')[1] || ''}`
    if (sent.has(key) || sent.size >= MAX_PER_PAGE) return
    sent.add(key)
    void fetch('/api/client-errors', {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: err.name || 'Error', message, stack: stack.slice(0, 4000), route: location.pathname, source: source.slice(0, 60) }),
    }).catch(() => undefined)
  } catch {
    /* 上报本身出错不能再影响页面 */
  }
}

export function installErrorReporter(app: App): void {
  const previous = app.config.errorHandler
  app.config.errorHandler = (err, instance, info) => {
    reportClientError(err, `vue:${info}`)
    if (previous) previous(err, instance, info)
    else console.error(err)
  }
  window.addEventListener('error', (event) => reportClientError(event.error ?? event.message, 'window'))
  window.addEventListener('unhandledrejection', (event) => reportClientError(event.reason, 'promise'))
}
