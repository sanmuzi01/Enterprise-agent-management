import { describe, expect, it, vi } from 'vitest'
import { isChunkLoadError, reloadForNewVersion, shouldReloadForNewVersion } from './staleBuild'

function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => { data.set(k, String(v)) },
    removeItem: (k) => { data.delete(k) },
    clear: () => data.clear(),
    key: () => null,
    get length() { return data.size },
  }
}

describe('isChunkLoadError', () => {
  it('认出各浏览器“页面文件加载失败”的报错', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: https://x/assets/AdminUsers-Ab12.js'))).toBe(true)
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
    expect(isChunkLoadError(new Error('Unable to preload CSS for /assets/Login-1.css'))).toBe(true)
    expect(isChunkLoadError("Failed to load module script: 'text/html' is not a valid JavaScript MIME type")).toBe(true)
  })

  it('普通的业务错误不算', () => {
    expect(isChunkLoadError(new Error('Request failed with status code 500'))).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
  })
})

describe('reloadForNewVersion', () => {
  it('刷新到用户要去的那一页', () => {
    const assign = vi.fn()
    expect(reloadForNewVersion('/admin/users', { now: 100_000, storage: memoryStorage(), assign })).toBe(true)
    expect(assign).toHaveBeenCalledWith('/admin/users')
  })

  it('10 秒内只自动刷新一次，防止新版本本身有问题时无限刷新', () => {
    const storage = memoryStorage()
    const assign = vi.fn()
    expect(reloadForNewVersion('/a', { now: 100_000, storage, assign })).toBe(true)
    expect(reloadForNewVersion('/a', { now: 105_000, storage, assign })).toBe(false)
    expect(assign).toHaveBeenCalledTimes(1)
    expect(reloadForNewVersion('/a', { now: 111_000, storage, assign })).toBe(true)   // 过了 10 秒是新的一次
  })

  it('浏览器不让存（隐私模式）也照常刷新一次', () => {
    const broken = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } } as unknown as Storage
    expect(shouldReloadForNewVersion(100_000, broken)).toBe(true)
  })
})
