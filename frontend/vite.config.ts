import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// 生产环境 Nginx 下发的内容安全策略（deploy/nginx.conf）。`npm run preview` 用同一份，这样上线前能在本地看到策略有没有挡掉页面自己的东西。
// 开发服务器（HMR 要用 WebSocket 和 eval）不启用。tests/test_csp_policy.py 会核对两处是否一致。
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"

const apiProxy = {
  // 前端请求 /api/xxx → 代理到 http://127.0.0.1:8011/xxx ，避免 CORS
  '/api': {
    target: 'http://127.0.0.1:8011',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
}

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    restoreMocks: true,
  },
  preview: {
    port: 4173,
    headers: { 'Content-Security-Policy': CSP },
    proxy: apiProxy,
  },
  server: {
    port: 5173,
    proxy: apiProxy,
  },
})
