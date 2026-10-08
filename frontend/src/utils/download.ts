import request from './request'

/**
 * 下载一个需要登录态的接口返回的文件（如 CSV 导出）。
 *
 * 普通 <a href> 带不上 CSRF 头等，所以走 axios 实例（带拦截器：Cookie 登录态 + CSRF 头）+
 * responseType: 'blob'，再用临时 <a download> 触发浏览器保存。
 */
export async function downloadFile(url: string, params?: Record<string, unknown>, fallbackName = 'download') {
  const response = await request.get(url, { params, responseType: 'blob' })
  const disposition = String(response.headers?.['content-disposition'] || '')
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match ? decodeURIComponent(match[1]) : fallbackName

  const blobUrl = URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(blobUrl)
}
