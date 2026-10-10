import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import { toastError } from './toast'
import { clearSessionMarker, csrfHeaders } from './session'

declare module 'axios' {
  interface AxiosRequestConfig {
    /** 调用方自己处理这个请求的失败（比如“没有权限就隐藏区块”），拦截器不再弹全局错误提示。 */
    skipErrorToast?: boolean
  }
}

const REQUEST_ID_HEADER = 'X-Request-ID'

const createRequestId = () => {
  if (crypto?.randomUUID) return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export const getErrorMessage = (err: any, fallback = '请求失败') => {
  const requestId = err?.response?.headers?.['x-request-id'] || err?.config?.headers?.[REQUEST_ID_HEADER]
  // 系统故障（5xx）时后端会给出问题编号：用户只要把它报给管理员，就能在问题中心直接找到这次故障
  const issueNo = err?.response?.data?.issue_no
  const status = err?.response?.status
  const rawDetail = err?.response?.data?.detail || err?.response?.data?.message || err?.response?.data?.error
  const withRequestId = (message: string) => issueNo ? `${message}（问题编号：${issueNo}）` : requestId ? `${message}（请求ID：${requestId}）` : message

  const statusMessages: Record<number, string> = {
    400: '提交内容有误，请检查填写项后再试',
    401: '登录状态已过期，请重新登录',
    403: '当前账号没有权限执行这个操作',
    404: '没有找到对应内容，请刷新页面后再试',
    409: '当前操作和已有数据冲突，请刷新后重试',
    422: '填写内容格式不正确，请检查后再提交',
    429: '操作太频繁了，请稍后再试',
    500: '服务器内部出错，请稍后重试',
    502: '后端服务暂时不可用，请稍后重试',
    503: '后端服务正在启动或维护，请稍后重试',
    504: '后端处理超时，请稍后重试',
  }

  const detailToText = (detail: any): string => {
    if (!detail) return ''
    if (typeof detail === 'string') return detail
    if (typeof detail === 'object') return detail.message || detail.error || detail.detail || ''
    return String(detail)
  }

  const translateEnglishError = (message: string): string => {
    if (!message) return fallback
    const masked = message
      .replace(/\*{2,}[a-z0-9_-]+/gi, '已隐藏')
      .replace(/sk-[a-z0-9_-]+/gi, '已隐藏')
    const lower = masked.toLowerCase()
    if (lower.includes('authentication') || lower.includes('api key') || lower.includes('apikey') || lower.includes('api_key')) {
      return '访问密钥无效或没有权限，请检查后重新粘贴'
    }
    if (/[\u4e00-\u9fff]/.test(masked)) return masked
    if (lower.includes('request failed with status code') && status && statusMessages[status]) return statusMessages[status]
    if (lower.includes('network error') || lower.includes('failed to fetch')) return '无法连接后端服务，请确认后端已启动并监听 127.0.0.1:8011'
    if (lower.includes('timeout')) return '请求超时，请稍后重试或检查后端任务是否仍在处理'
    if (lower.includes('unauthorized') || lower.includes('invalid token')) return '登录状态已过期，请重新登录'
    if (lower.includes('forbidden')) return '当前账号没有权限执行这个操作'
    if (lower.includes('not found')) return '没有找到对应内容，请刷新页面后再试'
    if (lower.includes('connection') || lower.includes('connect')) return '连接外部服务失败，请检查网络、密钥或服务地址'
    if (lower.includes('model')) return '所选 AI 服务暂时不可用，请换一个推荐方案或稍后重试'
    return fallback
  }

  const detail = detailToText(rawDetail)
  if (detail) return withRequestId(translateEnglishError(detail))
  if (err?.code === 'ECONNABORTED') return withRequestId('请求超时，请稍后重试或检查后端任务是否仍在处理')
  if (!err?.response && (err?.message === 'Network Error' || err?.code === 'ERR_NETWORK')) {
    return withRequestId('无法连接后端服务，请确认后端已启动并监听 127.0.0.1:8011')
  }
  if (status && statusMessages[status]) return withRequestId(statusMessages[status])
  return withRequestId(translateEnglishError(err?.message || fallback))
}

// Axios 单例：统一前缀 /api（匹配 vite.config.ts 的代理）、JWT 注入、401 清理
const request: AxiosInstance = axios.create({
  baseURL: '/api',
  withCredentials: true, // 登录令牌在 HttpOnly Cookie 里，由浏览器自动携带
  timeout: 300_000, // 5 分钟，同步对话 + RAG 切分可能很慢
})

request.interceptors.request.use((config) => {
  config.headers = config.headers || {}
  config.headers[REQUEST_ID_HEADER] = config.headers[REQUEST_ID_HEADER] || createRequestId()
  // 会改数据的请求带上 CSRF 头（值来自页面能读到的 csrf_token Cookie），后端会和会话核对
  Object.assign(config.headers, csrfHeaders(config.method))
  return config
})

request.interceptors.response.use(
  (resp: AxiosResponse) => resp,
  (err) => {
    // 登录接口自身返回 401 表示“账号或密码错误”，应作为普通错误展示，
    // 不能触发“登录过期”清理与跳转逻辑。
    const url: string = err.config?.url || ''
    const isLoginRequest = url.includes('/user/login')
    if (err.response?.status === 401 && !isLoginRequest) {
      localStorage.removeItem('user')
      clearSessionMarker()
      if (location.pathname !== '/login') {
        location.href = '/login'
      }
    } else if (!err.config?.skipErrorToast) {
      toastError(getErrorMessage(err))
    }
    return Promise.reject(err)
  },
)

export default request
