import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import '@fontsource-variable/inter'
import './style.css'
import { initTheme } from './utils/theme'
import { installErrorReporter } from './utils/errorReporter'
import { isChunkLoadError, reloadForNewVersion } from './utils/staleBuild'
import App from './App.vue'

// 重新部署后，开着的旧页面去要已经不存在的旧版页面文件：整页刷新一次拿新版本（见 utils/staleBuild.ts）
window.addEventListener('vite:preloadError', (event) => {
  if (reloadForNewVersion()) event.preventDefault()
})
router.onError((error, to) => {
  if (isChunkLoadError(error)) reloadForNewVersion(to.fullPath)
})

initTheme()
const app = createApp(App)
app.use(createPinia())
app.use(router)
installErrorReporter(app)
app.mount('#app')
