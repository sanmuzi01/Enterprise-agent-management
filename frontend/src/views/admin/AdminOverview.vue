<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-5">
      <p class="text-xs text-slate-500">用户、任务和系统运行状态总览</p>
      <button
        @click="loadAll"
        :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <!-- 统计卡片 -->
    <section class="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-7">
      <article v-for="item in countItems" :key="item.key" class="rounded-lg border border-slate-200 bg-white p-4">
        <p class="text-xs text-slate-500">{{ item.label }}</p>
        <p class="mt-2 text-2xl font-semibold text-slate-900">{{ item.value }}</p>
      </article>
    </section>

    <div class="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(560px,1fr)_420px]">
      <!-- 最近后台任务 -->
      <section class="rounded-lg border border-slate-200 bg-white">
        <div class="flex h-12 items-center justify-between border-b border-slate-200 px-4">
          <h2 class="text-sm font-semibold text-slate-900">最近后台任务</h2>
          <RouterLink to="/admin/tasks" class="text-xs text-indigo-600 hover:underline">查看全部 →</RouterLink>
        </div>
        <div class="divide-y divide-slate-100">
          <article v-for="task in tasks" :key="task.id" class="grid gap-3 px-4 py-3 text-sm md:grid-cols-[1fr_120px_90px] md:items-center">
            <div class="min-w-0">
              <p class="truncate font-medium text-slate-900">{{ task.title }}</p>
              <p class="mt-1 text-xs text-slate-400">用户 {{ task.user_id }} · 助手 {{ task.agent_id ?? '-' }} · {{ task.created_at || '-' }}</p>
              <p v-if="task.error_msg" class="mt-1 line-clamp-2 text-xs text-red-600">{{ task.error_msg }}</p>
            </div>
            <div class="h-1.5 rounded bg-slate-200">
              <div class="h-1.5 rounded bg-blue-500" :style="{ width: `${task.progress || 0}%` }"></div>
            </div>
            <span :class="taskStatusClass(task.status)" class="w-fit rounded px-2 py-1 text-xs">
              {{ taskStatusText(task.status) }}
            </span>
          </article>
          <div v-if="!tasks.length && !loading" class="py-12 text-center text-sm text-slate-500">暂无后台任务。</div>
        </div>
      </section>

      <!-- 右侧状态卡 -->
      <section class="space-y-5">
        <RouterLink
          to="/admin/integrations"
          class="group block rounded-lg border border-indigo-200 bg-indigo-50/60 p-4 transition-colors hover:border-indigo-300 hover:bg-indigo-50"
        >
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 class="text-sm font-semibold text-slate-900">飞书 / 钉钉接入</h2>
              <p class="mt-1 text-xs leading-5 text-slate-500">配置机器人、事件回调、组织架构同步和员工账号绑定。</p>
            </div>
            <MessagesSquare :size="18" class="mt-0.5 shrink-0 text-indigo-500" />
          </div>
          <p class="mt-3 text-xs font-medium text-indigo-600 group-hover:text-indigo-700">进入接入配置 →</p>
        </RouterLink>

        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-slate-900">任务状态</h2>
            <Activity :size="16" class="text-slate-400" />
          </div>
          <div class="space-y-2">
            <div v-for="item in taskStatusItems" :key="item.key" class="flex items-center justify-between text-sm">
              <span class="text-slate-500">{{ item.label }}</span>
              <span class="font-semibold text-slate-900">{{ item.value }}</span>
            </div>
          </div>
        </article>

        <article class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-slate-900">知识库状态</h2>
            <Database :size="16" class="text-slate-400" />
          </div>
          <div class="space-y-2">
            <div v-for="item in knowledgeStatusItems" :key="item.key" class="flex items-center justify-between text-sm">
              <span class="text-slate-500">{{ item.label }}</span>
              <span class="font-semibold text-slate-900">{{ item.value }}</span>
            </div>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Activity, Database, MessagesSquare, RefreshCcw } from 'lucide-vue-next'
import * as adminApi from '../../api/admin'
import type { AdminOverview, AdminTask } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const overview = ref<AdminOverview | null>(null)
const tasks = ref<AdminTask[]>([])
const loading = ref(false)
const errorMsg = ref('')

const countLabels: Record<string, string> = {
  users: '用户',
  online_users: '在线用户',
  agents: '助手',
  skills: '能力',
  llm_configs: '模型连接',
  knowledge_docs: '文档',
  background_tasks: '后台任务',
}

const countItems = computed(() => Object.entries(countLabels).map(([key, label]) => ({
  key,
  label,
  value: overview.value?.counts?.[key] ?? 0,
})))

const taskStatusItems = computed(() => statusItems(overview.value?.task_status || {}))
const knowledgeStatusItems = computed(() => statusItems(overview.value?.knowledge_status || {}))

const statusItems = (data: Record<string, number>) => {
  const labels: Record<string, string> = {
    queued: '排队中', running: '处理中', finished: '已完成', failed: '失败',
    pending: '待处理', processing: '处理中', done: '已完成',
    cancelled: '已取消',
  }
  const keys = Object.keys(data)
  if (!keys.length) return [{ key: 'empty', label: '暂无数据', value: 0 }]
  return keys.map((key) => ({ key, label: labels[key] || key, value: data[key] }))
}

const taskStatusText = (s: string) => ({
  queued: '排队中', running: '处理中', finished: '已完成', failed: '失败', cancelled: '已取消',
}[s] || s)

const taskStatusClass = (s: string) => ({
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-50 text-blue-700',
  finished: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-amber-50 text-amber-700',
}[s] || 'bg-slate-100 text-slate-600')

const loadAll = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const [overviewData, taskData] = await Promise.all([
      adminApi.getAdminOverview(),
      adminApi.listAdminTasks(10),
    ])
    overview.value = overviewData
    tasks.value = taskData
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '读取概览数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>
