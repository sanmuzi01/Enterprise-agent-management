<template>
  <div class="h-screen flex bg-transparent">
    <!-- 左侧会话栏 -->
    <Transition name="fade">
      <div v-if="convDrawer" class="fixed inset-0 z-30 bg-black/30 backdrop-blur-[2px] md:hidden" @click="convDrawer = false"></div>
    </Transition>
    <aside
      :class="[
        'ui-glass fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] shrink-0 flex-col border-r transition-transform duration-500 ease-[var(--ease)] md:static md:z-auto md:max-w-none md:translate-x-0',
        convDrawer ? 'translate-x-0' : '-translate-x-full',
      ]"
    >
      <!-- 助手信息 -->
      <div class="px-4 pb-1 pt-4">
        <button
          @click="$router.push('/agents')"
          class="-ml-1 inline-flex items-center text-[13px] text-[var(--accent)] hover:opacity-80"
        >
          <ChevronLeft :size="18" :stroke-width="2" />
          工作台
        </button>
        <h2 class="mt-1 truncate text-[19px] font-bold tracking-[-0.024em] text-slate-900">{{ currentAgent?.name || '加载中...' }}</h2>
      </div>

      <div class="space-y-1.5 px-3 pb-3 pt-2">
        <button
          @click="createNewConversation"
          :disabled="loading"
          class="flex h-9 w-full items-center gap-2 rounded-[10px] bg-[var(--accent-soft)] px-3 text-[14px] font-medium text-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <PlusCircle :size="16" :stroke-width="2" />
          <span>新建会话</span>
          <span class="ml-auto text-[11px] font-medium opacity-70">Ctrl N</span>
        </button>
        <button
          @click="router.push(`/agents/${agentId}/knowledge`)"
          class="flex h-9 w-full items-center gap-2 rounded-[10px] px-3 text-[14px] font-medium text-slate-700 hover:bg-black/[.045]"
        >
          <BookOpen :size="16" :stroke-width="1.8" class="text-slate-500" />
          <span>个人资料</span>
        </button>
      </div>

      <div class="space-y-2 px-3 pb-2">
        <div class="relative">
          <Search class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" :size="14" />
          <input
            v-model="conversationQuery"
            type="text"
            class="h-9 w-full rounded-[10px] bg-slate-100 pl-8 pr-3 text-[14px] text-slate-800 outline-none transition-shadow placeholder:text-slate-400 focus:bg-white focus:shadow-[0_0_0_4px_var(--accent-soft)]"
            placeholder="搜索会话"
          />
        </div>
        <div class="relative grid grid-cols-3 rounded-[10px] bg-slate-100 p-[3px] text-xs">
          <span
            class="absolute bottom-[3px] left-[3px] top-[3px] w-[calc(33.333%-2px)] rounded-[8px] bg-white shadow-[0_1px_3px_rgba(0,0,0,.14),0_0_0_.5px_rgba(0,0,0,.04)] transition-transform duration-500 ease-[var(--spring)]"
            :style="{ transform: `translateX(${filterIndex * 100}%)` }"
          ></span>
          <button
            v-for="item in conversationFilters"
            :key="item.value"
            @click="conversationFilter = item.value"
            :class="conversationFilter === item.value ? 'text-slate-900' : 'text-slate-500'"
            class="relative z-10 h-7 font-medium"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

            <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5 pt-1">
        <div
          v-for="c in filteredConversations"
          :key="c.id"
          class="group relative rounded-lg"
        >
          <!-- 行主体 -->
          <div
            @click="selectConversation(c.id)"
            :class="[
              'flex h-10 items-center rounded-[10px] px-2 cursor-pointer text-[14px] transition-colors',
              currentConversationId === c.id
                ? 'bg-[var(--accent)] text-white font-medium'
                : 'text-slate-800 hover:bg-black/[.045]'
            ]"
          >
            <!-- 固定/归档 图标（锚点前预留图标） -->
            <span class="w-5 shrink-0 text-xs text-gray-400 mr-1 flex items-center justify-center">
              <Pin v-if="isPinned(c.id)" :size="13" class="text-amber-500 fill-amber-200" />
              <Archive v-else-if="isArchived(c.id)" :size="13" class="text-gray-400" />
            </span>
            <!-- 标题（重命名时变成 input） -->
            <span v-if="!(editingConvId === c.id)" class="truncate flex-1 pr-2">{{ c.title }}</span>
            <input
              v-else
              ref="renameInputRef"
              v-model="renameText"
              @keydown.enter="!isImeEnter($event) && commitRename(c.id)"
              @keydown.esc="cancelRename"
              @click.stop
              @blur="commitRename(c.id)"
              class="flex-1 h-7 px-2 rounded border border-blue-400 bg-white text-sm text-gray-800 outline-none focus:ring-2 focus:ring-blue-300"
            />
            <!-- 右侧 更多功能按钮（hover 才显） -->
            <button
              v-if="editingConvId !== c.id"
              @click.stop="openMenu(c.id)"
              :class="[
                'w-6 h-6 shrink-0 rounded flex items-center justify-center transition-opacity',
                currentConversationId === c.id
                  ? 'opacity-100 text-white/80 hover:bg-white/20'
                  : 'opacity-0 group-hover:opacity-100 text-gray-400 hover:bg-gray-200 hover:text-gray-600'
              ]"
              title="更多"
            >
              <MoreHorizontal :size="14" :stroke-width="2" />
            </button>
          </div>

          <!-- 操作弹窗（绝对定位在此行右下方） -->
          <div
            v-if="menuConvId === c.id"
            class="absolute right-1 top-9 z-30 w-36 py-1 bg-white rounded-lg shadow-lg border border-gray-200 text-sm overflow-hidden"
            @click.stop
          >
            <button
              @click="startRename(c)"
              class="w-full px-3 h-8 flex items-center gap-2 text-gray-700 hover:bg-gray-100"
            >
              <Pencil :size="13" /> 重命名
            </button>
            <button
              @click="togglePin(c.id)"
              class="w-full px-3 h-8 flex items-center gap-2 text-gray-700 hover:bg-gray-100"
            >
              <Pin :size="13" /> {{ isPinned(c.id) ? '取消置顶' : '置顶' }}
            </button>
            <button
              @click="toggleArchive(c)"
              class="w-full px-3 h-8 flex items-center gap-2 text-gray-700 hover:bg-gray-100"
            >
              <Archive :size="13" /> {{ isArchived(c.id) ? '取消归档' : '归档' }}
            </button>
            <div class="h-px bg-gray-100 my-1"></div>
            <button
              @click="handleDeleteConv(c)"
              class="w-full px-3 h-8 flex items-center gap-2 text-red-600 hover:bg-red-50"
            >
              <Trash2 :size="13" /> 删除
            </button>
            <div class="h-px bg-gray-100 my-1"></div>
            <button
              @click="handleExportConv(c, 'markdown')"
              class="w-full px-3 h-8 flex items-center gap-2 text-gray-700 hover:bg-gray-100"
            >
              <Download :size="13" /> 导出 MD
            </button>
            <button
              @click="handleExportConv(c, 'json')"
              class="w-full px-3 h-8 flex items-center gap-2 text-gray-700 hover:bg-gray-100"
            >
              <Download :size="13" /> 导出 JSON
            </button>
          </div>
        </div>

        <div v-if="filteredConversations.length === 0" class="text-sm text-gray-400 text-center py-8 px-4">
          {{ conversations.length === 0 ? '暂无会话，发送第一条消息会自动创建' : '没有匹配的会话' }}
        </div>
      </div>
    </aside>

    <!-- 右侧聊天区 -->
    <section class="relative flex min-w-0 flex-1 flex-col">
      <div class="ui-glass flex items-start gap-1 border-b px-3 py-2 md:items-center md:px-6">
        <button
          type="button"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 hover:bg-black/[.06] md:hidden"
          aria-label="会话列表"
          @click="convDrawer = true"
        >
          <PanelLeft :size="19" :stroke-width="1.8" />
        </button>
        <AgentSubnav class="min-w-0 flex-1" :agent-id="agentId" :agent-name="currentAgent?.name" active="chat" />
      </div>
      <!-- 消息区域 -->
      <div ref="messageListRef" class="flex-1 space-y-6 overflow-y-auto px-4 pb-48 pt-6 md:px-8">
        <div class="mx-auto w-full max-w-3xl px-1">
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <span class="font-medium text-slate-700">{{ currentAgent?.name || '当前助手' }}</span>
            <span :class="hasCurrentModelKey ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'" class="rounded-full px-2.5 py-1 font-medium">
              模型 {{ hasCurrentModelKey ? '已配置' : '缺密钥' }}
            </span>
            <span :class="currentAgent?.rag_enabled === 1 ? (hasEmbeddingKey ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700') : 'bg-slate-100 text-slate-500'" class="rounded-full px-2.5 py-1 font-medium">
              资料 {{ currentAgent?.rag_enabled === 1 ? (hasEmbeddingKey ? '可用' : '缺密钥') : '关闭' }}
            </span>
            <span :class="currentAgent?.memory_enabled === 1 ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'" class="rounded-full px-2.5 py-1 font-medium">
              记忆 {{ currentAgent?.memory_enabled === 1 ? '开启' : '关闭' }}
            </span>
            <span v-if="currentAgent?.skills?.length" class="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600">
              能力 {{ currentAgent.skills.length }} 个
            </span>
          </div>
        </div>

        <div v-if="messages.length === 0" class="h-full flex items-center justify-center text-gray-400">
          开始和助手对话吧
        </div>

        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          :class="['mx-auto flex w-full max-w-3xl', msg.role === 'user' ? 'justify-end' : 'justify-start']"
        >
          <div :class="msg.role === 'user' ? 'max-w-[78%]' : 'w-full'">
            <div
              :class="[
                'text-[15.5px] leading-[1.7] tracking-[-0.008em]',
                msg.role === 'user'
                  ? 'whitespace-pre-wrap rounded-[20px] rounded-br-md bg-[var(--accent)] px-4 py-2.5 text-white'
                  : 'md-body text-slate-900'
              ]"
              @click="msg.role === 'assistant' && onAnswerClick($event, msg)"
            >
              <template v-if="msg.role === 'user'">{{ displayUser(msg.content) }}</template>
              <div v-else v-html="renderAnswer(msg.content)"></div>
            </div>
            <CitationList
              v-if="msg.role === 'assistant'"
              :citations="msg.citations"
              :open-index="msg.citeOpen ?? null"
            />
            <RagSavingsBar v-if="msg.role === 'assistant' && msg.ragStats" :stats="msg.ragStats" compact class="mt-1.5 max-w-md" />
            <div v-if="msg.role === 'assistant' && msg.tokens != null" class="mt-1 text-xs text-gray-500">
              本轮实际用量：{{ msg.tokens }} Token（含工具调用与记忆总结，非估算）
            </div>
          </div>
        </div>

        <!-- 思考/工具调用事件展示 -->
        <div v-for="(evt, i) in eventTraces" :key="'evt-'+i" class="mx-auto flex w-full max-w-3xl justify-start">
          <div
            v-if="evt.type === 'confirmation_required'"
            class="max-w-2xl w-full px-4 py-3 rounded-lg border border-amber-300 bg-amber-50 text-xs text-amber-900 space-y-2"
          >
            <div class="flex items-center gap-1.5 font-sans font-semibold">
              <AlertTriangle :size="14" class="shrink-0" />
              高风险操作待确认：{{ evt.name }}
            </div>
            <pre class="font-mono text-[11px] text-amber-800 whitespace-pre-wrap">{{ JSON.stringify(evt.args, null, 2) }}</pre>
            <div v-if="!evt.decision" class="flex gap-2 font-sans">
              <button
                :disabled="evt.deciding"
                @click="decideToolConfirmation(evt, true)"
                class="rounded-full bg-amber-600 px-3 py-1 text-white text-xs font-medium disabled:opacity-50"
              >确认执行</button>
              <button
                :disabled="evt.deciding"
                @click="decideToolConfirmation(evt, false)"
                class="rounded-full bg-white border border-amber-300 px-3 py-1 text-amber-800 text-xs font-medium disabled:opacity-50"
              >取消</button>
            </div>
            <div v-else-if="evt.decision === 'confirmed'" class="font-sans text-emerald-700">
              已确认执行<span v-if="evt.resultText">：{{ short(evt.resultText, 200) }}</span>
            </div>
            <div v-else class="font-sans text-gray-500">已取消，未执行</div>
          </div>
          <div v-else-if="evt.type === 'route'" class="max-w-2xl w-full px-4 py-2.5 rounded-lg border border-sky-200 bg-sky-50 text-xs text-sky-900 space-y-1" data-testid="route-notice">
            <p v-if="evt.reason === 'routed'">
              已转交给「{{ evt.target_name }}」处理<span v-if="evt.matched_keywords?.length">（命中：{{ evt.matched_keywords.join('、') }}）</span>。
            </p>
            <p v-else-if="evt.reason === 'no_usable_agent'">
              问题涉及「{{ departmentLabel(evt.department_code) }}」，但你所在的部门没有已发布的{{ departmentLabel(evt.department_code) }}助手，由中央助手直接回答；可联系企业管理员配置并发布。
            </p>
            <p v-if="evt.alternatives?.length">
              这个问题还涉及：
              <RouterLink v-for="alt in evt.alternatives" :key="alt.agent_id" :to="`/agents/${alt.agent_id}/chat`" class="mr-2 font-medium text-sky-700 underline">改问「{{ alt.name }}」</RouterLink>
            </p>
            <p v-if="evt.unavailable?.length && evt.reason === 'routed'" class="text-sky-700">
              另外涉及{{ evt.unavailable.map((u: any) => departmentLabel(u.department_code)).join('、') }}，你所在的部门没有对应的已发布助手。
            </p>
            <p v-if="evt.alternatives?.length || evt.unavailable?.length" data-testid="route-orchestrate">
              这件事涉及多个部门：
              <button class="font-medium text-sky-700 underline" @click="openOrchestration(evt.message)">在部门工作台一次拆成各部门步骤办理</button>
            </p>
          </div>
          <div v-else class="max-w-2xl w-full px-4 py-2 rounded-lg border border-gray-200 bg-gray-50 text-xs text-gray-500 font-mono space-y-1">
            <div v-if="evt.type === 'thinking'">
              <span class="text-purple-500 font-semibold">🤔 思考</span>
              <div class="whitespace-pre-wrap mt-1">{{ short(evt.content, 200) }}</div>
            </div>
            <div v-else-if="evt.type === 'tool_call'">
              <span class="text-blue-500 font-semibold">调用工具</span> {{ evt.name }}
              <pre class="mt-1 text-xs text-gray-600">{{ JSON.stringify(evt.args, null, 2) }}</pre>
            </div>
            <div v-else-if="evt.type === 'tool_result'">
              <span class="text-green-500 font-semibold">工具结果</span> {{ evt.name }}
              <div class="mt-1 text-gray-600">{{ short(evt.result, 200) }}</div>
            </div>
            <div v-else-if="evt.type === 'retrieval'">
              <span class="text-amber-500 font-semibold">资料检索</span> 命中 {{ evt.hit_count }} 条
              <span v-if="evt.stats && evt.stats.source_doc_chars" class="ml-1 text-emerald-600">
                · 上下文压缩 {{ Math.round((evt.stats.saved_ratio || 0) * 100) }}%（省 ≈{{ evt.stats.est_tokens_saved }} token）
              </span>
              <div v-if="evt.hit_count === 0" class="mt-1 text-gray-400">
                知识库没匹配到内容 —— 换个说法、用文档里的术语再问，或到「知识库 → 调试台」排查检索效果。
              </div>
            </div>
          </div>
        </div>

        <div v-if="loading" class="mx-auto flex w-full max-w-3xl justify-start">
          <div class="rounded-2xl bg-white px-4 py-3 shadow-[var(--sh-1)]">
            <div class="flex gap-1">
              <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></span>
              <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0.1s"></span>
              <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0.2s"></span>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入框：悬浮的液态玻璃 -->
      <div class="pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-[var(--bg)] from-40% to-transparent px-3 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-12 md:px-6">
        <div class="pointer-events-auto mx-auto max-w-3xl">
          <div
            v-if="!chatReady"
            class="mb-3 flex items-center justify-between gap-3 rounded-2xl bg-amber-50 px-4 py-2.5 text-sm text-amber-800 shadow-[var(--sh-1)]"
          >
            <div class="flex min-w-0 items-center gap-2">
              <AlertTriangle :size="16" class="shrink-0" />
              <span class="truncate">{{ chatBlockedReason }}</span>
            </div>
            <button
              @click="router.push('/llm-configs')"
              class="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100"
            >
              连接模型
            </button>
          </div>
          <div v-if="pendingAttachments.length || uploading" class="mb-2 flex flex-wrap gap-2">
            <span
              v-for="a in pendingAttachments"
              :key="a.id"
              class="ui-glass-float relative inline-flex max-w-[16rem] items-center gap-1.5 rounded-full py-1 pl-3 pr-1.5 text-[13px] text-slate-700"
            >
              <FileText :size="14" class="relative shrink-0 text-[var(--accent)]" />
              <span class="relative truncate">{{ a.name }}</span>
              <button
                type="button"
                class="relative flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-black/[.08] hover:text-slate-700"
                :aria-label="`移除 ${a.name}`"
                @click="removeAttachment(a.id)"
              >
                <X :size="12" :stroke-width="2.4" />
              </button>
            </span>
            <span v-if="uploading" class="inline-flex items-center px-2 text-[13px] text-slate-400">上传中…</span>
          </div>
          <div class="ui-glass-float flex items-end gap-2 rounded-[27px] p-2" data-guide="composer">
            <template v-if="attachmentEnabled">
              <input ref="fileInput" type="file" class="hidden" multiple @change="onFilesPicked" />
              <button
                type="button"
                :disabled="loading || uploading || !chatReady || pendingAttachments.length >= 5"
                class="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-slate-500 hover:bg-black/[.06] hover:text-slate-800 disabled:cursor-default disabled:opacity-40"
                aria-label="添加附件"
                title="添加附件（供技能里的脚本处理，最大 10MB）"
                @click="fileInput?.click()"
              >
                <Paperclip :size="18" :stroke-width="2" />
              </button>
            </template>
            <textarea
              ref="inputRef"
              v-model="inputText"
              :disabled="loading || !chatReady"
              rows="1"
              @keydown.enter.exact="onComposerEnter"
              :placeholder="chatReady ? '输入消息…' : chatBlockedReason"
              class="relative max-h-40 min-w-0 flex-1 resize-none bg-transparent px-3 py-2.5 text-[15.5px] leading-6 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
            ></textarea>
            <button
              v-if="loading"
              @click="stopGenerating"
              class="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 hover:bg-slate-300"
              aria-label="停止生成"
              title="停止"
            >
              <Square :size="13" fill="currentColor" />
            </button>
            <button
              @click="sendMessage"
              :disabled="loading || !inputText.trim() || !chatReady"
              class="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] disabled:cursor-default disabled:bg-slate-200 disabled:text-slate-400"
              aria-label="发送"
              title="发送（Enter）"
            >
              <ArrowUp :size="19" :stroke-width="2.2" />
            </button>
          </div>
        </div>
      </div>
    </section>
          <!-- 右下角：运行轨迹浮动按钮（仅本轮对话有 run 时可用） -->
    <div class="fixed bottom-32 right-6 z-30 flex flex-col items-end gap-2">
      <!-- 当前会话下产生的 run 数徽标 -->
      <button
        @click="showTraceDrawer = true"
        :disabled="runs.length === 0"
        class="ui-glass-float group relative flex h-10 items-center gap-2 rounded-full pl-3 pr-4 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span class="w-6 h-6 rounded-full bg-purple-50 text-purple-600 flex items-center justify-center">
          <GitBranch :size="14" :stroke-width="2" />
        </span>
        <span class="text-sm font-medium text-gray-700">回答过程</span>
        <span v-if="runs.length > 0" class="text-xs px-1.5 h-5 rounded-full bg-purple-100 text-purple-700 flex items-center">
          {{ runs.length }}
        </span>
      </button>
    </div>

    <!-- Drawer 遮罩 -->
    <Transition name="fade">
      <div
        v-if="showTraceDrawer"
        class="fixed inset-0 bg-black/30 z-40"
        @click="showTraceDrawer = false"
      />
    </Transition>

    <!-- Drawer 面板（右侧滑入） -->
    <Transition name="slide">
      <aside
        v-if="showTraceDrawer"
        class="fixed top-0 right-0 h-full w-[480px] max-w-[92vw] bg-white shadow-2xl z-50 flex flex-col"
      >
        <!-- Drawer Header -->
        <header class="h-14 px-5 border-b border-gray-200 flex items-center justify-between shrink-0">
          <div class="flex items-center gap-2">
            <span class="w-8 h-8 rounded-lg bg-purple-50 text-purple-600 flex items-center justify-center">
              <GitBranch :size="16" />
            </span>
            <div>
              <h3 class="text-sm font-semibold text-gray-800">回答过程</h3>
              <p class="text-xs text-gray-500">{{ currentAgent?.name || '' }}</p>
            </div>
          </div>
          <button
            @click="showTraceDrawer = false"
            class="w-8 h-8 rounded flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          >
            <X :size="16" />
          </button>
        </header>

        <!-- Drawer Body: 左列 Run 列表 + 右列 Step 详情（小屏折叠成上下） -->
        <div class="flex-1 min-h-0 flex">
          <!-- 左列：Run 列表 -->
          <div class="w-[210px] shrink-0 border-r border-gray-200 flex flex-col bg-gray-50/70">
            <div class="px-3 py-2 border-b border-gray-200">
              <div class="flex items-center justify-between">
                <span class="text-xs font-medium text-gray-500">最近回答</span>
                <button
                  @click="loadRuns()"
                  class="text-xs text-purple-600 hover:text-purple-700"
                >刷新</button>
              </div>
            </div>
            <div class="flex-1 overflow-y-auto p-2 space-y-1">
              <div v-if="runs.length === 0" class="text-xs text-gray-400 text-center py-8 px-2">
                暂无回答记录<br>发送一次对话后出现
              </div>
              <button
                v-for="r in runs"
                :key="r.id"
                @click="selectRun(r.id)"
                :class="[
                  'w-full text-left p-2.5 rounded-lg border transition-colors',
                  selectedRunId === r.id
                    ? 'border-purple-300 bg-purple-50'
                    : 'border-transparent hover:border-gray-200 hover:bg-white'
                ]"
              >
                <div class="flex items-center gap-1.5 mb-1">
                  <span :class="statusDot(r.status).dot" class="inline-block w-1.5 h-1.5 rounded-full"></span>
                  <span class="text-[11px] text-gray-400">#{{ r.id }}</span>
                  <span :class="statusDot(r.status).text" class="text-[10px] ml-auto font-medium">
                    {{ r.status === 'finished' ? '成功' : r.status === 'failed' ? '失败' : r.status === 'cancelled' ? '已停止' : '运行中' }}
                  </span>
                </div>
                <p class="text-xs text-gray-700 line-clamp-2 leading-snug">{{ r.user_message }}</p>
                <p class="text-[10px] text-gray-400 mt-1.5">
                  {{ r.started_at?.slice(5, 16) }} · {{ r.total_steps }}步<span v-if="r.total_tokens != null"> · {{ r.total_tokens }} Token</span>
                </p>
              </button>
            </div>
          </div>

          <!-- 右列：Step 详情时间轴 -->
          <div class="flex-1 min-w-0 flex flex-col">
            <div v-if="!selectedRunDetail" class="flex-1 flex items-center justify-center text-xs text-gray-400 px-4 text-center">
              从左侧选择一次回答，查看详细步骤
            </div>
            <template v-else>
              <!-- Run 概览 -->
              <div class="px-4 py-3 border-b border-gray-100">
                <p class="text-xs text-gray-500 mb-1">用户问题</p>
                <p class="text-sm text-gray-800 leading-snug">{{ selectedRunDetail.user_message }}</p>
                <div v-if="selectedRunDetail.final_answer" class="mt-2">
                  <p class="text-xs text-gray-500 mb-1">最终回答</p>
                  <p class="text-xs text-gray-700 leading-relaxed max-h-24 overflow-y-auto">
                    {{ selectedRunDetail.final_answer }}
                  </p>
                </div>
              </div>

              <!-- Steps 时间轴 -->
              <div class="flex-1 overflow-y-auto px-4 py-3 space-y-4">
                <div v-if="selectedRunDetail.steps.length === 0" class="text-xs text-gray-400 text-center py-8">
                  该次运行没有步骤记录
                </div>
                <div v-for="step in selectedRunDetail.steps" :key="step.step_no" class="relative pl-6">
                  <!-- 时间轴竖线 -->
                  <div class="absolute left-1.5 top-3 bottom-[-16px] w-px bg-gray-200"></div>
                  <!-- 时间轴节点 -->
                  <div
                    :class="[
                      'absolute -left-0.5 top-1 w-4 h-4 rounded-full border-2 bg-white flex items-center justify-center shrink-0',
                      stepIcon(step).border
                    ]"
                  >
                    <component :is="stepIcon(step).icon" :size="10" :class="stepIcon(step).text" />
                  </div>

                  <!-- Step 卡片 -->
                  <div class="border border-gray-200 rounded-lg p-3 bg-white hover:shadow-sm transition-shadow">
                    <div class="flex items-center gap-2 mb-1.5">
                      <span :class="['text-[10px] font-medium px-1.5 h-4 rounded flex items-center', stepIcon(step).tag]">
                        Step {{ step.step_no }} · {{ stepLabel(step) }}
                      </span>
                    </div>

                    <!-- thought / LLM 思考 -->
                    <div v-if="step.thought" class="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap bg-gray-50 rounded p-2 mb-2">
                      {{ step.thought.length > 400 ? step.thought.slice(0, 400) + '...' : step.thought }}
                    </div>

                    <!-- Tool 调用信息 -->
                    <div v-if="step.tool_name" class="space-y-1 text-xs">
                      <div class="flex items-start gap-2">
                        <span class="shrink-0 text-purple-600 font-medium">🔧 {{ toolDisplayName(step.tool_name) }}</span>
                      </div>
                      <details class="group">
                        <summary class="text-gray-500 cursor-pointer select-none hover:text-gray-700 list-none">
                          <span class="inline-flex items-center gap-1">
                            <span class="group-open:rotate-90 transition-transform">▶</span>
                            技术详情：调用内容
                          </span>
                        </summary>
                        <pre class="mt-1 p-2 rounded bg-gray-50 text-[11px] text-gray-700 overflow-x-auto">{{ prettyJson(step.tool_args) }}</pre>
                      </details>
                      <details v-if="step.tool_result" class="group">
                        <summary class="text-gray-500 cursor-pointer select-none hover:text-gray-700 list-none">
                          <span class="inline-flex items-center gap-1">
                            <span class="group-open:rotate-90 transition-transform">▶</span>
                            技术详情：返回内容
                          </span>
                        </summary>
                        <pre class="mt-1 p-2 rounded bg-emerald-50 text-[11px] text-emerald-800 overflow-x-auto max-h-48 overflow-y-auto">{{ step.tool_result }}</pre>
                      </details>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </aside>
    </Transition>
    
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { toolDisplayName } from '../utils/displayNames'
import { ref, onMounted, nextTick, watch, computed, onBeforeUnmount } from 'vue'
import { useRoute ,useRouter } from 'vue-router'
import { renderMarkdown } from '../utils/markdown'
import * as agentApi from '../api/agent'
import * as convApi from '../api/conversation'
import * as chatApi from '../api/chat'
import * as llmConfigApi from '../api/llmConfig'
import * as runApi from '../api/run'
import type { LlmConfig } from '../api/llmConfig'
import type { AgentRun, RunDetail } from '../api/run'
import type { Citation } from '../api/chat'
import CitationList from '../components/knowledge/CitationList.vue'
import RagSavingsBar from '../components/knowledge/RagSavingsBar.vue'
import AgentSubnav from '../components/agent/AgentSubnav.vue'
import {
  BookOpen, PlusCircle, MoreHorizontal, Pencil, Trash2, Pin, Archive,
  GitBranch, X, Wrench, Sparkles, AlertTriangle, Download, Search, ChevronLeft, ArrowUp, Square, PanelLeft,
  Paperclip, FileText
} from 'lucide-vue-next'
import { toastError, toastSuccess } from '../utils/toast'
import { getErrorMessage } from '../utils/request'
import { downloadFile } from '../utils/download'
import * as attachmentApi from '../api/attachment'
import type { Attachment } from '../api/attachment'
const router = useRouter()
const route = useRoute()
const agentId = computed(() => Number(route.params.agentId))

const currentAgent = ref<any>(null)
const configs = ref<LlmConfig[]>([])
const conversations = ref<any[]>([])
const conversationQuery = ref('')
const conversationFilter = ref<'all' | 'active' | 'archived'>('active')
const currentConversationId = ref<number | null>(null)
const messages = ref<any[]>([])
const inputText = ref('')
const convDrawer = ref(false)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const autoGrow = () => {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}
watch(inputText, () => nextTick(autoGrow))
const loading = ref(false)
const abortController = ref<AbortController | null>(null)
const messageListRef = ref<HTMLElement | null>(null)
const eventTraces = ref<any[]>([])
const configLoadError = ref('')
// ====== 运行轨迹 Drawer ======
const showTraceDrawer = ref(false)
const runs = ref<AgentRun[]>([])
const selectedRunId = ref<number | null>(null)
const selectedRunDetail = ref<RunDetail | null>(null)

const loadRuns = async (convId?: number | null) => {
  // 没有当前会话时 → 清空轨迹（新会话还没产生 run）
  const targetConvId = convId !== undefined ? convId : currentConversationId.value
  if (!targetConvId) {
    runs.value = []
    selectedRunId.value = null
    selectedRunDetail.value = null
    return
  }
  try {
    runs.value = await runApi.listRuns(agentId.value, 30, targetConvId)
    // 自动选中最新一条 run
    if (runs.value.length > 0 && selectedRunId.value === null) {
      await selectRun(runs.value[0].id)
    }
  } catch (e: any) {
    console.error('加载运行轨迹失败:', e)
    toastError(getErrorMessage(e, '加载运行轨迹失败'))
    runs.value = []
  }
}
const selectRun = async (runId: number) => {
  selectedRunId.value = runId
  try {
    selectedRunDetail.value = await runApi.getRunSteps(runId)
  } catch (e: any) {
    console.error('加载运行步骤失败:', e)
    toastError(getErrorMessage(e, '加载运行步骤失败'))
  }
}
// 运行状态颜色标签
const statusDot = (s: string) => ({
  running: { dot: 'bg-amber-400', text: 'text-amber-600' },
  finished: { dot: 'bg-emerald-500', text: 'text-emerald-600' },
  failed: { dot: 'bg-red-500', text: 'text-red-600' },
}[s] || { dot: 'bg-gray-400', text: 'text-gray-500' })

// Step 图标 + 标签
const stepIcon = (s: any) => {
  const t = String(s.step_type || '').toLowerCase()
  // 知识库检索
  if (t.includes('retrieve') || s.thought?.includes('检索') || s.thought?.includes('RAG')) {
    return { icon: BookOpen, text: 'text-amber-600', border: 'border-amber-300', tag: 'bg-amber-50 text-amber-700' }
  }
  if (t.includes('permission')) {
    return { icon: AlertTriangle, text: 'text-red-600', border: 'border-red-300', tag: 'bg-red-50 text-red-700' }
  }
  // Tool 调用
  if (t.includes('tool') || s.tool_name) {
    return { icon: Wrench, text: 'text-blue-600', border: 'border-blue-300', tag: 'bg-blue-50 text-blue-700' }
  }
  // 默认 LLM 思考
  return { icon: Sparkles, text: 'text-purple-600', border: 'border-purple-300', tag: 'bg-purple-50 text-purple-700' }
}
const stepLabel = (s: any) => {
  const t = String(s.step_type || '').toLowerCase()
  if (t.includes('retrieve')) return '知识库检索'
  if (t.includes('permission')) return '权限拒绝'
  if (t.includes('tool')) return '工具执行'
  if (s.tool_name) return '工具：' + toolDisplayName(s.tool_name)
  return 'LLM 推理'
}
const prettyJson = (v: any) => {
  if (v == null) return ''
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

const configuredModelNames = computed(() => new Set(configs.value.map((config) => config.model_name.toLowerCase())))
const hasCurrentModelKey = computed(() => {
  const modelName = String(currentAgent.value?.model_name || '').trim().toLowerCase()
  return Boolean(modelName && configuredModelNames.value.has(modelName))
})
const hasEmbeddingKey = computed(() => configs.value.some((config) => {
  const modelName = config.model_name.toLowerCase()
  return modelName.includes('embedding') || modelName.startsWith('baai/') || modelName === 'glm-4'
}))
const chatBlockedReason = computed(() => {
  if (configLoadError.value) return configLoadError.value
  if (!currentAgent.value) return '当前助手不存在或无权限访问'
  if (!hasCurrentModelKey.value) return `当前用户还没有配置「${currentAgent.value.model_name || '聊天模型'}」的密钥`
  if (currentAgent.value.rag_enabled === 1 && !hasEmbeddingKey.value) return '当前助手已开启资料库，但还没有配置资料检索模型密钥'
  return ''
})
const chatReady = computed(() => !chatBlockedReason.value)
const conversationFilters: Array<{ label: string; value: 'all' | 'active' | 'archived' }> = [
  { label: '正常', value: 'active' },
  { label: '全部', value: 'all' },
  { label: '归档', value: 'archived' },
]
const filterIndex = computed(() => Math.max(0, conversationFilters.findIndex((i) => i.value === conversationFilter.value)))
const filteredConversations = computed(() => {
  const query = conversationQuery.value.trim().toLowerCase()
  return conversations.value.filter((conversation) => {
    if (conversationFilter.value === 'active' && conversation.is_archived === 1) return false
    if (conversationFilter.value === 'archived' && conversation.is_archived !== 1) return false
    if (!query) return true
    return String(conversation.title || '').toLowerCase().includes(query)
  })
})

// 把回答正文里的 【来源N】 变成可点标记，点了就展开对应的来源片段
const renderAnswer = (text: string) =>
  renderMarkdown(text, true)

// ---- 附件：上传给助手的文件 / 脚本生成的文件下载 ----
const attachmentEnabled = ref(false)
const pendingAttachments = ref<Attachment[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const loadAttachmentStatus = async () => {
  try {
    attachmentEnabled.value = (await attachmentApi.getAttachmentStatus()).enabled
  } catch {
    attachmentEnabled.value = false
  }
}

const onFilesPicked = async (e: Event) => {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return
  uploading.value = true
  try {
    for (const file of files) {
      if (pendingAttachments.value.length >= 5) {
        toastError('一次最多添加 5 个附件')
        break
      }
      try {
        pendingAttachments.value.push(await attachmentApi.uploadAttachment(file))
      } catch (err: any) {
        toastError(`${file.name}：${getErrorMessage(err, '上传失败')}`)
      }
    }
  } finally {
    uploading.value = false
  }
}

const removeAttachment = (id: string) => {
  pendingAttachments.value = pendingAttachments.value.filter((a) => a.id !== id)
}

// 历史消息里的附件会带「附件ID」（给助手看的），展示时去掉
const displayUser = (text: string) => (text || '').replace(/（附件ID：[0-9a-f]{24}）/g, '')

function onAnswerClick(e: MouseEvent, msg: any) {
  const link = (e.target as HTMLElement)?.closest?.('a[href^="#attachment-"]') as HTMLAnchorElement | null
  if (link) {
    e.preventDefault()
    const id = (link.getAttribute('href') || '').replace('#attachment-', '')
    downloadFile(`/attachment/${id}/download`, undefined, link.textContent || 'download').catch((err) =>
      toastError(getErrorMessage(err, '文件已过期或不存在（生成的文件只保留几天）')),
    )
    return
  }
  const el = (e.target as HTMLElement)?.closest?.('.cite-ref') as HTMLElement | null
  if (!el) return
  const idx = Number(el.dataset.cite)
  if (Number.isFinite(idx)) msg.citeOpen = idx
}
const DEPARTMENT_LABELS: Record<string, string> = { hr: '人事', procurement: '采购', sales: '销售', finance: '财务', it: 'IT' }
const departmentLabel = (code?: string | null) => (code && DEPARTMENT_LABELS[code]) || code || '相关部门'
const short = (s: string, n: number) => {
  const s2 = s || ''
  return s2.length > n ? s2.slice(0, n) + '...' : s2
}

/** 高风险工具确认卡片的确认/取消按钮（第五轮审计 P0-2）。
 * evt 是 eventTraces 数组里那个具体的 confirmation_required 对象引用，
 * 直接改它的字段能触发响应式更新（不需要另外去数组里查 index）。 */
async function decideToolConfirmation(evt: any, approve: boolean) {
  if (evt.deciding || evt.decision) return
  evt.deciding = true
  try {
    if (approve) {
      const res = await chatApi.confirmToolCall(evt.token)
      evt.decision = 'confirmed'
      evt.resultText = res.result || ''
    } else {
      await chatApi.rejectToolCall(evt.token)
      evt.decision = 'rejected'
    }
  } catch (e: any) {
    evt.resultText = '处理失败：' + getErrorMessage(e, '请重试')
  } finally {
    evt.deciding = false
  }
}

const scrollToBottom = async () => {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}

const loadConversations = async () => {
  conversations.value = await convApi.listConversations(agentId.value)
}

const loadCurrentAgent = async () => {
  try {
    const agentList = await agentApi.listAgents()
    currentAgent.value = agentList.find((agent: any) => agent.id === agentId.value) || null
    if (!currentAgent.value) {
      // 列表只含自己创建的助手；企业中央助手、部门助手由管理员创建，有权使用时按 id 单独取。
      try { currentAgent.value = await agentApi.getAgent(agentId.value) } catch { currentAgent.value = null }
    }
  } catch (e: any) {
    console.error('加载助手信息失败:', e)
    toastError(getErrorMessage(e, '加载助手信息失败'))
    currentAgent.value = null
  }
}

const loadConfigs = async () => {
  configLoadError.value = ''
  try {
    configs.value = await llmConfigApi.listConfigs()
  } catch (e: any) {
    configs.value = []
    configLoadError.value = getErrorMessage(e, '无法读取模型连接')
  }
}

const selectConversation = async (convId: number) => {
  currentConversationId.value = convId
  messages.value = await convApi.listMessages(convId)
  eventTraces.value = []
  // 切会话 → 清空旧 run 选中 + 加载该会话的 runs
  selectedRunId.value = null
  selectedRunDetail.value = null
  await loadRuns(convId)
  scrollToBottom()
}

const createNewConversation = () => {
  currentConversationId.value = null
  messages.value = []
  eventTraces.value = []
  selectedRunId.value = null
  selectedRunDetail.value = null
  runs.value = []
}
// ====== 会话项：更多功能菜单 ======
const menuConvId = ref<number | null>(null)
const editingConvId = ref<number | null>(null)
const renameText = ref('')
const renameInputRef = ref<HTMLInputElement | null>(null)

const openMenu = (convId: number) => {
  // 关闭其它：有则无，无则打开当前行（再次点同个按钮就关闭）
  menuConvId.value = menuConvId.value === convId ? null : convId
}

// 点击空白处关闭菜单
const onDocClick = () => {
  if (!menuConvId.value) return
  // 点在按钮/弹窗里的已经用 @click.stop 吞了
  menuConvId.value = null
}
onMounted(() => { document.addEventListener('click', onDocClick) })
onBeforeUnmount(() => { document.removeEventListener('click', onDocClick) })
// 记得把 onBeforeUnmount 加到 import（没加就加在 vue 的 import 里）

const isPinned = (id: number) => conversations.value.find(c => c.id === id)?.is_pinned === 1
const isArchived = (id: number) => conversations.value.find(c => c.id === id)?.is_archived === 1

const togglePin = async (id: number) => {
  const next = isPinned(id) ? 0 : 1
  try {
    const updated = await convApi.updateConversationFlags(id, { is_pinned: next })
    const idx = conversations.value.findIndex(x => x.id === id)
    if (idx !== -1) conversations.value[idx] = { ...conversations.value[idx], ...updated }
    resortConversations()
  } catch (e: any) {
    toastError(getErrorMessage(e, '置顶失败'))
  } finally {
    menuConvId.value = null
  }
}

const toggleArchive = async (c: any) => {
  const next = isArchived(c.id) ? 0 : 1
  try {
    const updated = await convApi.updateConversationFlags(c.id, { is_archived: next })
    const idx = conversations.value.findIndex(x => x.id === c.id)
    if (idx !== -1) conversations.value[idx] = { ...conversations.value[idx], ...updated }
    resortConversations()
    if (currentConversationId.value === c.id && next === 1) {
      const nextConv = filteredConversations.value.find(x => x.id !== c.id && !isArchived(x.id))
      nextConv ? selectConversation(nextConv.id) : createNewConversation()
    }
  } catch (e: any) {
    toastError(getErrorMessage(e, '归档失败'))
  } finally {
    menuConvId.value = null
  }
}

// 归档的会话沉底；置顶的浮顶
const resortConversations = () => {
  const all = [...conversations.value]
  all.sort((a, b) => {
    const pa = isArchived(a.id) ? 2 : isPinned(a.id) ? 0 : 1
    const pb = isArchived(b.id) ? 2 : isPinned(b.id) ? 0 : 1
    if (pa !== pb) return pa - pb
    return String(b.update_time || '').localeCompare(String(a.update_time || ''))
  })
  conversations.value = all
}

// ====== 重命名 ======
const startRename = (c: any) => {
  editingConvId.value = c.id
  renameText.value = c.title
  menuConvId.value = null
  // DOM 更新后聚焦
  nextTick(() => {
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
  })
}

const cancelRename = () => {
  editingConvId.value = null
  renameText.value = ''
}

const commitRename = async (convId: number) => {
  const newTitle = renameText.value.trim()
  if (newTitle && editingConvId.value === convId) {
    // 调 API 改标题
    try {
      const updated = await convApi.updateConversationTitle(convId, newTitle)
      const idx = conversations.value.findIndex(x => x.id === convId)
      if (idx !== -1 && updated) {
        conversations.value[idx] = { ...conversations.value[idx], ...updated }
      }
    } catch (e: any) {
      toastError(getErrorMessage(e, '重命名失败'))
    }
  }
  cancelRename()
}

// ====== 删除 ======
const handleDeleteConv = async (c: any) => {
  if (!confirm(`确认删除会话「${c.title}」？该操作不可恢复。`)) return
  try {
    await convApi.deleteConversation(c.id)
    menuConvId.value = null
    await loadConversations()
    resortConversations()
    if (currentConversationId.value === c.id) {
      createNewConversation()
      if (conversations.value.length > 0) selectConversation(conversations.value[0].id)
    }
  } catch (e: any) {
    toastError(getErrorMessage(e, '删除失败'))
  }
}

const handleExportConv = async (c: any, format: 'markdown' | 'json') => {
  try {
    const blob = await convApi.exportConversation(c.id, format)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${c.title || 'conversation'}_${c.id}.${format === 'json' ? 'json' : 'md'}`
    link.click()
    URL.revokeObjectURL(url)
    menuConvId.value = null
    toastSuccess('会话已导出')
  } catch (e: any) {
    toastError(getErrorMessage(e, '导出失败'))
  }
}
/** 跨部门请求：把原话带到部门工作台的“协同办理”（只在本浏览器会话里暂存，不放进地址栏）。 */
function openOrchestration(message?: string) {
  try {
    if (message) sessionStorage.setItem('orchestration_draft', message)
  } catch { /* 存不住就让用户在协同办理里重新输入 */ }
  router.push('/department')
}

// 回车发送；输入法选词的回车留给输入法（不能 preventDefault，否则选不了词），Shift+回车换行由 .exact 排除
const onComposerEnter = (event: KeyboardEvent) => {
  if (isImeEnter(event)) return
  event.preventDefault()
  void sendMessage()
}

const sendMessage = async () => {
  const msg = inputText.value.trim()
  if (!msg || loading.value) return
  if (!chatReady.value) {
    toastError(chatBlockedReason.value)
    return
  }

  loading.value = true
  abortController.value = new AbortController()
  eventTraces.value = []
  // 先加 user 消息到 UI
  const attached = pendingAttachments.value
  pendingAttachments.value = []
  messages.value.push({
    role: 'user',
    content: attached.length ? `${msg}\n\n【用户上传的附件】\n${attached.map((a) => `- ${a.name}`).join('\n')}` : msg,
  })
  inputText.value = ''
  scrollToBottom()

  // 临时占位 assistant 消息，流式往里面塞内容
  const placeholderIdx = messages.value.length
  messages.value.push({ role: 'assistant', content: '' })

  try {
    await chatApi.sendStream({
      agentId: agentId.value,
      conversationId: currentConversationId.value,
      message: msg,
      attachmentIds: attached.map((a) => a.id),
      onEvent: async (evt) => {
                if (evt.type === 'route') {
          eventTraces.value.push({ ...evt, message: msg })
        } else if (evt.type === 'ready' && evt.run_id) {
          // 可选：记 run_id 供轨迹
        } else if (evt.type === 'retrieval') {
          eventTraces.value.push({ type: 'retrieval', hit_count: evt.hit_count, stats: evt.stats })
          if (evt.stats) messages.value[placeholderIdx].ragStats = evt.stats
        } else if (evt.type === 'citations') {
          messages.value[placeholderIdx].citations = (evt.citations || []) as Citation[]
        } else if (evt.type === 'thinking') {
          if (evt.tool_calls?.length) messages.value[placeholderIdx].content = ''
          eventTraces.value.push({ type: 'thinking', content: evt.content || '' })
        } else if (evt.type === 'tool_call') {
          eventTraces.value.push({ type: 'tool_call', name: evt.name, args: evt.args || {} })
        } else if (evt.type === 'tool_result') {
          const pending = chatApi.parseConfirmationRequired(evt.result)
          if (pending) {
            eventTraces.value.push({
              type: 'confirmation_required',
              name: evt.name,
              token: pending.confirmation_token,
              args: pending.tool_args,
              decision: null as 'confirmed' | 'rejected' | null,
              resultText: '',
              deciding: false,
            })
          } else {
            eventTraces.value.push({ type: 'tool_result', name: evt.name, result: evt.result || '' })
          }
        } else if (evt.type === 'answer_delta') {
          messages.value[placeholderIdx].content += evt.content || ''
        } else if (evt.type === 'answer') {
          // answer 事件：完整内容替换（因为是 answer 而不是 token 流）
          messages.value[placeholderIdx].content = evt.content || ''
               } else if (evt.type === 'done') {
          if (evt.tokens != null) messages.value[placeholderIdx].tokens = evt.tokens
          // 完成：刷新会话列表 + 按当前会话刷新运行轨迹
          if (evt.conversation_id) {
            currentConversationId.value = evt.conversation_id
            await loadConversations()
            resortConversations()
          }
          await loadRuns(evt.conversation_id || currentConversationId.value)
      
        } else if (evt.type === 'error') {
          messages.value[placeholderIdx].content = '发送失败：' + (evt.message || evt.detail || '后端处理出错')
        }
        scrollToBottom()
      },
      signal: abortController.value.signal,
    })
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      messages.value[placeholderIdx].content = messages.value[placeholderIdx].content || '已停止生成'
    } else {
      messages.value[placeholderIdx].content = '发送失败：' + getErrorMessage(e, '后端处理出错')
    }
  } finally {
    loading.value = false
    abortController.value = null
    scrollToBottom()
  }
}

const stopGenerating = () => {
  abortController.value?.abort()
}

onMounted(async () => {
  void loadAttachmentStatus()
  await Promise.all([loadCurrentAgent(), loadConfigs()])
  await loadConversations()
  resortConversations()
  if (conversations.value.length > 0) {
    await selectConversation(conversations.value[0].id)  // ← 里面会 loadRuns(convId)
  } else {
    await loadRuns()  // 无会话 → 清空
  }
})

// 切 Agent 时刷新
watch(agentId, async () => {
  await Promise.all([loadCurrentAgent(), loadConfigs()])
  createNewConversation()              // 内部会清空 runs
  selectedRunId.value = null
  selectedRunDetail.value = null
  await loadConversations()
  resortConversations()
  if (conversations.value.length > 0) {
    await selectConversation(conversations.value[0].id)  // ← 里面会 loadRuns(convId)
  }
})
</script>
<style scoped>
/* 图标整体向上偏移 0.7px（SVG 字形设计的视觉中心略低于盒子中心） */
.icon-shift {
  transform: translateY(-0.7px);
  flex-shrink: 0;
}
/* Drawer 过渡 */
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-active, .fade-leave-active { transition: opacity .18s ease; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
.slide-enter-active, .slide-leave-active { transition: transform .24s ease; }
/* 回答正文里的 【来源N】 标记（v-html 注入，用 :deep 命中） */
:deep(.cite-ref) {
  color: var(--accent);
  cursor: pointer;
  font-size: 0.85em;
  white-space: nowrap;
}
:deep(.cite-ref:hover) { text-decoration: underline; }
</style>
