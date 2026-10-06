<template>
  <div class="p-6">
    <div class="mb-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <p class="text-xs text-slate-500">系统诊断：数据库、Redis、缓存、限流、Worker、生产配置</p>
        <p class="mt-1 text-sm font-medium" :class="health?.ok ? 'text-emerald-700' : 'text-red-700'">
          {{ health ? (health.ok ? '当前核心检查正常' : '存在需要处理的配置或服务问题') : '正在读取系统状态' }}
        </p>
      </div>
      <button
        @click="loadHealth"
        :disabled="loading"
        class="inline-flex h-9 items-center gap-2 rounded border border-slate-200 bg-white px-3 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        重新检查
      </button>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <section class="mb-5 rounded-lg border border-slate-200 bg-white" data-testid="readiness">
      <div class="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <p class="text-sm font-semibold text-slate-900">运行就绪</p>
        <p class="text-xs" :class="readiness?.ok ? 'text-emerald-700' : 'text-red-700'">{{ readiness ? (readiness.ok ? '各环节都已就绪' : '有环节没起来，按提示处理') : '检查中…' }}</p>
      </div>
      <ul class="divide-y divide-slate-100">
        <li v-for="c in readiness?.checks || []" :key="c.key" class="flex items-start gap-3 px-4 py-2.5" :data-testid="`readiness-${c.key}`">
          <component :is="c.ok ? CheckCircle2 : AlertTriangle" :size="16" class="mt-0.5 shrink-0"
            :class="c.level === 'error' ? 'text-red-500' : c.level === 'warn' ? 'text-amber-500' : 'text-emerald-500'" />
          <div class="min-w-0 text-sm">
            <p class="font-medium text-slate-800">{{ c.label }}</p>
            <p class="text-xs text-slate-500">{{ c.message }}</p>
            <p v-if="c.fix" class="text-xs text-indigo-600">处理：{{ c.fix }}</p>
          </div>
        </li>
      </ul>
    </section>

    <section class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <article class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between">
          <p class="text-xs text-slate-500">数据库连接池</p>
          <Database :size="17" class="text-sky-500" />
        </div>
        <p class="mt-2 text-xl font-semibold text-slate-900">{{ pool.checked_out ?? 0 }} / {{ pool.size ?? '-' }}</p>
        <p class="mt-1 text-xs text-slate-400">已借出 / 基础连接数，溢出 {{ pool.overflow ?? 0 }}</p>
      </article>

      <article class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between">
          <p class="text-xs text-slate-500">Redis 状态</p>
          <Server :size="17" :class="redisOk ? 'text-emerald-500' : 'text-amber-500'" />
        </div>
        <p class="mt-2 text-xl font-semibold" :class="redisOk ? 'text-emerald-700' : 'text-amber-700'">
          {{ redisOk ? '正常' : '内存兜底' }}
        </p>
        <p class="mt-1 text-xs text-slate-400">缓存、验证码、限流会自动尝试恢复 Redis</p>
      </article>

      <article class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between">
          <p class="text-xs text-slate-500">任务模式</p>
          <Workflow :size="17" class="text-indigo-500" />
        </div>
        <p class="mt-2 text-xl font-semibold text-slate-900">{{ health?.tasks?.execution_mode || '-' }}</p>
        <p class="mt-1 text-xs text-slate-400">{{ health?.tasks?.worker_required ? '需要独立 Worker 处理队列' : 'API 响应后执行后台任务' }}</p>
      </article>

      <article class="rounded-lg border border-slate-200 bg-white p-4">
        <div class="flex items-center justify-between">
          <p class="text-xs text-slate-500">生产配置</p>
          <ShieldCheck :size="17" :class="health?.config?.ok ? 'text-emerald-500' : 'text-red-500'" />
        </div>
        <p class="mt-2 text-xl font-semibold" :class="health?.config?.ok ? 'text-emerald-700' : 'text-red-700'">
          {{ health?.config?.environment || '-' }}
        </p>
        <p class="mt-1 text-xs text-slate-400">错误 {{ health?.config?.error_count ?? 0 }}，警告 {{ health?.config?.warning_count ?? 0 }}</p>
      </article>
    </section>

    <section class="mt-5 grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
      <article class="rounded-lg border border-slate-200 bg-white">
        <div class="border-b border-slate-100 px-4 py-3">
          <p class="text-sm font-semibold text-slate-900">检查项</p>
        </div>
        <div class="divide-y divide-slate-100">
          <div v-for="item in health?.checks || []" :key="item.name" class="flex items-start gap-3 px-4 py-3">
            <CheckCircle2 v-if="item.ok" :size="16" class="mt-0.5 text-emerald-500" />
            <AlertTriangle v-else :size="16" class="mt-0.5 text-red-500" />
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium text-slate-800">{{ item.name }}</p>
              <p class="mt-0.5 text-xs text-slate-500">{{ item.message || '-' }}</p>
            </div>
          </div>
          <div v-if="!health?.checks?.length && !loading" class="py-12 text-center text-sm text-slate-500">暂无诊断数据。</div>
        </div>
      </article>

      <div class="space-y-4">
        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="mb-3 text-sm font-semibold text-slate-900">缓存与限流</p>
          <div class="space-y-2 text-xs text-slate-600">
            <div class="flex items-center justify-between"><span>模型配置缓存</span><span>{{ backendText(health?.cache?.config?.backend) }} · {{ health?.cache?.config?.size ?? 0 }} 条</span></div>
            <div class="flex items-center justify-between"><span>能力配置缓存</span><span>{{ backendText(health?.cache?.skill?.backend) }} · {{ health?.cache?.skill?.size ?? 0 }} 条</span></div>
            <div class="flex items-center justify-between"><span>验证码缓存</span><span>{{ backendText(health?.cache?.verification?.backend) }} · {{ health?.cache?.verification?.size ?? 0 }} 条</span></div>
            <div class="flex items-center justify-between"><span>请求限流</span><span>{{ backendText(health?.limits?.rate?.backend) }}</span></div>
            <div class="flex items-center justify-between"><span>并发控制</span><span>{{ backendText(health?.limits?.concurrency?.backend) }}</span></div>
          </div>
        </article>

        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="mb-3 text-sm font-semibold text-slate-900">Worker 策略</p>
          <div class="space-y-2 text-xs text-slate-600">
            <div class="flex items-center justify-between"><span>运行超时</span><span>{{ health?.tasks?.running_timeout_seconds ?? '-' }} 秒</span></div>
            <div class="flex items-center justify-between"><span>自动重试</span><span>{{ health?.tasks?.max_auto_retries ?? 0 }} 次</span></div>
            <div class="flex items-center justify-between"><span>初始退避</span><span>{{ health?.tasks?.retry_base_seconds ?? '-' }} 秒</span></div>
            <div class="flex items-center justify-between"><span>最大退避</span><span>{{ health?.tasks?.retry_max_seconds ?? '-' }} 秒</span></div>
          </div>
        </article>
      </div>
    </section>

    <section class="mt-5 rounded-lg border border-slate-200 bg-white" data-testid="reminder-rules">
      <div class="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <p class="text-sm font-semibold text-slate-900">业务提醒规则</p>
        <p class="text-xs text-slate-400">由后台 Worker 定时运行；连续失败 3 次会通知企业管理员</p>
      </div>
      <p v-if="reminderError" class="px-4 py-2 text-xs text-red-600">{{ reminderError }}</p>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[640px] text-left text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
            <tr><th class="px-4 py-2 font-medium">规则</th><th class="px-4 py-2 font-medium">上次运行</th><th class="px-4 py-2 font-medium">结果</th><th class="px-4 py-2 font-medium">下次运行</th><th class="px-4 py-2 font-medium"></th></tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            <tr v-for="r in reminderRules" :key="r.rule">
              <td class="px-4 py-2.5"><p class="font-medium text-slate-800">{{ r.label }}</p><p class="text-xs text-slate-500">{{ r.description }} · 每 {{ r.interval_minutes }} 分钟</p></td>
              <td class="px-4 py-2.5 text-xs text-slate-600">{{ fmt(r.last_finished_at) }}</td>
              <td class="px-4 py-2.5 text-xs">
                <span v-if="r.last_status === 'ok'" class="text-emerald-700">正常 · 新增 {{ r.last_created }} · 自动关闭 {{ r.last_resolved }}</span>
                <span v-else-if="r.last_status === 'failed'" class="text-red-600" :title="r.last_error || ''">失败（连续 {{ r.consecutive_failures }} 次）：{{ r.last_error }}</span>
                <span v-else class="text-slate-400">尚未运行</span>
              </td>
              <td class="px-4 py-2.5 text-xs text-slate-600">{{ fmt(r.next_run_at) }}</td>
              <td class="px-4 py-2.5 text-right">
                <button class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  :disabled="runningRule === r.rule" @click="runRule(r.rule)">{{ runningRule === r.rule ? '运行中…' : '立即运行' }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, CheckCircle2, Database, RefreshCcw, Server, ShieldCheck, Workflow } from 'lucide-vue-next'
import { getDiagnose, getReadiness, type HealthStatus, type ReadinessItem } from '../../api/system'
import { getErrorMessage } from '../../utils/request'
import { getReminderStatus, runReminder, type ReminderRuleStatus } from '../../api/workCenter'

const health = ref<HealthStatus | null>(null)
const loading = ref(false)
const errorMsg = ref('')

const pool = computed(() => health.value?.database?.pool || {})
const redisOk = computed(() => {
  const cache = health.value?.cache
  return !!(
    cache?.config?.redis_ok ||
    cache?.skill?.redis_ok ||
    cache?.verification?.redis_ok ||
    health.value?.limits?.rate?.redis_ok ||
    health.value?.limits?.concurrency?.redis_ok
  )
})

const backendText = (value?: string) => value === 'redis' ? 'Redis' : '内存'

const readiness = ref<{ ok: boolean; checks: ReadinessItem[] } | null>(null)

const loadHealth = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    readiness.value = await getReadiness().catch(() => null)
    health.value = await getDiagnose()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '系统诊断加载失败')
  } finally {
    loading.value = false
  }
}

const reminderRules = ref<ReminderRuleStatus[]>([])
const reminderError = ref('')
const runningRule = ref('')
const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString('zh-CN') : '—')
const loadReminders = async () => {
  try { reminderRules.value = await getReminderStatus() } catch (e) { reminderError.value = getErrorMessage(e, '提醒规则状态加载失败') }
}
const runRule = async (rule: string) => {
  runningRule.value = rule; reminderError.value = ''
  try {
    const result = await runReminder(rule)
    if (result.status !== 'ok') reminderError.value = result.error || '运行失败'
    await loadReminders()
  } catch (e) { reminderError.value = getErrorMessage(e, '运行失败') } finally { runningRule.value = '' }
}

onMounted(() => { void loadHealth(); void loadReminders() })
</script>
