<template>
  <div class="app-canvas min-h-screen overflow-y-auto">
    <div class="absolute right-4 top-4 z-10"><ThemeToggle /></div>
    <main class="mx-auto flex w-full max-w-[392px] flex-col items-center px-5 pb-10 pt-[clamp(28px,8vh,88px)]">
      <span
        class="flex h-[76px] w-[76px] items-center justify-center rounded-[22px] bg-gradient-to-b from-[#3d3d40] to-[#0e0e10] text-white shadow-[inset_0_1px_0_rgba(255,255,255,.26),0_8px_24px_rgba(0,0,0,.22),0_1px_3px_rgba(0,0,0,.3)]"
      >
        <Sparkles :size="36" :stroke-width="1.6" />
      </span>
      <h1 class="mt-6 text-center text-[clamp(26px,5vw,34px)] font-semibold leading-tight tracking-[-0.03em] [text-wrap:balance]">
        {{ heading }}
      </h1>
      <p class="mt-2 text-center text-[15px] leading-6 text-slate-500">{{ lead }}</p>

      <div
        v-if="!isResetMode"
        class="relative mt-6 grid w-full grid-cols-2 rounded-[10px] bg-slate-100 p-[3px]"
        role="tablist"
        aria-label="登录或注册"
      >
        <span
          class="absolute bottom-[3px] left-[3px] top-[3px] w-[calc(50%-3px)] rounded-[8px] bg-white shadow-[0_1px_3px_rgba(0,0,0,.14),0_0_0_.5px_rgba(0,0,0,.04)] transition-transform duration-500 ease-[var(--spring)]"
          :class="isRegisterMode ? 'translate-x-full' : ''"
        ></span>
        <button
          role="tab"
          :aria-selected="!isRegisterMode"
          @click="switchMode(false)"
          :class="!isRegisterMode ? 'text-slate-900' : 'text-slate-500'"
          class="relative z-10 h-8 text-[13px] font-medium"
        >
          登录
        </button>
        <button
          role="tab"
          :aria-selected="isRegisterMode"
          @click="switchMode(true)"
          :class="isRegisterMode ? 'text-slate-900' : 'text-slate-500'"
          class="relative z-10 h-8 text-[13px] font-medium"
        >
          注册
        </button>
      </div>

      <!-- 找回密码 -->
      <div v-if="isResetMode" class="mt-6 w-full">
        <div class="ui-panel overflow-hidden">
          <label class="row">
            <span class="row-label">手机号</span>
            <input
              v-model="phone"
              type="tel"
              inputmode="numeric"
              autocomplete="tel"
              maxlength="11"
              class="row-input"
              placeholder="注册时使用的手机号"
              @input="normalizePhoneInput"
              @keydown.enter="!isImeEnter($event) && submit()"
            />
          </label>
          <label class="row row-border">
            <span class="row-label">验证码</span>
            <span class="flex h-full items-center gap-2">
              <input
                v-model="smsCode"
                type="text"
                inputmode="numeric"
                maxlength="6"
                autocomplete="one-time-code"
                class="row-input"
                placeholder="6 位数字"
                @keydown.enter="!isImeEnter($event) && submit()"
              />
              <button
                type="button"
                @click.prevent="sendSmsCode"
                :disabled="smsSending || smsCountdown > 0 || !canSendSms"
                class="shrink-0 text-[13px] font-medium text-[var(--accent)] disabled:cursor-not-allowed disabled:text-slate-400"
              >
                {{ smsButtonText }}
              </button>
            </span>
          </label>
          <label class="row row-border">
            <span class="row-label">新密码</span>
            <span class="flex h-full items-center gap-2">
              <input
                v-model="resetNewPassword"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="new-password"
                class="row-input"
                placeholder="至少 6 位"
                @keydown.enter="!isImeEnter($event) && submit()"
              />
              <button
                type="button"
                class="shrink-0 text-slate-400 hover:text-slate-700"
                :title="showPassword ? '隐藏密码' : '显示密码'"
                @click.prevent="showPassword = !showPassword"
              >
                <component :is="showPassword ? EyeOff : Eye" :size="17" :stroke-width="1.7" />
              </button>
            </span>
          </label>
        </div>
        <p v-if="smsHint" class="mt-2 px-1 text-[12px] text-slate-500">{{ smsHint }}</p>

        <p v-if="errorMsg" class="mt-4 rounded-xl bg-red-50 px-3.5 py-2.5 text-[13px] text-red-700">{{ errorMsg }}</p>
        <p v-if="successMsg" class="mt-4 rounded-xl bg-emerald-50 px-3.5 py-2.5 text-[13px] text-emerald-700">{{ successMsg }}</p>

        <button @click="submit" :disabled="loading" class="ui-primary mt-5 h-12 w-full text-[15px] font-medium tracking-[-0.01em]">
          {{ loading ? '处理中…' : '重置密码' }}
        </button>
        <button type="button" class="mt-4 w-full text-center text-[14px] text-[var(--accent)]" @click="closeReset">返回登录</button>
      </div>

      <!-- 登录 / 注册 -->
      <div v-else class="mt-4 w-full">
        <div class="ui-panel overflow-hidden">
          <label class="row">
            <span class="row-label">用户名</span>
            <input
              v-model="username"
              type="text"
              autocomplete="username"
              class="row-input"
              placeholder="3–20 个字符"
              @keydown.enter="!isImeEnter($event) && submit()"
            />
          </label>
          <label class="row row-border">
            <span class="row-label">密码</span>
            <span class="flex h-full items-center gap-2">
              <input
                v-model="password"
                :type="showPassword ? 'text' : 'password'"
                :autocomplete="isRegisterMode ? 'new-password' : 'current-password'"
                class="row-input"
                placeholder="至少 6 位"
                @keydown.enter="!isImeEnter($event) && submit()"
              />
              <button
                type="button"
                class="shrink-0 text-slate-400 hover:text-slate-700"
                :title="showPassword ? '隐藏密码' : '显示密码'"
                @click.prevent="showPassword = !showPassword"
              >
                <component :is="showPassword ? EyeOff : Eye" :size="17" :stroke-width="1.7" />
              </button>
            </span>
          </label>

          <div
            :class="[
              'grid transition-[grid-template-rows] duration-500 ease-[var(--ease)]',
              isRegisterMode ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
            ]"
          >
            <div class="min-h-0 overflow-hidden" :inert="!isRegisterMode">
              <label class="row row-border">
                <span class="row-label">年龄</span>
                <input
                  v-model.number="age"
                  type="number"
                  min="0"
                  max="150"
                  class="row-input"
                  placeholder="请输入年龄"
                  @keydown.enter="!isImeEnter($event) && submit()"
                />
              </label>
              <label class="row row-border">
                <span class="row-label">手机号</span>
                <input
                  v-model="phone"
                  type="tel"
                  inputmode="numeric"
                  autocomplete="tel"
                  maxlength="11"
                  class="row-input"
                  placeholder="用于接收验证码"
                  @input="normalizePhoneInput"
                  @keydown.enter="!isImeEnter($event) && submit()"
                />
              </label>
              <label class="row row-border">
                <span class="row-label">验证码</span>
                <span class="flex h-full items-center gap-2">
                  <input
                    v-model="smsCode"
                    type="text"
                    inputmode="numeric"
                    maxlength="6"
                    autocomplete="one-time-code"
                    class="row-input"
                    placeholder="6 位数字"
                    @keydown.enter="!isImeEnter($event) && submit()"
                  />
                  <button
                    type="button"
                    @click.prevent="sendSmsCode"
                    :disabled="smsSending || smsCountdown > 0 || !canSendSms"
                    class="shrink-0 text-[13px] font-medium text-[var(--accent)] disabled:cursor-not-allowed disabled:text-slate-400"
                  >
                    {{ smsButtonText }}
                  </button>
                </span>
              </label>
            </div>
          </div>
        </div>

        <p v-if="usernameHint" class="mt-2 px-1 text-[12px] text-amber-600">{{ usernameHint }}</p>
        <p v-if="isRegisterMode && smsHint" class="mt-2 px-1 text-[12px] text-slate-500">{{ smsHint }}</p>

        <div v-if="isRegisterMode && password" class="mt-3 px-1">
          <div class="h-1 rounded-full bg-slate-100">
            <div class="h-1 rounded-full transition-all duration-500" :class="passwordStrengthClass" :style="{ width: `${passwordStrength.percent}%` }"></div>
          </div>
          <p class="mt-1.5 text-[12px] text-slate-500">密码强度：{{ passwordStrength.text }}</p>
        </div>

        <label v-if="isRegisterMode" class="mt-4 flex cursor-pointer items-start gap-2.5 px-1 text-[13px] leading-5 text-slate-600">
          <input v-model="acceptedTerms" type="checkbox" class="mt-0.5 h-4 w-4 rounded accent-[var(--accent)]" />
          <span>
            我已阅读并同意
            <button type="button" class="font-medium text-[var(--accent)]" @click.prevent="showTerms = true">用户须知</button>
          </span>
        </label>

        <p v-if="errorMsg" class="mt-4 rounded-xl bg-red-50 px-3.5 py-2.5 text-[13px] text-red-700">{{ errorMsg }}</p>
        <p v-if="successMsg" class="mt-4 rounded-xl bg-emerald-50 px-3.5 py-2.5 text-[13px] text-emerald-700">{{ successMsg }}</p>

        <button
          @click="submit"
          :disabled="loading || (isRegisterMode && !acceptedTerms)"
          class="ui-primary mt-5 h-12 w-full text-[15px] font-medium tracking-[-0.01em]"
        >
          {{ loading ? '处理中…' : isRegisterMode ? '注册并登录' : '登录' }}
        </button>

        <button v-if="!isRegisterMode" type="button" class="mt-4 block w-full text-center text-[14px] text-[var(--accent)]" @click="openReset">
          忘记密码？
        </button>
      </div>

      <p class="mt-10 max-w-[300px] text-center text-[12px] leading-relaxed text-slate-400">
        管理员账号登录后会自动进入后台，普通用户无法进入管理员页面。
      </p>
      <div class="mt-5 flex flex-wrap justify-center gap-x-3.5 gap-y-1 text-[12px] text-slate-400">
        <template v-for="(cap, i) in capabilities" :key="cap">
          <span v-if="i > 0" aria-hidden="true">·</span>
          <span>{{ cap }}</span>
        </template>
      </div>
      <IcpFooter class="mt-5" />
    </main>

    <Transition name="sheet">
      <div v-if="showTerms" class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm">
        <section class="w-full max-w-md rounded-[22px] bg-white p-6 shadow-2xl">
          <div class="flex items-start justify-between gap-4">
            <div>
              <h3 class="text-[18px] font-semibold tracking-[-0.02em] text-slate-900">用户须知</h3>
              <p class="mt-1 text-[13px] text-slate-500">注册前请确认你理解以下使用规则。</p>
            </div>
            <button
              type="button"
              class="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-800"
              aria-label="关闭"
              @click="showTerms = false"
            >
              <X :size="16" />
            </button>
          </div>
          <div class="mt-4 space-y-3 text-[14px] leading-6 text-slate-600">
            <p>你需要妥善保管自己的账号、密码、模型密钥和上传资料，不要提交违法、侵权或包含敏感隐私的数据。</p>
            <p>平台会按功能需要处理你上传的资料、能力配置和对话内容，用于提供助手、检索和任务执行能力。</p>
            <p>管理员可基于安全、合规和资源保护需要，对异常账号、异常请求和高风险内容进行管控。</p>
          </div>
          <div class="mt-6 flex justify-end gap-2">
            <button type="button" class="h-9 rounded-full bg-slate-100 px-4 text-[14px] font-medium text-slate-700 hover:bg-slate-200" @click="showTerms = false">
              关闭
            </button>
            <button type="button" class="ui-primary h-9 px-4 text-[14px] font-medium" @click="acceptTerms">同意并继续</button>
          </div>
        </section>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Eye, EyeOff, Sparkles, X } from 'lucide-vue-next'
import { useUserStore } from '../stores/user'
import { getErrorMessage } from '../utils/request'
import ThemeToggle from '../components/ThemeToggle.vue'
import IcpFooter from '../components/IcpFooter.vue'

const username = ref('')
const password = ref('')
const age = ref<number | null>(18)
const phone = ref('')
const smsCode = ref('')
const smsSending = ref(false)
const smsCountdown = ref(0)
const smsHint = ref('')
const acceptedTerms = ref(false)
const showTerms = ref(false)
const showPassword = ref(false)
const loading = ref(false)
const errorMsg = ref('')
const successMsg = ref('')
const isRegisterMode = ref(false)
const isResetMode = ref(false)
const resetNewPassword = ref('')
const router = useRouter()
const userStore = useUserStore()
let smsTimer: number | undefined

const capabilities = ['私人助手', '知识空间', '技能中心']

const heading = computed(() => {
  if (isResetMode.value) return '重置登录密码'
  return isRegisterMode.value ? '创建你的账号' : '登录 AI 助手工作台'
})
const lead = computed(() => {
  if (isResetMode.value) return '用注册手机号接收验证码，再设置新密码。'
  return isRegisterMode.value ? '注册后即可创建自己的助手和知识空间。' : '使用你的账号继续。'
})

const switchMode = (registerMode: boolean) => {
  isRegisterMode.value = registerMode
  errorMsg.value = ''
  successMsg.value = ''
}

const openReset = () => {
  isResetMode.value = true
  phone.value = ''
  smsCode.value = ''
  resetNewPassword.value = ''
  errorMsg.value = ''
  successMsg.value = ''
  smsHint.value = ''
}

const closeReset = () => {
  isResetMode.value = false
  phone.value = ''
  smsCode.value = ''
  resetNewPassword.value = ''
  errorMsg.value = ''
  successMsg.value = ''
  smsHint.value = ''
}

const normalizedPhone = computed(() => phone.value.replace(/\D/g, ''))
const canSendSms = computed(() => /^1[3-9]\d{9}$/.test(normalizedPhone.value))
const smsButtonText = computed(() => {
  if (smsSending.value) return '发送中…'
  if (smsCountdown.value > 0) return `${smsCountdown.value}s 后重发`
  return '获取验证码'
})
const usernameHint = computed(() => {
  const value = username.value.trim()
  if (!value) return ''
  if (value.length < 3) return '用户名至少 3 个字符'
  if (value.length > 20) return '用户名最多 20 个字符'
  return ''
})
const passwordStrength = computed(() => {
  const value = password.value
  let score = 0
  if (value.length >= 6) score += 1
  if (value.length >= 10) score += 1
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score += 1
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1
  if (score <= 1) return { text: '较弱', percent: 28 }
  if (score === 2) return { text: '可用', percent: 56 }
  if (score === 3) return { text: '良好', percent: 78 }
  return { text: '较强', percent: 100 }
})
const passwordStrengthClass = computed(() => {
  if (passwordStrength.value.percent < 50) return 'bg-amber-400'
  if (passwordStrength.value.percent < 80) return 'bg-[var(--accent)]'
  return 'bg-emerald-500'
})

const startSmsCountdown = (seconds: number) => {
  smsCountdown.value = Math.max(1, seconds)
  if (smsTimer) window.clearInterval(smsTimer)
  smsTimer = window.setInterval(() => {
    smsCountdown.value -= 1
    if (smsCountdown.value <= 0 && smsTimer) {
      window.clearInterval(smsTimer)
      smsTimer = undefined
    }
  }, 1000)
}

const normalizePhoneInput = () => {
  phone.value = normalizedPhone.value.slice(0, 11)
}

const sendSmsCode = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  smsHint.value = ''
  if (!canSendSms.value) {
    errorMsg.value = '请输入有效的手机号'
    return
  }
  smsSending.value = true
  try {
    const result = isResetMode.value
      ? await userStore.sendResetPasswordSmsCode(normalizedPhone.value)
      : await userStore.sendRegisterSmsCode(normalizedPhone.value)
    const retryAfter = Number(result?.retry_after || 60)
    startSmsCountdown(retryAfter)
    smsHint.value = result?.dev_code
      ? `本地开发验证码：${result.dev_code}`
      : '验证码已发送，请注意查收'
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '验证码发送失败')
  } finally {
    smsSending.value = false
  }
}

const acceptTerms = () => {
  acceptedTerms.value = true
  showTerms.value = false
}

const validate = () => {
  if (isResetMode.value) {
    if (!canSendSms.value) return '请输入有效的手机号'
    if (!/^\d{6}$/.test(smsCode.value.trim())) return '请输入 6 位短信验证码'
    if (resetNewPassword.value.length < 6) return '新密码至少需要 6 位'
    return ''
  }
  if (!username.value.trim() || !password.value) return '请输入用户名和密码'
  if (username.value.trim().length < 3 || username.value.trim().length > 20) return '用户名长度需要在 3-20 个字符之间'
  if (password.value.length < 6) return '密码至少需要 6 位'
  if (isRegisterMode.value) {
    if (age.value === null || age.value < 0 || age.value > 150) return '年龄需要在 0-150 之间'
    if (!canSendSms.value) return '请输入有效的手机号'
    if (!/^\d{6}$/.test(smsCode.value.trim())) return '请输入 6 位短信验证码'
    if (!acceptedTerms.value) return '请先阅读并同意用户须知'
  }
  return ''
}

const submit = async () => {
  const err = validate()
  if (err) {
    errorMsg.value = err
    return
  }
  loading.value = true
  errorMsg.value = ''
  successMsg.value = ''
  try {
    if (isResetMode.value) {
      await userStore.resetPassword(normalizedPhone.value, smsCode.value.trim(), resetNewPassword.value)
      closeReset()
      successMsg.value = '密码已重置，请用新密码登录'
      return
    }
    if (isRegisterMode.value) {
      const result = await userStore.register(
        username.value.trim(),
        password.value,
        Number(age.value),
        normalizedPhone.value,
        smsCode.value.trim(),
        acceptedTerms.value,
      )
      if (result?.message && result.message !== '注册成功') {
        throw new Error(result.message)
      }
      successMsg.value = '注册成功，正在登录...'
    }
    await userStore.login(username.value.trim(), password.value)
    // 管理员跳 /admin；普通用户进 /home，由路由守卫决定落在部门工作台还是个人工作台
    router.push(userStore.user?.is_admin ? '/admin' : '/home')
  } catch (e: any) {
    const fallback = isResetMode.value ? '重置密码失败' : isRegisterMode.value ? '注册失败' : '登录失败'
    errorMsg.value = getErrorMessage(e, fallback)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (userStore.isLoggedIn()) router.push(userStore.user?.is_admin ? '/admin' : '/home')
})

onUnmounted(() => {
  if (smsTimer) window.clearInterval(smsTimer)
})
</script>

<style scoped>
.row {
  display: grid;
  height: 52px;
  cursor: text;
  grid-template-columns: 76px 1fr;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  transition: background-color 0.2s;
}
.row:focus-within {
  background: var(--accent-soft);
}
.row-border {
  border-top: 1px solid var(--line);
}
.row-label {
  font-size: 14px;
  font-weight: 500;
}
.row-input {
  height: 100%;
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 15px;
}
.row-input::placeholder {
  color: var(--ink-3);
}
</style>
