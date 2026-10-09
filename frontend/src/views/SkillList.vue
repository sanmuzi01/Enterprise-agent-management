<template>
  <div :class="[isAdminView ? 'h-full' : 'h-screen', 'flex flex-col bg-transparent']">
    <header class="min-h-16 border-b border-sky-200/70 ui-glass px-5 py-3 text-slate-900 backdrop-blur-xl flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex items-center gap-3">
        <button
          v-if="!isAdminView"
          @click="router.push('/agents')"
          class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-500 hover:bg-black/[.06] hover:text-slate-900"
          title="返回工作台"
        >
          <ArrowLeft :size="16" />
        </button>
        <div>
          <h1 class="text-base font-semibold text-slate-950">技能中心</h1>
          <p class="text-xs text-slate-500">{{ isAdmin ? '维护并发布可供用户选择的助手技能。' : '创建或编辑助手时，可直接选择这里的技能。' }}</p>
        </div>
      </div>
      <div v-if="isAdmin" class="flex flex-wrap items-center gap-2">
        <button
          @click="openImport"
          class="inline-flex items-center gap-2 rounded border border-sky-200 bg-white/80 px-3 py-2 text-sm text-slate-700 hover:bg-sky-50"
        >
          <Upload :size="15" />
          导入能力包
        </button>
        <button
          v-if="isAdmin"
          @click="handleReanalyze"
          :disabled="reanalyzing"
          class="inline-flex items-center gap-2 rounded border border-sky-200 bg-white/80 px-3 py-2 text-sm text-slate-700 hover:bg-sky-50 disabled:text-slate-300"
          title="沙箱依赖变化后，刷新每个 Skill 的「脚本可运行」标签，不用重新导入"
        >
          <RefreshCw :size="15" :class="reanalyzing ? 'animate-spin' : ''" />
          {{ reanalyzing ? '检查中…' : '重新检查脚本' }}
        </button>
        <button
          @click="openCreate"
          class="ui-primary inline-flex items-center gap-2 rounded px-3 py-2 text-sm font-medium text-white"
        >
          <Plus :size="15" />
          新建能力
        </button>
        <button
          @click="openTemplateCreate"
          class="inline-flex items-center gap-2 rounded border border-sky-200 bg-white/80 px-3 py-2 text-sm text-slate-700 hover:bg-sky-50"
        >
          <Plus :size="15" />
          新建样板
        </button>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto p-6">
      <div class="mx-auto max-w-6xl">
        <section v-if="isAdmin" class="mb-5">
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-slate-950">从样板开始</h2>
            <span class="text-xs text-slate-500">适合不知道怎么写能力的新用户</span>
          </div>
          <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            <article
              v-for="template in templates"
              :key="template.filename"
              class="ui-card rounded-lg p-4"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <h3 class="truncate text-sm font-semibold text-slate-900">{{ template.name }}</h3>
                  <p class="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500">{{ template.description || template.filename }}</p>
                </div>
                <span :class="template.editable ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'" class="shrink-0 rounded px-2 py-1 text-xs">
                  {{ template.editable ? '我的样板' : '内置' }}
                </span>
              </div>
              <p class="mt-3 truncate text-xs text-slate-400">{{ (template.tool_names || []).map(toolDisplayName).join(' / ') || '不需要额外工具' }}</p>
              <div class="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-3">
                <button
                  @click="createFromTemplate(template)"
                  class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                >
                  使用
                </button>
                <button
                  v-if="template.editable"
                  @click="openTemplateEdit(template)"
                  class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                >
                  编辑
                </button>
                <button
                  v-if="template.editable"
                  @click="handleTemplateDelete(template)"
                  class="rounded border border-red-100 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
                >
                  删除
                </button>
              </div>
            </article>
          </div>
        </section>

        <div class="mb-4 flex items-center justify-between">
          <div v-if="isAdmin" class="inline-flex rounded border border-sky-200 bg-white/70 p-1 backdrop-blur">
            <button
              @click="activeTab = 'mine'"
              :class="activeTab === 'mine' ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-200' : 'text-slate-600 hover:bg-sky-50'"
              class="rounded px-3 py-1.5 text-sm"
            >
              全部技能
            </button>
            <button
              @click="activeTab = 'public'"
              :class="activeTab === 'public' ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-200' : 'text-slate-600 hover:bg-sky-50'"
              class="rounded px-3 py-1.5 text-sm"
            >
              能力商店
            </button>
          </div>
          <div class="flex items-center gap-3">
            <span v-if="importError" class="text-xs text-red-600">{{ importError }}</span>
            <button @click="reload" class="text-xs text-sky-600 hover:text-sky-800">刷新</button>
          </div>
        </div>

        <div v-if="shownSkills.length === 0" class="rounded-lg border border-dashed border-sky-300/70 bg-white/62 py-16 text-center text-sm text-slate-500 backdrop-blur">
          <template v-if="activeTab === 'mine'">
            平台还没有技能。可以
            <button class="text-[var(--accent)] hover:underline" @click="openImport">导入官方 / GitHub 上的 Skill</button>
            ，或从上面的样板开始。
          </template>
          <template v-else>暂无可用技能。</template>
        </div>

        <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="skill in shownSkills"
            :key="skill.id"
            class="transition-all duration-500 ease-[var(--spring)] hover:-translate-y-0.5 hover:shadow-[var(--sh-2)] ui-card rounded-lg p-4 transition"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="flex min-w-0 items-center gap-3">
                <span class="inline-flex h-9 w-9 items-center justify-center rounded bg-violet-50 text-violet-700">
                  <Zap :size="18" />
                </span>
                <div class="min-w-0">
                  <h2 class="truncate text-sm font-semibold text-slate-900">{{ skill.name }}</h2>
                  <p class="truncate text-xs text-slate-500">{{ skill.description || '可添加到助手的工作能力' }}</p>
                </div>
              </div>
              <span
                v-if="activeTab === 'public' && skill.is_official"
                class="shrink-0 rounded bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700"
                title="由管理员审核上架"
              >官方</span>
              <span v-else :class="skill.is_public === 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'" class="shrink-0 rounded px-2 py-1 text-xs">
                {{ skill.is_public === 1 ? '公开' : '私有' }}
              </span>
              <span
                v-if="isAdmin && (activeTab === 'mine' || isMySkill(skill))"
                :class="lifecycleBadgeClass(skill.lifecycle_status)"
                class="shrink-0 rounded px-2 py-1 text-xs"
                :title="skill.is_public === 1 && skill.lifecycle_status !== 'published' ? '只有「已发布」的公开能力才会出现在能力商店里' : ''"
              >
                {{ lifecycleLabel(skill.lifecycle_status) }}
              </span>
            </div>

            <p class="mt-4 min-h-10 text-sm leading-relaxed text-slate-600">{{ skill.description || '暂无描述' }}</p>
            <div class="mt-3 rounded border px-3 py-2 text-xs" :class="validationBoxClass(skill.id)">
              <div class="flex items-center justify-between gap-2">
                <span class="inline-flex min-w-0 items-center gap-1.5">
                  <component :is="validationIcon(skill.id)" :size="14" class="shrink-0" />
                  <span class="truncate">{{ validationText(skill.id) }}</span>
                </span>
                <button
                  @click="openValidationPreview(skill.id)"
                  class="shrink-0 rounded px-1.5 py-0.5 hover:bg-white/70"
                  title="查看可用性检查详情"
                >
                  详情
                </button>
              </div>
              <div v-if="validationMap[skill.id]?.tool_names?.length" class="mt-2 flex flex-wrap gap-1">
                <span
                  v-for="toolName in validationMap[skill.id].tool_names"
                  :key="toolName"
                  class="rounded bg-white/70 px-1.5 py-0.5 text-[11px]"
                >
                  {{ toolDisplayName(toolName) }}
                </span>
              </div>
              <div class="mt-2 flex flex-wrap gap-1">
                <span class="rounded bg-white/70 px-1.5 py-0.5 text-[11px]">
                  文件资源 {{ validationMap[skill.id]?.allowed_resource_count || 0 }}/{{ validationMap[skill.id]?.resource_count || 0 }}
                </span>
                <span
                  v-if="validationMap[skill.id]?.permissions?.network"
                  class="rounded bg-white/70 px-1.5 py-0.5 text-[11px]"
                >
                  可联网
                </span>
              </div>
              <div v-if="scriptBadge(skill.id)" class="mt-2">
                <span
                  :class="scriptBadge(skill.id)!.cls"
                  class="inline-block rounded px-1.5 py-0.5 text-[11px] font-medium"
                  :title="scriptBadge(skill.id)!.title"
                >
                  {{ scriptBadge(skill.id)!.text }}
                </span>
              </div>
            </div>

            <div v-if="isAdmin" class="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-3">
              <button
                v-if="activeTab === 'public' && !isMySkill(skill)"
                @click="handleInstall(skill)"
                :disabled="installingId === skill.id"
                class="rounded border border-sky-200 px-3 py-1.5 text-xs text-sky-700 hover:bg-sky-50 disabled:text-slate-300"
              >
                {{ installingId === skill.id ? '安装中...' : '安装' }}
              </button>
              <button
                v-if="(activeTab === 'mine' || isMySkill(skill)) && needsTranslation(skill)"
                @click="handleTranslate(skill)"
                :disabled="translatingId === skill.id"
                class="rounded border border-sky-200 px-3 py-1.5 text-xs text-sky-700 hover:bg-sky-50 disabled:text-slate-300"
                title="名称和说明是英文，用你自己的模型翻译成中文"
              >
                {{ translatingId === skill.id ? '翻译中…' : '翻译成中文' }}
              </button>
              <button
                v-if="isAdmin && (activeTab === 'mine' || isMySkill(skill))"
                @click="openVersions(skill)"
                class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                title="历史版本（改错了可以恢复）"
              >
                <History :size="15" />
              </button>
              <button
                v-if="activeTab === 'mine' || isMySkill(skill)"
                @click="openEdit(skill)"
                class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                title="编辑"
              >
                <Pencil :size="15" />
              </button>
              <button
                v-if="activeTab === 'mine' || isMySkill(skill)"
                @click="handleExport(skill)"
                class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-blue-50 hover:text-blue-700"
                title="导出"
              >
                <Download :size="15" />
              </button>
              <button
                v-if="activeTab === 'mine' || isMySkill(skill)"
                @click="handleDelete(skill)"
                class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-red-50 hover:text-red-600"
                title="删除"
              >
                <Trash2 :size="15" />
              </button>
            </div>
          </article>
        </div>
      </div>
    </main>

    <!-- 导入能力 -->
    <div v-if="showImport" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="closeImport">
      <div class="w-full max-w-xl rounded-lg bg-white shadow-xl">
        <header class="flex h-14 items-center justify-between border-b border-slate-200 px-5">
          <h2 class="text-base font-semibold text-slate-900">{{ importResult ? '导入完成' : '导入能力' }}</h2>
          <button @click="closeImport" class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100" title="关闭">
            <X :size="16" />
          </button>
        </header>

        <!-- 结果 -->
        <main v-if="importResult" class="max-h-[72vh] space-y-4 overflow-y-auto p-5">
          <div v-for="s in importResult.imported" :key="s.id" class="rounded-lg border border-emerald-100 bg-emerald-50/60 p-3">
            <p class="flex items-center gap-1.5 text-sm font-medium text-emerald-800">
              <CheckCircle2 :size="15" class="shrink-0" />
              <span class="truncate">{{ s.name }}</span>
            </p>
            <p class="mt-1 text-xs text-emerald-700/80">
              工作流程说明已生效<template v-if="s.script_count">；{{ s.script_count }} 个 Python 脚本已保存</template><template v-if="s.resource_count">；参考文档 {{ s.resource_count }} 个（{{ s.prompt_resource_count }} 个已附加给助手）</template>
            </p>
            <div v-if="needsTranslation(s)" class="mt-2 flex items-center justify-between gap-2 rounded bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
              <span>名称和说明是英文，用户可能看不懂</span>
              <button
                type="button"
                :disabled="translatingId === s.id"
                class="shrink-0 rounded bg-white px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                @click="handleTranslate(s)"
              >
                {{ translatingId === s.id ? '翻译中…' : '翻译成中文' }}
              </button>
            </div>
            <ul v-if="s.notes.length" class="mt-2 space-y-1 border-t border-emerald-100 pt-2 text-xs text-slate-600">
              <li v-for="(n, i) in s.notes" :key="i" class="flex gap-1.5">
                <span class="text-slate-400">·</span>{{ n }}
              </li>
            </ul>
          </div>
          <div v-for="f in importResult.failed" :key="f.name" class="rounded-lg border border-red-100 bg-red-50/60 p-3">
            <p class="flex items-center gap-1.5 text-sm font-medium text-red-700">
              <AlertTriangle :size="15" class="shrink-0" />
              <span class="truncate">{{ f.name }} 导入失败</span>
            </p>
            <p class="mt-1 text-xs text-red-600/90">{{ f.error }}</p>
          </div>
          <p class="text-xs leading-relaxed text-slate-500">
             导入后会出现在「全部技能」里。公开后，普通用户可在创建或编辑助手时选择使用。
          </p>
        </main>

        <!-- 选择来源 -->
        <main v-else class="max-h-[72vh] space-y-5 overflow-y-auto p-5">
          <p class="text-sm leading-relaxed text-slate-600">
            <b class="font-medium text-slate-900">能力（Skill）</b>是一份写给助手的工作说明书：什么场景用、按什么步骤做、输出什么格式。
            可以直接导入 <b class="font-medium text-slate-900">Anthropic 官方 Skill</b> 和 <b class="font-medium text-slate-900">GitHub 上的 Skill 仓库</b>。
          </p>

          <div class="inline-flex rounded border border-sky-200 bg-white/70 p-1">
            <button
              v-for="t in importTabs"
              :key="t.key"
              @click="importTab = t.key"
              :class="importTab === t.key ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-200' : 'text-slate-600 hover:bg-sky-50'"
              class="rounded px-3 py-1.5 text-sm"
            >
              {{ t.label }}
            </button>
          </div>

          <div v-if="importTab === 'file'" class="space-y-2">
            <input ref="importInput" type="file" class="hidden" accept=".zip,.md,.yml,.yaml" @change="handleImportSelect" />
            <button
              type="button"
              :disabled="importing"
              @click="importInput?.click()"
              @dragover.prevent
              @drop.prevent="handleImportDrop"
              class="flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed border-sky-300 bg-sky-50/40 px-4 py-8 text-center hover:bg-sky-50 disabled:opacity-60"
            >
              <Upload :size="20" class="text-sky-600" />
              <span class="text-sm font-medium text-slate-800">{{ importing ? '导入中…' : '点击选择文件，或拖到这里' }}</span>
              <span class="text-xs text-slate-500">支持 .zip · SKILL.md · .yml / .yaml，最大 50MB</span>
            </button>
            <p class="text-xs leading-relaxed text-slate-500">
              .zip 可以是<b class="font-medium text-slate-700">单个 Skill 文件夹</b>，也可以是
              <b class="font-medium text-slate-700">整个 GitHub 仓库的 Download ZIP</b>（里面有多个 Skill 会全部导入）。
              也支持从本平台「导出」的能力包。
            </p>
          </div>

          <div v-else class="space-y-2">
            <div class="flex gap-2">
              <input
                id="github-url"
                v-model="githubUrl"
                :disabled="importing"
                @keydown.enter="!isImeEnter($event) && handleGithubImport()"
                class="h-10 min-w-0 flex-1 rounded border border-slate-300 px-3 text-sm outline-none focus:border-sky-500"
                placeholder="https://github.com/anthropics/skills"
              />
              <button
                type="button"
                :disabled="importing || !githubUrl.trim()"
                @click="handleGithubImport"
                class="ui-primary h-10 shrink-0 px-4 text-sm font-medium disabled:opacity-50"
              >
                {{ importing ? '下载中…' : '导入' }}
              </button>
            </div>
            <p class="text-xs leading-relaxed text-slate-500">
              粘贴<b class="font-medium text-slate-700">仓库地址</b>会导入其中所有 Skill；
              粘贴<b class="font-medium text-slate-700">某个文件夹的地址</b>
              （如 <span class="break-all">…/tree/main/skills/pdf</span>）只导入那一个。只支持公开仓库。
            </p>
            <p class="rounded bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
              服务器在国内，可能访问不到 GitHub。失败时请在自己电脑上打开链接，点 Code → Download ZIP，再回到「上传文件」导入。
            </p>
          </div>

          <label class="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50/50 px-3 py-2.5 text-xs leading-relaxed text-slate-600">
            <input id="import-public" v-model="importPublic" type="checkbox" class="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <b class="font-medium text-slate-800">同时公开到能力商店</b>
              ：所有用户都能在编辑助手时勾选它（用户不能自己导入、创建或修改技能）。Python 脚本会保留，
              只有你导入并审核过的脚本才会进沙箱运行，上架前请自己看一遍说明内容。
            </span>
          </label>

          <p v-if="importFormError" class="rounded bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-600">{{ importFormError }}</p>

          <div class="rounded-lg bg-slate-50 p-4">
            <p class="text-xs font-medium text-slate-700">导入后能用到什么程度</p>
            <ul class="mt-2 space-y-1.5 text-xs leading-relaxed text-slate-600">
              <li class="flex gap-2"><span class="text-emerald-600">✓</span>工作流程、规范、输出格式（SKILL.md 正文）：完整生效</li>
              <li class="flex gap-2"><span class="text-emerald-600">✓</span>参考文档（.md .txt .json .csv）：一并保存，在字数预算内附加给助手</li>
              <li v-if="sandboxEnabled" class="flex gap-2">
                <span class="text-emerald-600">✓</span>
                Python 脚本（.py）：在隔离沙箱里运行。聊天输入框里可以点回形针上传文件让脚本处理，生成的文件能直接下载
              </li>
              <li v-else class="flex gap-2">
                <span class="text-amber-500">!</span>
                Python 脚本（.py）：会保存，但服务器的脚本沙箱还没开启，暂时不能运行；管理员开启后自动生效
              </li>
              <li class="flex gap-2"><span class="text-red-500">✗</span>其他语言的脚本（.sh .js 等）：不能运行，会跳过</li>
              <li v-if="!sandboxEnabled" class="flex gap-2"><span class="text-red-500">✗</span>图片、PDF、Office 模板等文件：不导入（带 Python 脚本且沙箱开启时会随脚本保存）</li>
              <li class="flex gap-2"><span class="text-red-500">✗</span>官方的 allowed-tools：那是 Claude Code 的工具，本平台没有，只能用平台自己的工具</li>
            </ul>
            <p v-if="sandboxEnabled" class="mt-3 border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
              沙箱的限制：<b class="font-medium text-slate-700">没有网络</b>、不能安装依赖（只预装了 PDF / Excel / Word / pandas 等常用库）、
              单次最长约 30 秒。依赖 Node.js、LibreOffice 或联网的脚本会报错。
            </p>
            <p v-else class="mt-3 border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
              沙箱没开启时，更适合<b class="font-medium text-slate-700">写作、审阅、分析、沟通规范</b>这类靠提示词完成的 Skill；
              依赖运行脚本才能处理文件的（如生成 Excel、编辑 PDF）暂时效果有限。
            </p>
          </div>
        </main>

        <footer v-if="importResult" class="flex justify-end border-t border-slate-200 px-5 py-3">
          <button @click="closeImport" class="ui-primary h-9 px-5 text-sm font-medium">完成</button>
        </footer>
      </div>
    </div>

    <div v-if="showDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="closeDialog">
      <div class="w-full max-w-2xl rounded-lg bg-white shadow-xl">
        <header class="flex h-14 items-center justify-between border-b border-slate-200 px-5">
          <h2 class="text-base font-semibold text-slate-900">{{ editing ? '编辑能力' : '新建能力' }}</h2>
          <button @click="closeDialog" class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100" title="关闭">
            <X :size="16" />
          </button>
        </header>

        <main class="max-h-[72vh] space-y-4 overflow-y-auto p-5">
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label class="mb-1 block text-xs font-medium text-slate-600">名称</label>
              <input v-model="form.name" class="h-10 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-violet-500" placeholder="例如：合同审阅助手" />
            </div>
            <div>
              <label class="mb-1 block text-xs font-medium text-slate-600">创建方式</label>
              <select
                v-model="form.template_filename"
                :disabled="!!editing"
                class="h-10 w-full rounded border border-slate-300 bg-white px-3 text-sm outline-none focus:border-violet-500 disabled:bg-slate-100"
              >
                <option value="">自定义能力</option>
                <option v-for="t in templates" :key="t.filename" :value="t.filename">
                  从样板创建：{{ t.name }}
                </option>
              </select>
            </div>
          </div>

          <div v-if="!editing" class="grid grid-cols-1 gap-2 md:grid-cols-2">
            <button
              type="button"
              @click="selectBlankTemplate"
              :class="!form.template_filename ? 'border-violet-500 bg-violet-50' : 'border-slate-200 bg-white hover:bg-slate-50'"
              class="rounded border p-3 text-left"
            >
              <span class="block text-sm font-medium text-slate-900">自定义能力</span>
              <span class="mt-1 block text-xs leading-relaxed text-slate-500">从零选择工具、填写工作规则，适合完全自定义能力。</span>
            </button>
            <button
              v-for="template in templates"
              :key="template.filename"
              type="button"
              @click="applyTemplate(template)"
              :class="form.template_filename === template.filename ? 'border-violet-500 bg-violet-50' : 'border-slate-200 bg-white hover:bg-slate-50'"
              class="rounded border p-3 text-left"
            >
              <span class="block text-sm font-medium text-slate-900">{{ template.name }}</span>
              <span class="mt-1 line-clamp-2 block text-xs leading-relaxed text-slate-500">{{ template.description || template.filename }}</span>
              <span class="mt-2 block truncate text-xs text-slate-400">{{ (template.tool_names || []).map(toolDisplayName).join(' / ') || '不需要额外工具' }}</span>
            </button>
          </div>

          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">描述</label>
            <textarea v-model="form.description" rows="3" class="w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-violet-500" placeholder="说明这个能力可以帮助手做什么" />
          </div>

          <div>
            <div class="mb-2 flex items-center justify-between">
              <label class="block text-xs font-medium text-slate-600">这个能力可以使用的工具</label>
              <span class="text-xs text-slate-400">选择后会写入能力配置</span>
            </div>
            <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
              <label
                v-for="tool in tools"
                :key="tool.name"
                class="flex items-start gap-2 rounded border border-slate-200 px-3 py-2"
              >
                <input v-model="form.tool_names" type="checkbox" :value="tool.name" class="mt-1 h-4 w-4" />
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-slate-800">{{ toolDisplayName(tool.name) }}</span>
                  <span class="line-clamp-2 block text-xs leading-relaxed text-slate-500">{{ tool.description }}</span>
                </span>
              </label>
            </div>
          </div>

          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">能力说明</label>
            <textarea
              v-model="form.system_prompt"
              rows="7"
              class="w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-violet-500"
              placeholder="写下这个能力的工作流程、约束、输出格式。"
            />
          </div>

          <div class="rounded border border-slate-200 p-3">
            <div class="mb-3 flex items-center justify-between">
              <div>
                <p class="text-xs font-medium text-slate-600">联网与文件权限</p>
                <p class="mt-1 text-xs text-slate-400">每行一个 resources 内的相对路径</p>
              </div>
              <label class="flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" v-model="form.permission_network" class="h-4 w-4" />
                需要网络
              </label>
            </div>
            <textarea
              v-model="form.permission_file_read"
              rows="4"
              class="w-full resize-none rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-violet-500"
              placeholder="例如：guide.md&#10;examples/sample.json"
            />
            <div v-if="form.resources.length" class="mt-3 space-y-1">
              <p class="text-xs font-medium text-slate-500">能力包自带文件</p>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="resource in form.resources"
                  :key="resource.path"
                  type="button"
                  @click="toggleResource(resource.path)"
                  :class="isResourceAllowed(resource.path) ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'"
                  class="rounded px-2 py-1 text-xs"
                  :title="resource.exists ? resource.path : '文件不存在'"
                >
                  {{ resource.path }}
                </button>
              </div>
            </div>
          </div>

          <label v-if="isAdmin" class="flex items-center justify-between rounded border border-slate-200 px-3 py-2">
            <span>
                  <span class="block text-sm font-medium text-slate-800">公开给其他用户使用</span>
              <span class="block text-xs text-slate-500">其他用户可以在公开列表中使用</span>
            </span>
            <input type="checkbox" v-model="isPublicBool" class="h-4 w-4" />
          </label>

          <div v-if="isAdmin && editing" class="rounded border border-slate-200 px-3 py-2">
            <div class="flex items-center justify-between gap-3">
              <span>
                <span class="block text-sm font-medium text-slate-800">发布状态</span>
                <span class="block text-xs text-slate-500">
                  只有「已发布」才会出现在能力商店里；作者自己任何状态都能绑到自己的助手上测试
                </span>
              </span>
              <select
                v-model="form.lifecycle_status"
                class="h-9 shrink-0 rounded border border-slate-300 px-2 text-sm outline-none focus:border-violet-500"
              >
                <option v-for="opt in skillApi.SKILL_LIFECYCLE_STATUSES" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </div>
            <p v-if="isPublicBool && form.lifecycle_status !== 'published'" class="mt-2 text-xs text-amber-600">
              提示：「公开给其他用户使用」已勾选，但状态不是「已发布」，其他用户暂时还看不到它。
            </p>
            <p v-if="editing?.lifecycle_status === 'published'" class="mt-2 text-xs text-slate-400">
              这个能力当前已发布。如果这次保存改了下面的工具/说明/权限内容，且没有手动
              把发布状态改成别的值，会自动退回「草稿」——已经绑定它的其他人的助手会
              停止使用改动前的版本，需要你确认无误后再重新选「已发布」。
            </p>
          </div>
        </main>

        <footer class="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-4">
          <p v-if="errorMsg" class="text-sm text-red-600">{{ errorMsg }}</p>
          <span v-else class="text-xs text-slate-400">{{ editing ? '保存后会同步更新能力配置文件。' : '保存后会生成一个用户专属能力配置文件。' }}</span>
          <div class="flex shrink-0 gap-2">
            <button @click="closeDialog" class="rounded border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">取消</button>
            <button
              @click="saveCurrentAsTemplate"
              :disabled="submitting || !form.name.trim() || form.tool_names.length === 0 || !form.system_prompt.trim()"
              class="rounded border border-slate-200 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
            >
              另存为样板
            </button>
            <button
              @click="submit"
              :disabled="submitting || !form.name.trim() || form.tool_names.length === 0 || (!editing && !form.template_filename && !form.system_prompt.trim())"
              class="rounded bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:bg-violet-300"
            >
              {{ submitting ? '保存中...' : '保存' }}
            </button>
          </div>
        </footer>
      </div>
    </div>

    <div v-if="showTemplateDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="closeTemplateDialog">
      <div class="w-full max-w-2xl rounded-lg bg-white shadow-xl">
        <header class="flex h-14 items-center justify-between border-b border-slate-200 px-5">
          <h2 class="text-base font-semibold text-slate-900">{{ templateEditing ? '编辑样板' : '新建样板' }}</h2>
          <button @click="closeTemplateDialog" class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100" title="关闭">
            <X :size="16" />
          </button>
        </header>

        <main class="max-h-[72vh] space-y-4 overflow-y-auto p-5">
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">样板名称</label>
            <input v-model="templateForm.name" class="h-10 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-violet-500" placeholder="例如：投研分析样板" />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">样板描述</label>
            <textarea v-model="templateForm.description" rows="3" class="w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-violet-500" placeholder="说明这个样板适合什么场景" />
          </div>
          <div>
            <div class="mb-2 flex items-center justify-between">
              <label class="block text-xs font-medium text-slate-600">样板工具</label>
              <span class="text-xs text-slate-400">创建能力时会默认带出</span>
            </div>
            <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
              <label v-for="tool in tools" :key="tool.name" class="flex items-start gap-2 rounded border border-slate-200 px-3 py-2">
                <input v-model="templateForm.tool_names" type="checkbox" :value="tool.name" class="mt-1 h-4 w-4" />
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-slate-800">{{ toolDisplayName(tool.name) }}</span>
                  <span class="line-clamp-2 block text-xs leading-relaxed text-slate-500">{{ tool.description }}</span>
                </span>
              </label>
            </div>
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">工作规则</label>
            <textarea v-model="templateForm.system_prompt" rows="8" class="w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-violet-500" placeholder="写下默认工作流程、约束和输出格式" />
          </div>
        </main>

        <footer class="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-4">
          <p v-if="templateErrorMsg" class="text-sm text-red-600">{{ templateErrorMsg }}</p>
          <span v-else class="text-xs text-slate-400">样板会保存到你的账号里，之后创建能力时可复用。</span>
          <div class="flex shrink-0 gap-2">
            <button @click="closeTemplateDialog" class="rounded border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">取消</button>
            <button
              @click="submitTemplate"
              :disabled="templateSubmitting || !templateForm.name.trim() || templateForm.tool_names.length === 0 || !templateForm.system_prompt.trim()"
              class="rounded bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:bg-violet-300"
            >
              {{ templateSubmitting ? '保存中...' : '保存样板' }}
            </button>
          </div>
        </footer>
      </div>
    </div>

    <!-- 历史版本 -->
    <div v-if="versionsFor" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-4" @click.self="closeVersions">
      <div class="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <header class="flex h-14 items-center justify-between border-b border-slate-200 px-5">
          <div class="min-w-0">
            <h2 class="truncate text-base font-semibold text-slate-900">历史版本 · {{ versionsFor.name }}</h2>
            <p class="text-xs text-slate-500">每次编辑前自动保存，最多保留最近 20 个</p>
          </div>
          <button @click="closeVersions" class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100" title="关闭">
            <X :size="16" />
          </button>
        </header>
        <main class="max-h-[60vh] overflow-y-auto p-5">
          <p v-if="versionsLoading" class="py-8 text-center text-sm text-slate-400">加载中…</p>
          <p v-else-if="versionsError" class="rounded bg-red-50 px-3 py-2 text-xs text-red-600">{{ versionsError }}</p>
          <p v-else-if="versions.length === 0" class="py-8 text-center text-sm text-slate-400">
            还没有历史版本。第一次编辑这个技能时，会先自动保存当前的样子。
          </p>
          <ul v-else class="space-y-2">
            <li v-for="ver in versions" :key="ver.id" class="flex items-start justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2.5">
              <div class="min-w-0">
                <p class="text-sm font-medium text-slate-900">
                  v{{ ver.version_no }}
                  <span class="ml-1.5 text-xs font-normal text-slate-400">{{ formatVersionTime(ver.created_at) }}</span>
                </p>
                <p class="mt-0.5 truncate text-xs text-slate-500">{{ ver.name }}<template v-if="ver.note"> · {{ ver.note }}</template></p>
              </div>
              <button
                type="button"
                :disabled="restoringId !== null"
                class="shrink-0 rounded border border-sky-200 px-2.5 py-1 text-xs text-sky-700 hover:bg-sky-50 disabled:text-slate-300"
                @click="handleRestore(ver)"
              >
                {{ restoringId === ver.id ? '恢复中…' : '恢复到此版本' }}
              </button>
            </li>
          </ul>
        </main>
        <footer class="border-t border-slate-200 px-5 py-3 text-xs leading-relaxed text-slate-500">
          所有用户绑定的是同一份技能配置：恢复会立刻对所有人生效。恢复前会先自动保存当前状态，恢复错了可以再恢复回来。
          是否公开不受影响。
        </footer>
      </div>
    </div>

    <div v-if="previewValidation" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-4" @click.self="previewValidation = null">
      <div class="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <header class="flex h-14 items-center justify-between border-b border-slate-200 px-5">
          <div class="min-w-0">
            <h2 class="truncate text-base font-semibold text-slate-900">{{ previewValidation.name }}</h2>
            <p class="truncate text-xs text-slate-500">{{ previewValidation.config_file }}</p>
          </div>
          <button @click="previewValidation = null" class="inline-flex h-8 w-8 items-center justify-center rounded text-slate-400 hover:bg-slate-100" title="关闭">
            <X :size="16" />
          </button>
        </header>
        <main class="space-y-4 p-5">
          <div :class="previewValidation.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'" class="rounded border px-3 py-2 text-sm">
            {{ previewValidation.ok ? '该能力当前可被助手正常加载' : '该能力当前不可用' }}
          </div>
          <div>
            <p class="mb-2 text-xs font-medium text-slate-500">可使用的工具</p>
            <div class="flex flex-wrap gap-1.5">
              <span v-for="toolName in previewValidation.tool_names" :key="toolName" class="rounded bg-blue-50 px-2 py-1 text-xs text-blue-700">{{ toolDisplayName(toolName) }}</span>
              <span v-if="previewValidation.tool_names.length === 0" class="text-xs text-slate-400">不需要额外工具</span>
            </div>
          </div>
          <div>
            <p class="mb-2 text-xs font-medium text-slate-500">工作规则</p>
            <span :class="previewValidation.system_prompt_ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'" class="rounded px-2 py-1 text-xs">
              {{ previewValidation.system_prompt_ready ? '已填写' : '为空' }}
            </span>
          </div>
          <div>
            <p class="mb-2 text-xs font-medium text-slate-500">联网与文件权限</p>
            <div class="flex flex-wrap gap-1.5">
              <span class="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
                网络 {{ previewValidation.permissions?.network ? '允许' : '关闭' }}
              </span>
              <span class="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
                文件资源 {{ previewValidation.allowed_resource_count }}/{{ previewValidation.resource_count }}
              </span>
            </div>
            <div v-if="previewValidation.resources?.length" class="mt-2 max-h-28 overflow-y-auto rounded border border-slate-200 p-2">
              <p v-for="resource in previewValidation.resources" :key="resource.path" class="text-xs text-slate-500">
                {{ resource.allowed ? '已允许' : '未允许' }} · {{ resource.path }} · {{ resource.exists ? '存在' : '缺失' }}
              </p>
            </div>
          </div>
          <div v-if="previewValidation.script_count">
            <p class="mb-2 text-xs font-medium text-slate-500">Python 脚本</p>
            <p class="text-xs leading-relaxed text-slate-600">{{ scriptSummary(previewValidation) }}</p>
            <p v-if="previewValidation.script_missing_packages?.length" class="mt-1.5 text-xs text-slate-500">
              沙箱里没有的依赖：{{ previewValidation.script_missing_packages.join('、') }}
            </p>
          </div>
          <div v-if="previewValidation.errors.length" class="space-y-2">
            <p class="text-xs font-medium text-red-600">错误</p>
            <p v-for="err in previewValidation.errors" :key="err" class="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{{ err }}</p>
          </div>
          <div v-if="previewValidation.warnings.length" class="space-y-2">
            <p class="text-xs font-medium text-amber-600">提醒</p>
            <p v-for="warn in previewValidation.warnings" :key="warn" class="rounded bg-amber-50 px-3 py-2 text-xs text-amber-700">{{ warn }}</p>
          </div>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { isImeEnter } from '../utils/ime'
import { toolDisplayName } from '../utils/displayNames'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock3, Download, History, Pencil, Plus, RefreshCw, Trash2, Upload, X, Zap } from 'lucide-vue-next'
import * as skillApi from '../api/skill'
import * as attachmentApi from '../api/attachment'
import { useUserStore } from '../stores/user'
import type { Skill, SkillImportResult, SkillLifecycleStatus, SkillTemplate, SkillTool, SkillValidation, SkillVersion } from '../api/skill'
import { toastError, toastSuccess } from '../utils/toast'
import { getErrorMessage } from '../utils/request'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const isAdmin = computed(() => Boolean(userStore.user?.is_admin))
// 管理员从后台「技能管理」进来：套在后台布局里，不显示「返回工作台」
const isAdminView = computed(() => route.path.startsWith('/admin'))
const mySkills = ref<Skill[]>([])
const publicSkills = ref<Skill[]>([])
const templates = ref<SkillTemplate[]>([])
const tools = ref<SkillTool[]>([])
const activeTab = ref<'mine' | 'public'>(isAdmin.value ? 'mine' : 'public')
const showDialog = ref(false)
const editing = ref<Skill | null>(null)
const submitting = ref(false)
const errorMsg = ref('')
const importing = ref(false)
const importError = ref('')
const importInput = ref<HTMLInputElement | null>(null)
const showImport = ref(false)
const importTab = ref<'file' | 'github'>('file')
const importTabs = [
  { key: 'file', label: '上传文件' },
  { key: 'github', label: 'GitHub 链接' },
] as const
const githubUrl = ref('')
const importFormError = ref('')
const importResult = ref<SkillImportResult | null>(null)
const installingId = ref<number | null>(null)
const showTemplateDialog = ref(false)
const templateEditing = ref<SkillTemplate | null>(null)
const templateSubmitting = ref(false)
const templateErrorMsg = ref('')
const validationMap = ref<Record<number, SkillValidation>>({})
const validating = ref(false)
const previewValidation = ref<SkillValidation | null>(null)

const form = ref({
  name: '',
  description: '',
  template_filename: '',
  is_public: 0,
  system_prompt: '',
  tool_names: [] as string[],
  permission_network: false,
  permission_file_read: '',
  resources: [] as skillApi.SkillResource[],
  lifecycle_status: 'draft' as SkillLifecycleStatus,
})

const lifecycleLabel = (status?: SkillLifecycleStatus) =>
  skillApi.SKILL_LIFECYCLE_STATUSES.find((opt) => opt.value === status)?.label || '草稿'

const lifecycleBadgeClass = (status?: SkillLifecycleStatus) => {
  if (status === 'published') return 'bg-emerald-50 text-emerald-700'
  if (status === 'reviewing') return 'bg-amber-50 text-amber-700'
  if (status === 'retired') return 'bg-slate-200 text-slate-500'
  return 'bg-slate-100 text-slate-500' // draft
}

const templateForm = ref({
  name: '',
  description: '',
  system_prompt: '',
  tool_names: [] as string[],
})

const shownSkills = computed(() => activeTab.value === 'mine' ? mySkills.value : publicSkills.value)
const mySkillIds = computed(() => new Set(mySkills.value.map((skill) => skill.id)))
const isMySkill = (skill: Skill) => mySkillIds.value.has(skill.id)
const isPublicBool = computed<boolean>({
  get: () => form.value.is_public === 1,
  set: (v) => { form.value.is_public = v ? 1 : 0 },
})

const loadValidations = async (skills: Skill[]) => {
  validating.value = true
  try {
    const entries = await Promise.all(skills.map(async (skill) => {
      try {
        return [skill.id, await skillApi.validateSkill(skill.id)] as const
      } catch (e: any) {
        return [skill.id, {
          ok: false,
          errors: [getErrorMessage(e, '无法检查该能力')],
          warnings: [],
          tool_names: [],
          missing_tool_names: [],
          system_prompt_ready: false,
          permissions: { network: false, file_read: [], exec: false },
          resources: [],
          resource_count: 0,
          allowed_resource_count: 0,
          skill_id: skill.id,
          name: skill.name,
          config_file: skill.config_file,
          is_public: skill.is_public,
        }] as const
      }
    }))
    validationMap.value = Object.fromEntries(entries)
  } finally {
    validating.value = false
  }
}

// 脚本状态标签：让用户在安装前就知道这个能力的脚本能不能用
const scriptBadge = (skillId: number) => {
  const v = validationMap.value[skillId]
  if (!v || !v.script_count) return null
  const missing = v.script_missing_packages?.length ? `缺少依赖：${v.script_missing_packages.join('、')}` : ''
  const reasons = [missing, v.script_network ? `${v.script_network} 个脚本需要联网` : ''].filter(Boolean).join('；')
  if (v.script_status === 'unsupported') {
    return { text: '脚本暂不支持', cls: 'bg-red-100 text-red-700', title: `沙箱里跑不了这个能力的脚本。${reasons}。安装后只按文字说明工作` }
  }
  if (!v.sandbox_enabled) {
    return { text: '脚本需管理员启用沙箱', cls: 'bg-amber-100 text-amber-800', title: '带 Python 脚本，但服务器还没开启脚本沙箱，暂时只按文字说明工作' }
  }
  if (v.script_status === 'ready') return { text: '脚本可运行', cls: 'bg-emerald-100 text-emerald-700', title: '脚本可以在隔离沙箱里运行' }
  if (v.script_status === 'partial') {
    return { text: `部分脚本可运行 ${v.script_runnable}/${v.script_count}`, cls: 'bg-amber-100 text-amber-800', title: `其余脚本跑不了。${reasons}` }
  }
  return { text: '脚本未检查', cls: 'bg-slate-100 text-slate-600', title: '这个能力是旧版本导入的，没有兼容性检查结果，重新导入可获得' }
}

const scriptSummary = (v: SkillValidation) => {
  const total = v.script_count || 0
  if (v.script_status === 'unsupported') return `${total} 个脚本都不能在沙箱里运行，只按文字说明工作。`
  const state = v.sandbox_enabled ? '沙箱已开启' : '沙箱未开启（管理员启用后生效）'
  if (v.script_status === 'partial') return `${total} 个脚本里 ${v.script_runnable} 个可以运行，其余跑不了。${state}。`
  if (v.script_status === 'ready') return `${total} 个脚本都可以运行。${state}。`
  return `${total} 个脚本，没有兼容性检查结果。${state}。`
}

const validationText = (skillId: number) => {
  const validation = validationMap.value[skillId]
  if (!validation || validating.value) return '检查中...'
  if (!validation.ok) return validation.errors[0] || '不可用'
  if (validation.warnings.length) return validation.warnings[0]
  return validation.tool_names.length ? '可正常加载' : '提示词能力'
}

const validationBoxClass = (skillId: number) => {
  const validation = validationMap.value[skillId]
  if (!validation || validating.value) return 'border-slate-200 bg-slate-50 text-slate-500'
  if (!validation.ok) return 'border-red-200 bg-red-50 text-red-700'
  if (validation.warnings.length) return 'border-amber-200 bg-amber-50 text-amber-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

const validationIcon = (skillId: number) => {
  const validation = validationMap.value[skillId]
  if (!validation || validating.value) return Clock3
  if (!validation.ok) return AlertTriangle
  if (validation.warnings.length) return AlertTriangle
  return CheckCircle2
}

const openValidationPreview = (skillId: number) => {
  previewValidation.value = validationMap.value[skillId] || null
}

watch(() => form.value.template_filename, (filename) => {
  if (editing.value || !filename) return
  const template = templates.value.find((item) => item.filename === filename)
  if (template) applyTemplate(template)
})

const applyTemplate = (template: SkillTemplate) => {
  form.value.template_filename = template.filename
  form.value.tool_names = [...(template.tool_names || [])]
  form.value.description = template.description || form.value.description
  form.value.system_prompt = template.system_prompt || form.value.system_prompt
}

const createFromTemplate = (template: SkillTemplate) => {
  editing.value = null
  form.value = {
    name: template.name,
    description: template.description || '',
    template_filename: template.filename,
    is_public: 0,
    system_prompt: template.system_prompt || '',
    tool_names: [...(template.tool_names || [])],
    permission_network: false,
    permission_file_read: '',
    resources: [],
    lifecycle_status: 'draft',
  }
  errorMsg.value = ''
  showDialog.value = true
}

const selectBlankTemplate = () => {
  form.value.template_filename = ''
  form.value.tool_names = []
  form.value.system_prompt = ''
}

const reload = async () => {
  if (!isAdmin.value) {
    activeTab.value = 'public'
    mySkills.value = []
    templates.value = []
    tools.value = []
    publicSkills.value = await skillApi.listPublicSkills()
    await loadValidations(publicSkills.value)
    return
  }
  const [mine, pub, tpls, availableTools] = await Promise.all([
    skillApi.listUserSkills(),
    skillApi.listPublicSkills(),
    skillApi.listTemplates(),
    skillApi.listTools(),
  ])
  mySkills.value = mine
  publicSkills.value = pub
  templates.value = tpls
  tools.value = availableTools
  await loadValidations([...new Map([...mine, ...pub].map((skill) => [skill.id, skill])).values()])
}

const openCreate = () => {
  editing.value = null
  form.value = {
    name: '',
    description: '',
    template_filename: '',
    is_public: 0,
    system_prompt: '',
    tool_names: [],
    permission_network: false,
    permission_file_read: '',
    resources: [],
    lifecycle_status: 'draft',
  }
  if (templates.value.length > 0) {
    const firstTemplate = templates.value[0]
    form.value.name = firstTemplate.name
    applyTemplate(firstTemplate)
  }
  errorMsg.value = ''
  showDialog.value = true
}

const openEdit = async (skill: Skill) => {
  editing.value = skill
  errorMsg.value = ''
  showDialog.value = true
  let detail = skill
  try {
    detail = await skillApi.getSkill(skill.id)
    // 用刚读到的最新数据（含 row_version）替换列表里可能已经过时的快照，
    // 乐观锁比对的是这一份，不是打开弹窗那一刻列表里的旧值。
    editing.value = detail
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '读取能力配置失败')
  }
  form.value = {
    name: detail.name,
    description: detail.description || '',
    template_filename: '',
    is_public: detail.is_public,
    system_prompt: detail.config?.system_prompt || '',
    tool_names: [...(detail.config?.tool_names || [])],
    permission_network: Boolean(detail.config?.permissions?.network),
    permission_file_read: (detail.config?.permissions?.file_read || []).join('\n'),
    resources: [...(detail.config?.resources || [])],
    lifecycle_status: detail.lifecycle_status || 'draft',
  }
}

const closeDialog = () => {
  showDialog.value = false
  editing.value = null
}

const openTemplateCreate = () => {
  templateEditing.value = null
  templateForm.value = { name: '', description: '', system_prompt: '', tool_names: [] }
  templateErrorMsg.value = ''
  showTemplateDialog.value = true
}

const openTemplateEdit = async (template: SkillTemplate) => {
  templateEditing.value = template
  templateErrorMsg.value = ''
  showTemplateDialog.value = true
  let detail = template
  try {
    detail = await skillApi.getTemplate(template.filename)
  } catch (e: any) {
    templateErrorMsg.value = getErrorMessage(e, '读取样板失败')
  }
  templateForm.value = {
    name: detail.name,
    description: detail.description || '',
    system_prompt: detail.system_prompt || '',
    tool_names: [...(detail.tool_names || [])],
  }
}

const closeTemplateDialog = () => {
  showTemplateDialog.value = false
  templateEditing.value = null
}

const submitTemplate = async () => {
  templateSubmitting.value = true
  templateErrorMsg.value = ''
  try {
    const payload = {
      name: templateForm.value.name.trim(),
      description: templateForm.value.description,
      system_prompt: templateForm.value.system_prompt,
      tool_names: templateForm.value.tool_names,
    }
    if (templateEditing.value) {
      await skillApi.updateTemplate(templateEditing.value.filename, payload)
    } else {
      await skillApi.createTemplate(payload)
    }
    await reload()
    closeTemplateDialog()
  } catch (e: any) {
    templateErrorMsg.value = getErrorMessage(e, '保存样板失败')
  } finally {
    templateSubmitting.value = false
  }
}

const handleTemplateDelete = async (template: SkillTemplate) => {
  if (!confirm(`确认删除样板「${template.name}」？`)) return
  try {
    await skillApi.deleteTemplate(template.filename)
    await reload()
  } catch (e: any) {
    importError.value = getErrorMessage(e, '删除样板失败')
  }
}

const saveCurrentAsTemplate = async () => {
  submitting.value = true
  errorMsg.value = ''
  try {
    await skillApi.createTemplate({
      name: form.value.name.trim(),
      description: form.value.description,
      system_prompt: form.value.system_prompt,
      tool_names: form.value.tool_names,
    })
    await reload()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '另存样板失败')
  } finally {
    submitting.value = false
  }
}

const submit = async () => {
  submitting.value = true
  errorMsg.value = ''
  try {
    if (editing.value) {
      await skillApi.updateSkill(editing.value.id, {
        name: form.value.name.trim(),
        description: form.value.description,
        is_public: form.value.is_public,
        system_prompt: form.value.system_prompt,
        tool_names: form.value.tool_names,
        permissions: buildPermissionsPayload(),
        lifecycle_status: form.value.lifecycle_status,
        expected_row_version: editing.value.row_version,
      })
    } else {
      await skillApi.createSkill({
        name: form.value.name.trim(),
        description: form.value.description,
        template_filename: form.value.template_filename,
        is_public: form.value.is_public,
        system_prompt: form.value.system_prompt,
        tool_names: form.value.tool_names,
        permissions: buildPermissionsPayload(),
      })
    }
    await reload()
    closeDialog()
  } catch (e: any) {
    const message = getErrorMessage(e, '保存失败')
    // 版本冲突：把这个能力最新的内容（含最新 row_version）重新读回来，
    // 让用户能直接看着最新版本再改一次，不用自己关掉弹窗重开。
    // openEdit 会先清空 errorMsg，所以冲突提示要在它跑完之后再设置。
    if (e?.response?.status === 409 && editing.value) {
      await openEdit(editing.value)
    }
    errorMsg.value = message
  } finally {
    submitting.value = false
  }
}

const permissionFileReadList = () => form.value.permission_file_read
  .split('\n')
  .map((item) => item.trim().replaceAll('\\', '/'))
  .filter(Boolean)

const buildPermissionsPayload = () => ({
  network: form.value.permission_network,
  file_read: permissionFileReadList(),
  exec: false,
})

const isResourceAllowed = (path: string) => permissionFileReadList().includes(path)

const toggleResource = (path: string) => {
  const current = permissionFileReadList()
  const next = current.includes(path) ? current.filter((item) => item !== path) : [...current, path]
  form.value.permission_file_read = next.join('\n')
}

const handleDelete = async (skill: Skill) => {
  if (!confirm(`确认删除能力「${skill.name}」？已添加该能力的助手会自动解绑。`)) return
  await skillApi.deleteSkill(skill.id)
  await reload()
}

const handleExport = async (skill: Skill) => {
  const blob = await skillApi.exportSkill(skill.id)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${skill.name}_${skill.id}.zip`
  link.click()
  URL.revokeObjectURL(url)
  toastSuccess('能力已导出')
}

const handleInstall = async (skill: Skill) => {
  installingId.value = skill.id
  importError.value = ''
  try {
    await skillApi.installPublicSkill(skill.id)
    toastSuccess('已安装到我的能力库')
    activeTab.value = 'mine'
    await reload()
  } catch (e: any) {
    importError.value = getErrorMessage(e, '安装失败')
  } finally {
    installingId.value = null
  }
}

// ---- 历史版本 ----
const versionsFor = ref<Skill | null>(null)
const versions = ref<SkillVersion[]>([])
const versionsLoading = ref(false)
const versionsError = ref('')
const restoringId = ref<number | null>(null)

const formatVersionTime = (iso: string | null) => {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false })
}

const openVersions = async (skill: Skill) => {
  versionsFor.value = skill
  versions.value = []
  versionsError.value = ''
  versionsLoading.value = true
  try {
    versions.value = await skillApi.listSkillVersions(skill.id)
  } catch (err: any) {
    versionsError.value = getErrorMessage(err, '加载历史版本失败')
  } finally {
    versionsLoading.value = false
  }
}

const closeVersions = () => {
  if (restoringId.value === null) versionsFor.value = null
}

const handleRestore = async (ver: SkillVersion) => {
  const skill = versionsFor.value
  if (!skill || restoringId.value !== null) return
  if (!confirm(`确认把「${skill.name}」恢复到 v${ver.version_no}？会立刻对所有绑定它的用户生效（恢复前会自动保存当前状态）。`)) return
  restoringId.value = ver.id
  try {
    await skillApi.restoreSkillVersion(skill.id, ver.id)
    toastSuccess(`已恢复到 v${ver.version_no}`)
    await reload()
    versionsFor.value = null
  } catch (err: any) {
    versionsError.value = getErrorMessage(err, '恢复失败')
  } finally {
    restoringId.value = null
  }
}

const reanalyzing = ref(false)
const handleReanalyze = async () => {
  if (reanalyzing.value) return
  reanalyzing.value = true
  try {
    const r = await skillApi.reanalyzeSkillScripts()
    await reload()
    toastSuccess(`已检查 ${r.checked} 个带脚本的能力，${r.changed} 个状态有变化`)
  } catch (err: any) {
    toastError(getErrorMessage(err, '检查失败，请稍后重试'))
  } finally {
    reanalyzing.value = false
  }
}

const translatingId = ref<number | null>(null)
const CJK = /[一-鿿]/
// 名称和说明里一个汉字都没有 = 用户大概率看不懂
const needsTranslation = (s: { name: string; description?: string }) =>
  !CJK.test(`${s.name} ${s.description || ''}`)

const handleTranslate = async (s: { id: number; name: string; description?: string }) => {
  if (translatingId.value) return
  translatingId.value = s.id
  try {
    const updated = await skillApi.translateSkill(s.id)
    // 同步到导入结果弹窗里正在显示的那一项
    const shown = importResult.value?.imported.find((x) => x.id === s.id)
    if (shown) {
      shown.name = updated.name
      shown.description = updated.description
    }
    await reload()
    toastSuccess('已翻译成中文')
  } catch (err: any) {
    toastError(getErrorMessage(err, '翻译失败，请稍后重试或手动编辑'))
  } finally {
    translatingId.value = null
  }
}

const sandboxEnabled = ref(false)
const importPublic = ref(false)
const openImport = () => {
  importResult.value = null
  importFormError.value = ''
  showImport.value = true
  attachmentApi.getAttachmentStatus().then((s) => { sandboxEnabled.value = s.enabled }).catch(() => { sandboxEnabled.value = false })
}

const closeImport = () => {
  if (importing.value) return
  showImport.value = false
}

const runImport = async (task: () => Promise<SkillImportResult>) => {
  if (importing.value) return
  importing.value = true
  importFormError.value = ''
  try {
    importResult.value = await task()
    activeTab.value = 'mine'
    await reload()
  } catch (err: any) {
    importFormError.value = getErrorMessage(err, '导入失败，请稍后重试')
  } finally {
    importing.value = false
  }
}

const handleImportSelect = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (file) await runImport(() => skillApi.importSkill(file, importPublic.value ? 1 : 0))
}

const handleImportDrop = async (e: DragEvent) => {
  const file = e.dataTransfer?.files?.[0]
  if (file) await runImport(() => skillApi.importSkill(file, importPublic.value ? 1 : 0))
}

const handleGithubImport = async () => {
  const url = githubUrl.value.trim()
  if (url) await runImport(() => skillApi.importSkillFromGithub(url, importPublic.value ? 1 : 0))
}

onMounted(reload)
</script>
