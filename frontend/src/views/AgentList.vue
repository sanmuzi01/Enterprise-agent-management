<template>
  <div class="flex h-screen flex-col bg-transparent">
    <header class="ui-glass relative z-10 shrink-0 border-b px-5 py-4 lg:flex lg:items-end lg:justify-between lg:px-10 lg:pb-3.5 lg:pt-6">
      <div class="min-w-0">
        <h1 class="text-[30px] font-bold leading-tight tracking-[-0.032em] text-slate-900">工作台</h1>
        <p class="mt-0.5 truncate text-[14px] text-slate-500">{{ workspaceSummary }}</p>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2 lg:mt-0">
        <button
          data-guide="create-agent"
          @click="openCreateDialog"
          class="ui-primary inline-flex h-9 items-center gap-1.5 px-4 text-[13px] font-medium"
        >
          <Plus :size="15" :stroke-width="2" />
          新建助手
        </button>
        <button
          @click="userStore.logout"
          class="inline-flex h-9 w-9 items-center justify-center rounded-full bg-black/[.05] text-slate-500 hover:bg-black/[.09] hover:text-slate-900 md:hidden"
          title="退出登录"
          aria-label="退出登录"
        >
          <LogOut :size="15" />
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto px-5 py-6 lg:px-10">
      <div class="mx-auto max-w-7xl">
        <div v-if="loading" class="py-20 text-center text-sm text-slate-500">加载中...</div>

        <div v-else-if="loadError" class="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
          <div class="flex items-center justify-between gap-3">
            <span>{{ loadError }}</span>
            <button @click="reload" class="shrink-0 rounded border border-red-200 bg-white px-3 py-1.5 text-xs hover:bg-red-100">
              重试
            </button>
          </div>
        </div>

        <div v-else class="space-y-5">
          <section class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <article class="ui-card rounded-lg p-4">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-500">系统健康</span>
                  <Activity :size="16" class="text-slate-400" />
                </div>
                <p class="mt-3 text-[34px] font-semibold leading-none tracking-[-0.032em] tabular-nums text-slate-900">{{ dashboard?.status.health_score ?? setupProgress }}</p>
                <p class="mt-1 text-xs text-slate-500">{{ healthText }}</p>
                <div class="mt-3 h-1.5 rounded bg-sky-100">
                  <div class="h-1.5 rounded bg-sky-500 transition-all" :style="{ width: `${dashboard?.status.health_score ?? setupProgress}%` }"></div>
                </div>
              </article>

              <article class="ui-card rounded-lg p-4">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-500">可用助手</span>
                  <Bot :size="16" class="text-slate-400" />
                </div>
                <p class="mt-3 text-[34px] font-semibold leading-none tracking-[-0.032em] tabular-nums text-slate-900">{{ dashboard?.counts.ready_agents ?? readyAgentCount }}</p>
                <p class="mt-1 text-xs text-slate-500">共 {{ dashboard?.counts.agents ?? agents.length }} 个助手</p>
              </article>

              <article class="ui-card rounded-lg p-4">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-500">知识库文档</span>
                  <Database :size="16" class="text-slate-400" />
                </div>
                <p class="mt-3 text-[34px] font-semibold leading-none tracking-[-0.032em] tabular-nums text-slate-900">{{ dashboard?.counts.knowledge_done ?? 0 }}</p>
                <p class="mt-1 text-xs text-slate-500">已入库 / 共 {{ dashboard?.counts.knowledge_docs ?? 0 }} 份</p>
              </article>

              <article class="ui-card rounded-lg p-4">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-500">{{ quota && !quota.unlimited ? '本月用量' : '累计消耗' }}</span>
                  <Gauge :size="16" class="text-slate-400" />
                </div>
                <p class="mt-3 text-[34px] font-semibold leading-none tracking-[-0.032em] tabular-nums text-slate-900">
                  {{ quota && !quota.unlimited ? formatNumber(quota.used_tokens) : formatNumber(dashboard?.counts.tokens ?? 0) }}
                </p>
                <p v-if="quota && !quota.unlimited" class="mt-1 text-xs text-slate-500">
                  {{ quota.plan_display_name }} · 配额 {{ formatNumber(quota.monthly_token_limit) }}
                  <button @click="exportUsageReport" class="ml-1 text-[var(--accent)] hover:underline">导出</button>
                </p>
                <p v-else class="mt-1 text-xs text-slate-500">
                  {{ quota ? `${quota.plan_display_name} · 不限量` : '按模型调用记录累计' }}
                  <button v-if="quota" @click="exportUsageReport" class="ml-1 text-[var(--accent)] hover:underline">导出</button>
                </p>
                <div v-if="quota && !quota.unlimited" class="mt-3 h-1.5 rounded-full bg-slate-100">
                  <div
                    class="h-1.5 rounded transition-all"
                    :class="quotaUsagePercent >= 90 ? 'bg-red-500' : quotaUsagePercent >= 70 ? 'bg-amber-500' : 'bg-[var(--accent)]'"
                    :style="{ width: `${quotaUsagePercent}%` }"
                  ></div>
                </div>
              </article>
            </div>

            <aside class="ui-card rounded-lg p-4">
              <div class="flex items-center justify-between">
                <div>
                  <h2 class="text-sm font-semibold text-slate-950">下一步建议</h2>
                  <p class="mt-1 text-xs text-slate-500">按上线可用性自动排序</p>
                </div>
                <Sparkles :size="17" class="text-sky-500" />
              </div>
              <div v-if="dashboard?.recommendations.length" class="mt-4 space-y-2">
                <button
                  v-for="item in dashboard.recommendations"
                  :key="item.key"
                  @click="router.push(item.action_path)"
                  class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] w-full rounded-lg border bg-white/76 p-3 text-left transition hover:-translate-y-0.5 hover:bg-sky-50"
                  :class="recommendationClass(item.level)"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="text-sm font-semibold text-slate-900">{{ item.title }}</span>
                    <span class="shrink-0 rounded bg-white px-2 py-0.5 text-xs text-slate-500">{{ item.action_text }}</span>
                  </div>
                  <p class="mt-1 text-xs leading-5 text-slate-500">{{ item.description }}</p>
                </button>
              </div>
              <div v-else class="mt-5 rounded-lg border border-emerald-200 bg-emerald-50/75 p-4 text-sm text-emerald-800">
                关键配置已经就绪，可以继续沉淀资料、创建能力或让多个助手协作。
              </div>
            </aside>
          </section>

          <details class="group rounded-lg border border-sky-200/70 bg-white/55">
            <summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-slate-600 [&::-webkit-details-marker]:hidden">
              <span class="text-sky-500 transition-transform group-open:rotate-90">▸</span>
              更多面板：工作台组件 · 小窗口
              <span class="ml-auto text-xs font-normal text-slate-400">点开展开</span>
            </summary>
            <div class="space-y-5 border-t border-sky-100 p-4">

          <section>
            <div class="mb-3 flex items-center justify-between">
              <div>
                <h2 class="text-sm font-semibold text-slate-950">我的工作台模块</h2>
                <p class="mt-1 text-xs text-slate-500">按自己的使用习惯显示模块，后续能力可以继续加到这里</p>
              </div>
              <button
                @click="showCustomizePanel = true"
                class="inline-flex items-center gap-2 rounded border border-sky-200 bg-white/80 px-3 py-1.5 text-xs text-slate-600 hover:bg-sky-50"
              >
                <SlidersHorizontal :size="14" />
                添加 / 隐藏
              </button>
            </div>
            <div v-if="visibleFeatureModules.length" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
              <button
                v-for="module in visibleFeatureModules"
                :key="module.key"
                @click="router.push(module.path)"
                class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] ui-card rounded-lg p-4 text-left transition hover:-translate-y-0.5"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="inline-flex h-9 w-9 items-center justify-center rounded bg-white text-sky-600 shadow-sm">
                    <component :is="module.icon" :size="17" />
                  </span>
                  <span class="rounded px-2 py-0.5 text-xs" :class="module.stateClass">{{ module.state }}</span>
                </div>
                <h3 class="mt-4 text-sm font-semibold text-slate-950">{{ module.title }}</h3>
                <p class="mt-2 text-lg font-semibold text-slate-900">{{ module.value }}</p>
                <p class="mt-1 min-h-10 text-xs leading-5 text-slate-500">{{ module.desc }}</p>
              </button>
            </div>
            <div v-else class="rounded-lg border border-dashed border-sky-200 bg-white/60 py-10 text-center text-sm text-slate-500">
              你隐藏了所有模块，可以重新添加常用功能。
            </div>
          </section>

          <section>
            <div class="mb-3 flex items-center justify-between">
              <div>
                <h2 class="text-sm font-semibold text-slate-950">我的小窗口</h2>
                <p class="mt-1 text-xs text-slate-500">把高频操作和监控信息放在工作台首页</p>
              </div>
            </div>
            <div v-if="visibleWorkspaceWidgets.length" class="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <article
                v-for="widget in visibleWorkspaceWidgets"
                :key="widget.id"
                class="ui-card rounded-lg p-4"
              >
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-950">{{ widget.title }}</h3>
                    <p class="mt-1 text-xs text-slate-500">{{ widgetDescription(widget.type) }}</p>
                  </div>
                  <button
                    @click="toggleWorkspaceWidget(widget.id)"
                    class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                    title="隐藏"
                  >
                    <X :size="14" />
                  </button>
                </div>
                <div class="mt-4">
                  <div v-if="widget.type === 'quick-actions'" class="grid grid-cols-2 gap-2">
                    <button @click="openCreateDialog" class="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-sky-50">新建助手</button>
                    <button @click="router.push('/skills')" class="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-sky-50">添加能力</button>
                    <button @click="router.push('/web-monitor')" class="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-sky-50">监控网页</button>
                    <button @click="router.push('/llm-configs')" class="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-sky-50">连接服务</button>
                  </div>
                  <div v-else-if="widget.type === 'web-monitor'" class="rounded border border-sky-100 bg-sky-50/70 p-3">
                    <p class="text-sm font-medium text-slate-900">网页监控已独立成模块</p>
                    <p class="mt-1 text-xs leading-5 text-slate-500">可以添加网页地址，手动检查变化，并保存最近状态。</p>
                    <button @click="router.push('/web-monitor')" class="mt-3 rounded bg-white px-3 py-1.5 text-xs text-sky-700 hover:bg-sky-100">打开网页监控</button>
                  </div>
                  <div v-else-if="widget.type === 'chart-output'" class="rounded border border-violet-100 bg-violet-50/70 p-3">
                    <p class="text-sm font-medium text-slate-900">图表输出已准备</p>
                    <p class="mt-1 text-xs leading-5 text-slate-500">在技能中心绑定图表工具后，用户可以直接要求助手输出柱状图、折线图或饼图。</p>
                    <button @click="router.push('/skills')" class="mt-3 rounded bg-white px-3 py-1.5 text-xs text-violet-700 hover:bg-violet-100">去技能中心</button>
                  </div>
                  <div v-else-if="widget.type === 'recent-tasks'" class="space-y-2">
                    <div v-for="task in dashboard?.recent_tasks.slice(0, 3) || []" :key="task.id" class="rounded border border-slate-100 bg-white/70 p-2">
                      <div class="flex items-center justify-between gap-2">
                        <span class="truncate text-xs font-medium text-slate-800">{{ task.title || taskTypeLabel(task.task_type) }}</span>
                        <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="statusClass(task.status)">{{ statusText(task.status) }}</span>
                      </div>
                    </div>
                    <p v-if="!dashboard?.recent_tasks.length" class="text-sm text-slate-500">暂无后台任务。</p>
                  </div>
                  <div v-else-if="widget.type === 'recent-runs'" class="space-y-2">
                    <div v-for="run in dashboard?.recent_runs.slice(0, 3) || []" :key="run.id" class="rounded border border-slate-100 bg-white/70 p-2">
                      <p class="truncate text-xs font-medium text-slate-800">{{ run.question || '未记录输入' }}</p>
                      <p class="mt-1 truncate text-xs text-slate-500">{{ run.agent_name }} · {{ statusText(run.status) }}</p>
                    </div>
                    <p v-if="!dashboard?.recent_runs.length" class="text-sm text-slate-500">暂无运行记录。</p>
                  </div>
                  <div v-else class="rounded border border-slate-100 bg-white/70 p-3 text-sm text-slate-500">
                    这是自定义小窗口，可以在后续绑定具体 Skill 或外部数据源。
                  </div>
                </div>
              </article>
            </div>
            <div v-else class="rounded-lg border border-dashed border-sky-200 bg-white/60 py-8 text-center text-sm text-slate-500">
              还没有显示中的小窗口，可以在“自定义工作台”里添加。
            </div>
          </section>

            </div>
          </details>

          <section v-if="setupProgress < 100" class="ui-card p-6">
            <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
              <div>
                <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p class="text-xs font-medium text-[var(--accent)]">开始使用</p>
                    <h2 class="mt-1 text-2xl font-semibold text-slate-950">{{ setupTitle }}</h2>
                    <p class="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{{ setupDescription }}</p>
                  </div>
                  <button
                    @click="runPrimaryAction"
                    class="ui-primary inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded px-4 text-sm font-medium text-white"
                  >
                    <component :is="primaryActionIcon" :size="16" />
                    {{ primaryActionText }}
                  </button>
                </div>

                <div class="mt-5 grid gap-3 md:grid-cols-2">
                  <button
                    v-for="item in setupItems"
                    :key="item.key"
                    @click="item.action"
                    class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] flex items-start gap-3 rounded-lg border p-4 text-left transition hover:-translate-y-0.5 hover:bg-sky-50/80"
                    :class="item.done ? 'border-emerald-200 bg-emerald-50/75' : item.active ? 'border-sky-300 bg-sky-50/85' : 'border-slate-200 bg-white/72'"
                  >
                    <span :class="item.done ? 'text-emerald-700' : item.active ? 'text-sky-700' : 'text-slate-500'" class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded bg-white shadow-sm">
                      <component :is="item.icon" :size="18" />
                    </span>
                    <span class="min-w-0 flex-1">
                      <span class="flex items-center justify-between gap-2">
                        <span class="block text-sm font-semibold text-slate-900">{{ item.title }}</span>
                        <span :class="item.done ? 'bg-emerald-100 text-emerald-700' : item.active ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-500'" class="shrink-0 rounded px-2 py-0.5 text-xs">
                          {{ item.done ? '完成' : item.active ? '当前' : '待办' }}
                        </span>
                      </span>
                      <span class="mt-1 block text-xs leading-5 text-slate-500">{{ item.desc }}</span>
                    </span>
                  </button>
                </div>
              </div>

              <aside class="flex flex-col justify-between rounded-[20px] bg-slate-50 p-5">
                <div class="flex items-center gap-4">
                  <div class="relative h-[84px] w-[84px] shrink-0">
                    <svg viewBox="0 0 100 100" class="h-full w-full -rotate-90">
                      <circle cx="50" cy="50" r="42" fill="none" stroke-width="9" class="[stroke:var(--sunken)]" />
                      <circle
                        cx="50" cy="50" r="42" fill="none" stroke-width="9" stroke-linecap="round"
                        class="[stroke:var(--accent)] transition-all duration-700"
                        stroke-dasharray="263.9"
                        :stroke-dashoffset="263.9 * (1 - setupProgress / 100)"
                      />
                    </svg>
                    <span class="absolute inset-0 flex items-center justify-center text-[22px] font-semibold tracking-[-0.03em] tabular-nums">{{ setupProgress }}%</span>
                  </div>
                  <div>
                    <p class="text-sm font-semibold text-slate-900">完成度</p>
                    <p class="mt-0.5 text-xs leading-5 text-slate-500">完成下面几步，助手就能正常工作。</p>
                  </div>
                </div>
                <div class="mt-5 grid grid-cols-2 gap-2 text-xs">
                  <span class="rounded-lg bg-white px-2.5 py-1.5 text-slate-500 shadow-[var(--sh-1)]">模型 {{ hasChatKey ? '已连接' : '未连接' }}</span>
                  <span class="rounded-lg bg-white px-2.5 py-1.5 text-slate-500 shadow-[var(--sh-1)]">助手 {{ agents.length }}</span>
                  <span class="rounded-lg bg-white px-2.5 py-1.5 text-slate-500 shadow-[var(--sh-1)]">默认 {{ selectedAgentName || '未设' }}</span>
                  <span class="rounded-lg bg-white px-2.5 py-1.5 text-slate-500 shadow-[var(--sh-1)]">资料 {{ hasEmbeddingKey ? '可用' : '待配置' }}</span>
                </div>
              </aside>
            </div>
          </section>

          <section class="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <article class="ui-card rounded-lg p-4">
              <div class="mb-3 flex items-center justify-between">
                <div>
                  <h2 class="text-sm font-semibold text-slate-950">最近运行</h2>
                  <p class="mt-1 text-xs text-slate-500">用于快速发现失败、耗时或配置问题</p>
                </div>
                <button @click="router.push('/tasks')" class="rounded border border-sky-200 bg-white/80 px-3 py-1.5 text-xs text-slate-600 hover:bg-sky-50">
                  任务中心
                </button>
              </div>
              <div v-if="dashboard?.recent_runs.length" class="divide-y divide-slate-100">
                <button
                  v-for="run in dashboard.recent_runs"
                  :key="run.id"
                  @click="router.push(`/agents/${run.agent_id}/debug`)"
                  class="block w-full py-3 text-left"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="truncate text-sm font-medium text-slate-900">{{ run.question || '未记录输入' }}</span>
                    <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="statusClass(run.status)">
                      {{ statusText(run.status) }}
                    </span>
                  </div>
                  <p class="mt-1 truncate text-xs text-slate-500">{{ run.agent_name }} · {{ run.total_steps }} 步 · 约 {{ formatNumber(run.total_tokens) }} 字符消耗</p>
                </button>
              </div>
              <div v-else class="rounded-lg border border-dashed border-slate-200 bg-white/60 py-8 text-center text-sm text-slate-500">
                还没有运行记录，开始一次对话后这里会显示检查线索。
              </div>
            </article>

            <article class="ui-card rounded-lg p-4">
              <div class="mb-3 flex items-center justify-between">
                <div>
                  <h2 class="text-sm font-semibold text-slate-950">后台任务</h2>
                  <p class="mt-1 text-xs text-slate-500">文档入库、网页抓取、索引重建都在这里体现</p>
                </div>
                <ListChecks :size="17" class="text-sky-500" />
              </div>
              <div v-if="dashboard?.recent_tasks.length" class="space-y-3">
                <div v-for="task in dashboard.recent_tasks" :key="task.id" class="rounded-lg border border-slate-100 bg-white/72 p-3">
                  <div class="flex items-center justify-between gap-3">
                    <span class="truncate text-sm font-medium text-slate-900">{{ task.title || taskTypeLabel(task.task_type) }}</span>
                    <span class="shrink-0 rounded px-2 py-0.5 text-xs" :class="statusClass(task.status)">
                      {{ statusText(task.status) }}
                    </span>
                  </div>
                  <div class="mt-2 h-1.5 rounded bg-slate-100">
                    <div class="h-1.5 rounded bg-sky-500 transition-all" :style="{ width: `${Math.max(0, Math.min(100, task.progress || 0))}%` }"></div>
                  </div>
                  <p v-if="task.error_msg" class="mt-2 truncate text-xs text-red-600">{{ task.error_msg }}</p>
                </div>
              </div>
              <div v-else class="rounded-lg border border-dashed border-slate-200 bg-white/60 py-8 text-center text-sm text-slate-500">
                暂无后台任务。上传文档或抓取网页后会出现处理进度。
              </div>
            </article>
          </section>

          <section>
            <div class="mb-3 flex items-center justify-between">
              <div>
                <h2 class="text-sm font-semibold text-slate-950">{{ selectedAgent ? '其他助手' : '我的助手' }}</h2>
                <p class="mt-1 text-xs text-slate-500">{{ agentListHint }}</p>
              </div>
            </div>

            <div v-if="agents.length === 0" class="rounded-lg border border-dashed border-sky-300/70 bg-white/62 py-12 text-center backdrop-blur">
              <Bot :size="34" class="mx-auto mb-3 text-sky-300" />
              <p class="text-sm font-medium text-slate-800">还没有助手</p>
              <p class="mt-1 text-sm text-slate-500">先连接模型，再创建一个能聊天的助手。</p>
            </div>

            <div v-else-if="floatingAgentList.length === 0" class="rounded-lg border border-dashed border-sky-200 bg-white/60 py-8 text-center text-sm text-slate-500">
              当前只有一个助手，已经固定到右上角浮窗。
            </div>

            <div v-else class="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="agent in floatingAgentList"
            :key="agent.id"
            class="ui-card p-5 transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)]"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded bg-blue-50 text-blue-700">
                    <Bot :size="18" />
                  </span>
                  <div class="min-w-0">
                    <h2 class="truncate text-sm font-semibold text-slate-900">{{ agent.name }}</h2>
                    <p class="truncate text-xs text-slate-500">{{ modelDisplayName(agent.model_name) }} · 温度 {{ agent.temperature }}</p>
                  </div>
                </div>
              </div>
              <span
                v-if="agent.is_selected"
                class="shrink-0 rounded bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700"
              >
                默认
              </span>
            </div>

            <div class="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div class="rounded border border-slate-100 px-2 py-2">
                <span class="text-slate-400">模型连接</span>
                <p :class="hasModelKey(agent.model_name || '') ? 'text-emerald-700' : 'text-amber-600'" class="mt-1 font-medium">
                  {{ hasModelKey(agent.model_name || '') ? '已配置' : '待配置' }}
                </p>
              </div>
              <div class="rounded border border-slate-100 px-2 py-2">
                <span class="text-slate-400">资料检索</span>
                <p :class="ragStatusClass(agent)" class="mt-1 font-medium">
                  {{ ragStatusText(agent) }}
                </p>
              </div>
            </div>

            <div class="mt-4 min-h-7">
              <div v-if="agent.skills?.length" class="flex flex-wrap gap-1.5">
                <span
                  v-for="skill in agent.skills"
                  :key="skill.id"
                  class="rounded bg-violet-50 px-2 py-1 text-xs text-violet-700"
                >
                  {{ skill.name }}
                </span>
              </div>
              <span v-else class="text-xs text-slate-400">未添加能力</span>
            </div>

            <div class="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
              <div class="flex gap-1">
                <button
                  @click="enterChat(agent.id)"
                  :disabled="!hasModelKey(agent.model_name || '') || (agent.rag_enabled === 1 && !hasEmbeddingKey)"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-blue-50 hover:text-blue-700"
                    title="开始聊天"
                >
                  <MessageSquare :size="15" />
                </button>
                <button
                  @click="router.push(`/agents/${agent.id}/knowledge`)"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-emerald-50 hover:text-emerald-700"
                    title="添加资料"
                >
                  <BookOpen :size="15" />
                </button>
                <button
                  @click="router.push(`/agents/${agent.id}/debug`)"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-amber-50 hover:text-amber-700"
                  title="运行检查"
                >
                  <Bug :size="15" />
                </button>
                  <button
                    v-if="!hasModelKey(agent.model_name || '') || (agent.rag_enabled === 1 && !hasEmbeddingKey)"
                    @click="router.push('/llm-configs')"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-amber-500 hover:bg-amber-50 hover:text-amber-700"
                  title="连接模型"
                  >
                    <KeyRound :size="15" />
                  </button>
                <button
                  @click="openEditDialog(agent)"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                  title="编辑"
                >
                  <Pencil :size="15" />
                </button>
                <button
                  @click="cloneAgent(agent)"
                  :disabled="cloningId === agent.id"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-indigo-50 hover:text-indigo-700 disabled:text-slate-300"
                  title="复制"
                >
                  <Copy :size="15" />
                </button>
                <button
                  @click="handleDelete(agent)"
                  class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-red-50 hover:text-red-600"
                  title="删除"
                >
                  <Trash2 :size="15" />
                </button>
              </div>
              <button
                @click="selectAgent(agent)"
                :disabled="agent.is_selected || selectingId === agent.id"
                class="rounded border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:border-transparent disabled:text-slate-300"
              >
                {{ agent.is_selected ? '已默认' : selectingId === agent.id ? '设置中...' : '设为默认' }}
              </button>
            </div>
          </article>
            </div>
          </section>
        </div>
      </div>
    </main>

    <AgentCreateDialog
      v-if="showCreateDialog"
      :agent="editingAgent"
      :skills="availableSkills"
      @close="closeDialog"
      @success="handleDialogSuccess"
    />

    <div v-if="showCustomizePanel" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 px-4 backdrop-blur-sm">
      <section class="w-full max-w-2xl rounded-lg border border-slate-200 bg-white p-5 shadow-2xl">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-base font-semibold text-slate-950">自定义工作台</h2>
            <p class="mt-1 text-sm text-slate-500">选择你常用的模块。每个用户都会保存自己的布局。</p>
          </div>
          <button
            @click="showCustomizePanel = false"
            class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            title="关闭"
          >
            <X :size="16" />
          </button>
        </div>

        <div class="mt-5 grid gap-3 md:grid-cols-2">
          <label
            v-for="module in featureModules"
            :key="module.key"
            class="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4"
          >
            <input
              type="checkbox"
              class="mt-1 h-4 w-4 accent-sky-600"
              :checked="enabledModuleKeys.includes(module.key)"
              @change="toggleWorkspaceModule(module.key)"
            />
            <span class="min-w-0">
              <span class="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <component :is="module.icon" :size="15" />
                {{ module.title }}
              </span>
              <span class="mt-1 block text-xs leading-5 text-slate-500">{{ module.desc }}</span>
            </span>
          </label>
        </div>

        <div class="mt-6 rounded-lg border border-sky-200 bg-sky-50/70 p-4">
          <div class="flex items-start gap-3">
            <span class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded bg-white text-sky-700">
              <MessageSquare :size="16" />
            </span>
            <div class="min-w-0 flex-1">
              <h3 class="text-sm font-semibold text-slate-900">直接对工作台提要求</h3>
              <p class="mt-1 text-xs leading-5 text-slate-500">例如：给我添加网页监控；以后输出用图表；隐藏后台任务。</p>
              <textarea
                v-model="workspaceCommand"
                rows="3"
                class="ui-field mt-3 w-full resize-none rounded px-3 py-2 text-sm outline-none"
                placeholder="写下你想要的工作台变化"
              />
              <div v-if="workspaceCommandActions.length" class="mt-3 space-y-1">
                <p v-for="action in workspaceCommandActions" :key="action" class="text-xs text-sky-800">{{ action }}</p>
              </div>
              <button
                @click="submitWorkspaceCommand"
                :disabled="workspaceCommandSubmitting || !workspaceCommand.trim()"
                class="mt-3 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-300"
              >
                {{ workspaceCommandSubmitting ? '调整中...' : '按要求调整' }}
              </button>
            </div>
          </div>
        </div>

        <div class="mt-6 border-t border-slate-100 pt-5">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-semibold text-slate-900">小窗口</h3>
              <p class="mt-1 text-xs text-slate-500">添加后会出现在工作台首页，每个用户独立保存。</p>
            </div>
          </div>
          <div class="mt-3 grid gap-3 md:grid-cols-2">
            <label
              v-for="widget in workspaceWidgets"
              :key="widget.id"
              class="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4"
            >
              <input
                type="checkbox"
                class="mt-1 h-4 w-4 accent-sky-600"
                :checked="widget.enabled"
                @change="toggleWorkspaceWidget(widget.id)"
              />
              <span>
                <span class="block text-sm font-semibold text-slate-900">{{ widget.title }}</span>
                <span class="mt-1 block text-xs leading-5 text-slate-500">{{ widgetDescription(widget.type) }}</span>
              </span>
            </label>
          </div>
          <div class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
            <h4 class="text-sm font-semibold text-slate-900">添加自定义小窗口</h4>
            <div class="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_180px_auto]">
              <input v-model="newWidgetTitle" class="ui-field h-10 rounded px-3 text-sm outline-none" placeholder="例如 我的数据监控" />
              <select v-model="newWidgetType" class="ui-field h-10 rounded px-3 text-sm outline-none">
                <option value="custom">空白窗口</option>
                <option value="quick-actions">快捷操作</option>
                <option value="web-monitor">网页监控</option>
                <option value="chart-output">图表输出</option>
                <option value="recent-runs">最近运行</option>
                <option value="recent-tasks">后台任务</option>
              </select>
              <button @click="addWorkspaceWidget" class="rounded bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-800">
                添加
              </button>
            </div>
          </div>
        </div>

        <div class="mt-5 flex justify-between gap-3 border-t border-slate-100 pt-4">
          <button
            @click="resetWorkspaceModules"
            class="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            恢复默认
          </button>
          <button
            @click="showCustomizePanel = false"
            class="ui-primary rounded px-4 py-2 text-sm font-medium text-white"
          >
            完成
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { modelDisplayName, taskTypeLabel } from '../utils/displayNames'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity, BookOpen, Bot, Brain, Bug, Copy, Cpu, Database, Gauge, Globe2, KeyRound, ListChecks, LogOut, MessageSquare, Pencil, Plus, SlidersHorizontal, Sparkles, Trash2, X, Zap,
} from 'lucide-vue-next'
import { useUserStore } from '../stores/user'
import { useAgentSessionStore } from '../stores/agentSession'
import * as agentApi from '../api/agent'
import type { AgentInfo } from '../api/agent'
import * as llmConfigApi from '../api/llmConfig'
import type { LlmConfig } from '../api/llmConfig'
import * as skillApi from '../api/skill'
import { getUserDashboard, getUserQuota } from '../api/userDashboard'
import type { DashboardLevel, UserDashboard, UserQuotaStatus } from '../api/userDashboard'
import { downloadFile } from '../utils/download'
import * as workspaceApi from '../api/workspace'
import type { WorkspaceWidget } from '../api/workspace'
import AgentCreateDialog from '../components/AgentCreateDialog.vue'
import { toastError, toastSuccess } from '../utils/toast'
import { getErrorMessage } from '../utils/request'

const userStore = useUserStore()
const agentSession = useAgentSessionStore()
const router = useRouter()
const route = useRoute()

const agents = ref<AgentInfo[]>([])
const availableSkills = ref<any[]>([])
const configs = ref<LlmConfig[]>([])
const dashboard = ref<UserDashboard | null>(null)
const quota = ref<UserQuotaStatus | null>(null)
const showCreateDialog = ref(false)
const showCustomizePanel = ref(false)
const editingAgent = ref<AgentInfo | null>(null)
const loading = ref(false)
const loadError = ref('')
const selectingId = ref<number | null>(null)
const cloningId = ref<number | null>(null)
const defaultWorkspaceModules = ['models', 'knowledge', 'skills', 'web-monitor', 'memory', 'tasks']
const enabledModuleKeys = ref<string[]>([...defaultWorkspaceModules])
const workspaceWidgets = ref<WorkspaceWidget[]>([])
const newWidgetTitle = ref('')
const newWidgetType = ref('custom')
const workspaceCommand = ref('')
const workspaceCommandSubmitting = ref(false)
const workspaceCommandActions = ref<string[]>([])

const defaultAgent = computed(() => agents.value.find(a => a.is_selected) || null)
const selectedAgentName = computed(() => defaultAgent.value?.name || '')
const selectedAgent = computed(() => defaultAgent.value || agents.value[0] || null)
const floatingAgentList = computed(() => agents.value.filter((agent) => !agent.is_selected))
const agentListHint = computed(() => {
  if (!agents.value.length) return '创建第一个助手后就可以开始对话。'
  if (defaultAgent.value) return '当前默认助手已经固定到右上角，可以在这里切换其他助手。'
  return '选择一个助手开始聊天、添加资料或调整能力。'
})
const configuredModelNames = computed(() => new Set(configs.value.map((config) => config.model_name.toLowerCase())))
const hasChatKey = computed(() => configs.value.some((config) => !isEmbeddingModel(config.model_name)))
const hasEmbeddingKey = computed(() => configs.value.some((config) => {
  return isEmbeddingModel(config.model_name) || config.model_name.toLowerCase() === 'glm-4'
}))
const hasAnyAgent = computed(() => agents.value.length > 0)
const hasReadyAgent = computed(() => agents.value.some((agent) => {
  const modelReady = hasModelKey(agent.model_name || '')
  const ragReady = agent.rag_enabled !== 1 || hasEmbeddingKey.value
  return modelReady && ragReady
}))
const readyAgentCount = computed(() => agents.value.filter((agent) => {
  const modelReady = hasModelKey(agent.model_name || '')
  const ragReady = agent.rag_enabled !== 1 || hasEmbeddingKey.value
  return modelReady && ragReady
}).length)
const healthText = computed(() => {
  const score = dashboard.value?.status.health_score ?? setupProgress.value
  if (score >= 90) return '配置完整，适合继续扩展能力'
  if (score >= 70) return '核心能力可用，仍有优化项'
  if (score >= 45) return '建议先补齐模型、资料或能力'
  return '关键配置不足，先按建议逐项处理'
})
const quotaUsagePercent = computed(() => {
  if (!quota.value || quota.value.unlimited || quota.value.monthly_token_limit <= 0) return 0
  return Math.min(100, Math.round((quota.value.used_tokens / quota.value.monthly_token_limit) * 100))
})
const featureModules = computed(() => {
  const counts = dashboard.value?.counts
  const taskFailed = dashboard.value?.status.tasks.failed ?? 0
  const knowledgeDone = counts?.knowledge_done ?? 0
  const knowledgeTotal = counts?.knowledge_docs ?? 0
  return [
    {
      key: 'models',
      title: '模型接入',
      value: `${counts?.chat_models ?? 0} 聊天 / ${counts?.embedding_models ?? 0} 检索`,
      desc: hasChatKey.value && hasEmbeddingKey.value ? '聊天和资料检索都已具备调用基础。' : '缺少模型会直接影响聊天、检索和网页入库。',
      icon: Cpu,
      path: '/llm-configs',
      state: hasChatKey.value && hasEmbeddingKey.value ? '完整' : '待补齐',
      stateClass: hasChatKey.value && hasEmbeddingKey.value ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700',
    },
    {
      key: 'knowledge',
      title: '个人知识空间',
      value: `${knowledgeDone} / ${knowledgeTotal} 已入库`,
      desc: knowledgeDone > 0 ? '资料已经能被检索，后续可继续增加网页和文档来源。' : '还没有可检索内容，建议先上传文档或抓取网页。',
      icon: Database,
      path: selectedAgent.value ? `/agents/${selectedAgent.value.id}/knowledge` : '/agents',
      state: knowledgeDone > 0 ? '可检索' : '空',
      stateClass: knowledgeDone > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
    },
    {
      key: 'skills',
      title: '技能中心',
      value: `${counts?.skills ?? 0} 个能力`,
      desc: (counts?.skills ?? 0) > 0 ? '可把常用流程沉淀成可复用能力。' : '还没有安装或创建能力，助手只能靠基础设定工作。',
      icon: Zap,
      path: '/skills',
      state: (counts?.skills ?? 0) > 0 ? '已扩展' : '待添加',
      stateClass: (counts?.skills ?? 0) > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-sky-50 text-sky-700',
    },
    {
      key: 'web-monitor',
      title: '网页监控',
      value: '实时数据',
      desc: '把网页变化、价格、公告等来源加入监控，保存最近检查状态。',
      icon: Globe2,
      path: '/web-monitor',
      state: '可添加',
      stateClass: 'bg-sky-50 text-sky-700',
    },
    {
      key: 'memory',
      title: '长期记忆',
      value: `${counts?.memories ?? 0} 条记忆`,
      desc: (counts?.memories ?? 0) > 0 ? '助手可结合历史偏好和项目背景回答。' : '记忆还未沉淀，持续对话后会更个性化。',
      icon: Brain,
      path: selectedAgent.value ? `/agents/${selectedAgent.value.id}/memory` : '/agents',
      state: (counts?.memories ?? 0) > 0 ? '已沉淀' : '待生成',
      stateClass: (counts?.memories ?? 0) > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
    },
    {
      key: 'tasks',
      title: '后台任务',
      value: `${counts?.tasks ?? 0} 个任务`,
      desc: taskFailed > 0 ? `有 ${taskFailed} 个失败任务，需要处理入库或抓取异常。` : '后台任务用于承接文档、网页和后续异步能力。',
      icon: ListChecks,
      path: '/tasks',
      state: taskFailed > 0 ? '需处理' : '正常',
      stateClass: taskFailed > 0 ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700',
    },
  ]
})
const visibleFeatureModules = computed(() => featureModules.value.filter((item) => enabledModuleKeys.value.includes(item.key)))
const visibleWorkspaceWidgets = computed(() => workspaceWidgets.value.filter((item) => item.enabled))

const workspaceSummary = computed(() => {
  if (!hasChatKey.value) return '先连接一个模型，系统会自动处理接口地址'
  if (!hasAnyAgent.value) return '模型已连接，现在可以创建第一个助手'
  if (!hasReadyAgent.value) return '助手还缺少必要配置，先补齐后再开始聊天'
  return `${agents.value.length} 个助手，${selectedAgentName.value || '可选择默认助手'}`
})

const defaultWorkspaceWidgets = (): WorkspaceWidget[] => [
  { id: 'quick-actions', type: 'quick-actions', title: '快捷操作', enabled: true, size: 'wide', settings: {} },
  { id: 'web-monitor', type: 'web-monitor', title: '网页监控', enabled: true, size: 'wide', settings: {} },
  { id: 'chart-output', type: 'chart-output', title: '图表输出', enabled: true, size: 'wide', settings: {} },
  { id: 'recent-runs', type: 'recent-runs', title: '最近运行', enabled: true, size: 'wide', settings: {} },
  { id: 'recent-tasks', type: 'recent-tasks', title: '后台任务', enabled: true, size: 'wide', settings: {} },
]

const loadWorkspaceModules = async () => {
  try {
    const workspace = await workspaceApi.getWorkspaceConfig()
    enabledModuleKeys.value = workspace.modules?.length ? workspace.modules : [...defaultWorkspaceModules]
    workspaceWidgets.value = workspace.widgets?.length ? workspace.widgets : defaultWorkspaceWidgets()
  } catch {
    enabledModuleKeys.value = [...defaultWorkspaceModules]
    workspaceWidgets.value = defaultWorkspaceWidgets()
  }
}

const saveWorkspaceModules = async () => {
  try {
    const workspace = await workspaceApi.saveWorkspaceConfig({
      modules: enabledModuleKeys.value,
      widgets: workspaceWidgets.value,
      layout: {},
    })
    enabledModuleKeys.value = workspace.modules
    workspaceWidgets.value = workspace.widgets
  } catch (e: any) {
    toastError(getErrorMessage(e, '保存工作台失败'))
  }
}

const toggleWorkspaceModule = async (key: string) => {
  enabledModuleKeys.value = enabledModuleKeys.value.includes(key)
    ? enabledModuleKeys.value.filter((item) => item !== key)
    : [...enabledModuleKeys.value, key]
  await saveWorkspaceModules()
}

const resetWorkspaceModules = async () => {
  enabledModuleKeys.value = [...defaultWorkspaceModules]
  workspaceWidgets.value = defaultWorkspaceWidgets()
  await saveWorkspaceModules()
}

const toggleWorkspaceWidget = async (id: string) => {
  workspaceWidgets.value = workspaceWidgets.value.map((item) => (
    item.id === id ? { ...item, enabled: !item.enabled } : item
  ))
  await saveWorkspaceModules()
}

const addWorkspaceWidget = async () => {
  const type = newWidgetType.value
  const title = newWidgetTitle.value.trim() || widgetDefaultTitle(type)
  workspaceWidgets.value = [
    ...workspaceWidgets.value,
    {
      id: `${type}-${Date.now()}`,
      type,
      title,
      enabled: true,
      size: 'wide',
      settings: {},
    },
  ]
  newWidgetTitle.value = ''
  newWidgetType.value = 'custom'
  await saveWorkspaceModules()
  toastSuccess('小窗口已添加')
}

const widgetDefaultTitle = (type: string) => {
  const map: Record<string, string> = {
    custom: '自定义小窗口',
    'quick-actions': '快捷操作',
    'web-monitor': '网页监控',
    'chart-output': '图表输出',
    'recent-runs': '最近运行',
    'recent-tasks': '后台任务',
  }
  return map[type] || '自定义小窗口'
}

const widgetDescription = (type: string) => {
  const map: Record<string, string> = {
    custom: '预留给个人流程、外部数据或自定义 Skill。',
    'quick-actions': '常用入口集中到一个小窗口。',
    'web-monitor': '查看网页监控入口和变化检查。',
    'chart-output': '让助手把结果输出成图表。',
    'recent-runs': '展示最近的助手运行记录。',
    'recent-tasks': '展示文档、网页、索引等后台任务。',
  }
  return map[type] || '自定义信息窗口'
}

const submitWorkspaceCommand = async () => {
  const text = workspaceCommand.value.trim()
  if (!text) return
  workspaceCommandSubmitting.value = true
  workspaceCommandActions.value = []
  try {
    const result = await workspaceApi.commandWorkspace(text)
    enabledModuleKeys.value = result.workspace.modules
    workspaceWidgets.value = result.workspace.widgets
    workspaceCommandActions.value = result.actions
    workspaceCommand.value = ''
    toastSuccess(result.message || '工作台已更新')
  } catch (e: any) {
    toastError(getErrorMessage(e, '调整工作台失败'))
  } finally {
    workspaceCommandSubmitting.value = false
  }
}

const setupItems = computed(() => {
  const selected = selectedAgent.value
  const shouldAddKnowledge = Boolean(selected && selected.rag_enabled === 1 && hasEmbeddingKey.value)
  return [
    {
      key: 'model',
      title: '连接模型',
      desc: '填写模型密钥后才能聊天',
      icon: Cpu,
      done: hasChatKey.value,
      active: !hasChatKey.value,
      action: () => router.push('/llm-configs'),
    },
    {
      key: 'agent',
      title: '创建助手',
      desc: '选择模板或自定义用途',
      icon: Bot,
      done: hasAnyAgent.value,
      active: hasChatKey.value && !hasAnyAgent.value,
      action: openCreateDialog,
    },
    {
      key: 'knowledge',
      title: '添加资料',
      desc: '上传文档或抓取网页',
      icon: Database,
      done: (dashboard.value?.counts.knowledge_done ?? 0) > 0,
      active: shouldAddKnowledge,
      action: () => selected ? router.push(`/agents/${selected.id}/knowledge`) : openCreateDialog(),
    },
    {
      key: 'chat',
      title: '开始对话',
      desc: '用助手处理实际问题',
      icon: MessageSquare,
      done: hasReadyAgent.value,
      active: hasAnyAgent.value && hasReadyAgent.value,
      action: () => selected ? enterChat(selected.id) : openCreateDialog(),
    },
  ]
})
const setupProgress = computed(() => {
  const base = [
    hasChatKey.value,
    hasAnyAgent.value,
    (dashboard.value?.counts.knowledge_done ?? 0) > 0 || !selectedAgent.value?.rag_enabled,
    hasReadyAgent.value,
  ].filter(Boolean).length
  return Math.round((base / 4) * 100)
})
const setupTitle = computed(() => {
  if (!hasChatKey.value) return '先把模型接上'
  if (!hasAnyAgent.value) return '创建你的第一个助手'
  if (!hasReadyAgent.value) return '补齐助手配置'
  return '可以开始使用了'
})
const setupDescription = computed(() => {
  if (!hasChatKey.value) return '你只需要选择模型并填写密钥，系统会自动匹配调用地址。'
  if (!hasAnyAgent.value) return '助手决定回答风格、使用哪个模型，以及是否启用知识库和记忆。'
  if (!hasReadyAgent.value) return '当前助手还缺少必要模型连接，补齐后才能稳定对话。'
  return '现在可以直接聊天，也可以给助手添加文档、网页资料，让回答更贴合你的内容。'
})
const primaryActionText = computed(() => {
  if (!hasChatKey.value) return '连接模型'
  if (!hasAnyAgent.value) return '创建助手'
  if (!hasReadyAgent.value) return '检查配置'
  return '开始聊天'
})
const primaryActionIcon = computed(() => {
  if (!hasChatKey.value) return Cpu
  if (!hasAnyAgent.value) return Plus
  if (!hasReadyAgent.value) return KeyRound
  return MessageSquare
})

const isEmbeddingModel = (modelName: string) => {
  const model = modelName.toLowerCase()
  return model.includes('embedding') || model.startsWith('baai/')
}

const hasModelKey = (modelName: string) => {
  return configuredModelNames.value.has(modelName.trim().toLowerCase())
}

const runPrimaryAction = () => {
  if (!hasChatKey.value) {
    router.push('/llm-configs')
    return
  }
  if (!hasAnyAgent.value) {
    openCreateDialog()
    return
  }
  const agent = selectedAgent.value
  if (!agent) {
    openCreateDialog()
    return
  }
  if (!hasModelKey(agent.model_name || '') || (agent.rag_enabled === 1 && !hasEmbeddingKey.value)) {
    router.push('/llm-configs')
    return
  }
  enterChat(agent.id)
}

const ragStatusText = (agent: AgentInfo) => {
  if (agent.rag_enabled !== 1) return '未启用'
  return hasEmbeddingKey.value ? '已启用' : '缺少密钥'
}

const ragStatusClass = (agent: AgentInfo) => {
  if (agent.rag_enabled !== 1) return 'text-slate-500'
  return hasEmbeddingKey.value ? 'text-emerald-700' : 'text-amber-600'
}

const formatNumber = (value: number) => {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(value || 0)
}

const recommendationClass = (level: DashboardLevel) => {
  if (level === 'danger') return 'border-red-200'
  if (level === 'warn') return 'border-amber-200'
  return 'border-sky-200'
}

const statusText = (status: string) => {
  const map: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    success: '成功',
    finished: '完成',
    done: '完成',
    failed: '失败',
    canceled: '已取消',
  }
  return map[status] || status || '未知'
}

const statusClass = (status: string) => {
  if (['success', 'finished', 'done'].includes(status)) return 'bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'bg-red-50 text-red-700'
  if (status === 'running') return 'bg-sky-50 text-sky-700'
  return 'bg-slate-100 text-slate-500'
}

const reload = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const [agentList, llmConfigs] = await Promise.all([
      agentApi.listAgents(),
      llmConfigApi.listConfigs(),
    ])
    agents.value = agentList
    configs.value = llmConfigs
    agentSession.setSelectedFromList(agentList)
    void loadSecondaryData()
  } catch (e: any) {
    loadError.value = getErrorMessage(e, '加载助手列表失败')
  } finally {
    loading.value = false
  }
}

const loadSecondaryData = async () => {
  try {
    const userSkillsRequest = userStore.user?.is_admin
      ? skillApi.listUserSkills()
      : Promise.resolve([])
    const [userSkills, publicSkills, dashboardData, quotaData] = await Promise.all([
      userSkillsRequest,
      skillApi.listPublicSkills(),
      getUserDashboard(),
      getUserQuota(),
    ])
    dashboard.value = dashboardData
    quota.value = quotaData
    const merged = new Map<number, any>()
    ;[...userSkills, ...publicSkills].forEach((s: any) => merged.set(s.id, s))
    availableSkills.value = [...merged.values()]
  } catch (e: any) {
    console.warn('加载工作台扩展数据失败:', e)
  }
}

const exportUsageReport = async () => {
  try {
    await downloadFile('/user/usage/export', undefined, 'my_usage_report.csv')
  } catch (e: any) {
    toastError(getErrorMessage(e, '导出失败'))
  }
}

const enterChat = (agentId: number) => {
  router.push(`/agents/${agentId}/chat`)
}

const openCreateDialog = () => {
  editingAgent.value = null
  showCreateDialog.value = true
}

const openEditDialog = (agent: AgentInfo) => {
  editingAgent.value = agent
  showCreateDialog.value = true
}

const closeDialog = () => {
  showCreateDialog.value = false
  editingAgent.value = null
}

const handleDialogSuccess = async (payload?: { agentId?: number; created: boolean }) => {
  if (payload?.created && payload.agentId) {
    await agentApi.selectAgent(payload.agentId)
    if (userStore.user) {
      userStore.user.selected_agent_id = payload.agentId
      localStorage.setItem('user', JSON.stringify(userStore.user))
    }
  }
  await reload()
}

const selectAgent = async (agent: AgentInfo) => {
  selectingId.value = agent.id
  try {
    await agentApi.selectAgent(agent.id)
    if (userStore.user) {
      userStore.user.selected_agent_id = agent.id
      localStorage.setItem('user', JSON.stringify(userStore.user))
    }
    agentSession.remember({ ...agent, is_selected: true })
    await reload()
    toastSuccess(`已选择「${agent.name}」`)
  } catch (e: any) {
    toastError(getErrorMessage(e, '设置默认助手失败'))
  } finally {
    selectingId.value = null
  }
}

const cloneAgent = async (agent: AgentInfo) => {
  const defaultName = `${agent.name} 副本`
  const inputName = prompt('新助手名称', defaultName)
  if (inputName === null) return
  const name = inputName.trim() || defaultName
  cloningId.value = agent.id
  try {
    const result = await agentApi.cloneAgent(agent.id, { name })
    await agentApi.selectAgent(result.agent_id)
    if (userStore.user) {
      userStore.user.selected_agent_id = result.agent_id
      localStorage.setItem('user', JSON.stringify(userStore.user))
    }
    await agentSession.loadSelected(true)
    await reload()
  } catch (e: any) {
    toastError(getErrorMessage(e, '复制助手失败'))
  } finally {
    cloningId.value = null
  }
}

const handleDelete = async (agent: AgentInfo) => {
  try {
    const preview = await agentApi.deletePreview(agent.id)
    const text = [
      `确认删除助手「${preview.agent_name}」？`,
      '',
      `会话：${preview.conversation_count}`,
      `消息：${preview.message_count}`,
      `知识库文档：${preview.knowledge_count}`,
      `运行记录：${preview.run_count}`,
      `能力绑定：${preview.skill_binding_count}`,
      '',
      '此操作不可恢复。',
    ].join('\n')
    if (!confirm(text)) return
    await agentApi.deleteAgent(agent.id)
    await reload()
  } catch (e: any) {
    toastError(getErrorMessage(e, '删除失败'))
  }
}

// 新手指引会带着 ?create=1 过来：自动打开"新建助手"，并把参数清掉，刷新页面不会重复弹
const consumeCreateQuery = () => {
  if (!route.query.create) return
  openCreateDialog()
  router.replace({ path: '/agents' })
}
watch(() => route.query.create, consumeCreateQuery)

onMounted(async () => {
  consumeCreateQuery()
  await Promise.all([reload(), loadWorkspaceModules()])
})
</script>
