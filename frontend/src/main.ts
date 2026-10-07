import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import '@fontsource-variable/inter'
import './style.css'
import { initTheme } from './utils/theme'
import { installErrorReporter } from './utils/errorReporter'
import App from './App.vue'

initTheme()
const app = createApp(App)
app.use(createPinia())
app.use(router)
installErrorReporter(app)
app.mount('#app')