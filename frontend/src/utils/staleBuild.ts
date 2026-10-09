/** 重新部署后，开着的旧页面打不开新页面的问题。
 *
 * 构建产物的文件名带内容哈希（AdminUsers-Ab12.js），每次发版都会变。页面已经开着的人还拿着旧版的文件清单，
 * 点菜单时去要一个已经不存在的旧文件，就会加载失败，表现为“点了没反应 / 白屏”。
 * 服务器这边：入口页面不缓存、缺的脚本返回 404（见 deploy/nginx.conf）；页面这边：发现是“页面文件加载失败”，
 * 就整页刷新一次，拿到新版本后直接打开用户要去的那一页。
 * 防死循环：10 秒内只自动刷新一次；刷新后还失败，说明不是版本问题，交给正常的错误提示。
 */

const RELOAD_KEY = 'stale_build_reload_at'
const RELOAD_WINDOW_MS = 10_000

/** 各浏览器、Vite 报“动态加载的页面文件拿不到”的说法不同，这里都认 */
const CHUNK_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,        // Chrome
  /error loading dynamically imported module/i,           // Firefox
  /Importing a module script failed/i,                    // Safari
  /Unable to preload CSS/i,                               // Vite 预加载样式失败
  /ChunkLoadError|Loading chunk [\w-]+ failed/i,
  /is not a valid JavaScript MIME type/i,                 // 旧文件不存在时拿回来的是 HTML
]

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : String(error ?? '')
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}

function readLastReload(storage: Storage | undefined): number {
  try {
    return Number(storage?.getItem(RELOAD_KEY) || 0)
  } catch {
    return 0
  }
}

/** 是否应该自动刷新（并记下这次刷新的时间）。10 秒内已经刷过一次就不再刷。 */
export function shouldReloadForNewVersion(now = Date.now(), storage: Storage | undefined = globalThis.sessionStorage): boolean {
  if (now - readLastReload(storage) < RELOAD_WINDOW_MS) return false
  try {
    storage?.setItem(RELOAD_KEY, String(now))
  } catch {
    // 存不了（隐私模式等）也刷新一次：最坏情况是用户自己再点一下
  }
  return true
}

/** 整页刷新到目标地址（默认当前地址）。返回是否真的刷新了。 */
export function reloadForNewVersion(target?: string, deps: { now?: number; storage?: Storage; assign?: (url: string) => void } = {}): boolean {
  if (!shouldReloadForNewVersion(deps.now, deps.storage ?? globalThis.sessionStorage)) return false
  const assign = deps.assign ?? ((url: string) => window.location.assign(url))
  assign(target || window.location.pathname + window.location.search + window.location.hash)
  return true
}
