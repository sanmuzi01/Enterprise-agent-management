<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-5">
      <p class="text-xs text-slate-500">管理企业部门、成员分配、部门负责人、企业角色和中央/部门 Agent。</p>
      <button
        @click="reloadAll"
        :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <div class="mb-5 inline-flex gap-1 rounded bg-slate-100 p-1 text-sm">
      <button
        v-for="t in tabs"
        :key="t.value"
        @click="activeTab = t.value"
        :class="activeTab === t.value ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
        class="h-8 rounded px-4 transition"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- ============ 部门管理 ============ -->
    <div v-if="activeTab === 'teams'" class="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr]">
      <section class="rounded-lg border border-slate-200 bg-white">
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-sm font-semibold text-slate-900">部门列表</h2>
          <button
            @click="openCreateTeam"
            class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
          >
            <Plus :size="13" />
            新建部门
          </button>
        </div>
        <div v-if="loading" class="py-10 text-center text-sm text-slate-400">加载中…</div>
        <ul v-else class="divide-y divide-slate-100">
          <li
            v-for="t in teams"
            :key="t.id"
            @click="selectTeam(t)"
            :class="selectedTeam?.id === t.id ? 'bg-indigo-50/70' : 'hover:bg-slate-50'"
            class="cursor-pointer px-4 py-3"
          >
            <div class="flex items-center justify-between">
              <p class="font-medium text-slate-900">{{ t.name }}</p>
              <span
                class="rounded px-2 py-0.5 text-xs"
                :class="t.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'"
              >{{ t.status === 'active' ? '启用中' : '已停用' }}</span>
            </div>
            <p class="mt-1 text-xs text-slate-400">
              {{ t.member_count }} 名成员
              <span v-if="t.leads.length"> · 负责人 {{ t.leads.map(l => l.name).join('、') }}</span>
              <span v-else class="text-amber-600"> · 未设负责人</span>
              <span v-if="t.department_code"> · {{ DEPARTMENT_CODE_OPTIONS.find(o => o.value === t.department_code)?.label }}</span>
            </p>
          </li>
          <li v-if="!teams.length" class="px-4 py-8 text-center text-sm text-slate-400">还没有创建任何部门</li>
        </ul>
      </section>

      <section v-if="selectedTeam" class="space-y-4">
        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold text-slate-900">{{ selectedTeam.name }}</h3>
            <div class="flex gap-1.5">
              <button @click="openRenameTeam(selectedTeam)" class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50">
                重命名
              </button>
              <button
                @click="toggleTeamStatus(selectedTeam)"
                class="rounded border px-2.5 py-1 text-xs"
                :class="selectedTeam.status === 'active' ? 'border-red-200 text-red-700 hover:bg-red-50' : 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'"
              >
                {{ selectedTeam.status === 'active' ? '停用部门' : '启用部门' }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="agentStatus" class="rounded-lg border border-slate-200 bg-white p-4" data-testid="dept-agent-status">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 class="text-sm font-semibold text-slate-900">部门专业 Agent</h4>
              <p class="mt-0.5 text-xs text-slate-500">按业务类型自动配置：{{ agentStatus.template_name }}</p>
            </div>
            <span class="rounded px-2 py-0.5 text-xs" :class="agentStateClass(agentStatus.state)">{{ agentStatus.state_label }}</span>
          </div>
          <p v-if="agentStatus.agent" class="mt-2 text-sm text-slate-700">
            {{ agentStatus.agent.name }}<span class="ml-1 text-xs text-slate-400">#{{ agentStatus.agent.id }} · {{ modelDisplayName(agentStatus.agent.model_name) }}</span>
          </p>
          <ul v-if="agentStatus.issues.length" class="mt-2 list-inside list-disc text-xs text-amber-700">
            <li v-for="(issue, i) in agentStatus.issues" :key="i">{{ issue }}</li>
          </ul>
          <div v-if="agentStatus.state !== 'team_disabled'" class="mt-3 flex gap-2">
            <button v-if="agentStatus.state === 'needs_repair'" @click="onRepairAgent" :disabled="acting"
              class="rounded bg-amber-600 px-2.5 py-1 text-xs text-white hover:bg-amber-700 disabled:opacity-50">一键修复</button>
            <button v-if="agentStatus.state === 'pending_publish'" @click="onPublishAgent" :disabled="acting"
              class="rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50">发布给部门员工</button>
          </div>
        </div>

        <div class="rounded-lg border border-slate-200 bg-white">
          <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h4 class="text-sm font-semibold text-slate-900">部门成员</h4>
            <button
              @click="openAddMember"
              class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
            >
              <UserPlus :size="13" />
              分配成员
            </button>
          </div>
          <table class="w-full text-left text-sm">
            <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
              <tr>
                <th class="px-4 py-2.5 font-medium">姓名</th>
                <th class="px-4 py-2.5 font-medium">部门角色</th>
                <th class="px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in teamMembers" :key="m.user_id" class="border-b border-slate-100">
                <td class="px-4 py-2.5 text-slate-900">{{ m.name }}<span class="ml-1 text-xs text-slate-400">#{{ m.user_id }}</span></td>
                <td class="px-4 py-2.5">
                  <select
                    :value="m.role_code"
                    @change="onChangeMemberRole(m, ($event.target as HTMLSelectElement).value)"
                    class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-indigo-500"
                  >
                    <option v-for="r in roleCatalog.team" :key="r.code" :value="r.code">{{ r.name }}</option>
                  </select>
                </td>
                <td class="px-4 py-2.5">
                  <button @click="onRemoveMember(m)" class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">
                    移出部门
                  </button>
                </td>
              </tr>
              <tr v-if="!teamMembers.length"><td colspan="3" class="px-4 py-8 text-center text-slate-400">这个部门还没有成员</td></tr>
            </tbody>
          </table>
        </div>

        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <h4 class="mb-3 text-sm font-semibold text-slate-900">权限关系</h4>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p class="mb-1.5 text-xs font-medium text-slate-500">绑定的知识库空间</p>
              <ul v-if="permissions?.knowledge_spaces.length" class="space-y-1">
                <li v-for="s in permissions.knowledge_spaces" :key="s.id" class="text-sm text-slate-700">{{ s.name }}</li>
              </ul>
              <p v-else class="text-sm text-slate-400">暂无</p>
            </div>
            <div>
              <p class="mb-1.5 text-xs font-medium text-slate-500">绑定的部门 Agent</p>
              <ul v-if="permissions?.agents.length" class="space-y-1">
                <li v-for="a in permissions.agents" :key="a.id" class="text-sm text-slate-700">
                  {{ a.name }}<span class="text-xs text-slate-400">（{{ departmentCodeLabel(a.department_code) }}）</span>
                </li>
              </ul>
              <p v-else class="text-sm text-slate-400">暂无</p>
            </div>
          </div>
        </div>
      </section>
      <section v-else class="flex items-center justify-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-400">
        选择左侧一个部门查看详情
      </section>
    </div>

    <!-- ============ 企业成员 ============ -->
    <div v-else-if="activeTab === 'members'" class="rounded-lg border border-slate-200 bg-white">
      <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 class="text-sm font-semibold text-slate-900">企业成员</h2>
        <button
          @click="openAddOrgMember"
          class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700"
        >
          <UserPlus :size="13" />
          添加企业成员
        </button>
      </div>
      <table class="w-full text-left text-sm">
        <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="px-4 py-2.5 font-medium">姓名</th>
            <th class="px-4 py-2.5 font-medium">所属部门</th>
            <th class="px-4 py-2.5 font-medium">企业角色</th>
            <th class="px-4 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in orgMembers" :key="m.user_id" class="border-b border-slate-100">
            <td class="px-4 py-2.5 text-slate-900">{{ m.name }}<span class="ml-1 text-xs text-slate-400">#{{ m.user_id }}</span></td>
            <td class="px-4 py-2.5 text-xs text-slate-500">
              <span v-if="m.departments.length">{{ m.departments.map(d => d.name).join('、') }}</span>
              <span v-else class="text-slate-300">—</span>
            </td>
            <td class="px-4 py-2.5">
              <select
                :value="m.role_code"
                @change="onChangeOrgMemberRole(m, ($event.target as HTMLSelectElement).value)"
                class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-indigo-500"
              >
                <option v-for="r in roleCatalog.organization" :key="r.code" :value="r.code">{{ r.name }}</option>
              </select>
            </td>
            <td class="px-4 py-2.5">
              <button
                @click="onToggleOrgMemberStatus(m)"
                class="rounded px-2 py-0.5 text-xs"
                :class="m.status === 'active' ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'"
              >
                {{ m.status === 'active' ? '启用中' : '已停用' }}
              </button>
            </td>
            <td class="px-4 py-2.5">
              <button @click="onRemoveOrgMember(m)" class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">
                移出企业
              </button>
            </td>
          </tr>
          <tr v-if="!orgMembers.length"><td colspan="5" class="px-4 py-8 text-center text-slate-400">还没有企业成员</td></tr>
        </tbody>
      </table>
    </div>

    <!-- ============ Agent 管理 ============ -->
    <div v-else class="rounded-lg border border-slate-200 bg-white">
      <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 class="text-sm font-semibold text-slate-900">企业智能体</h2>
          <p class="mt-0.5 text-xs text-slate-500">智能体由工程师开发好再接进来；也可以启用平台内置的业务智能体。创建后在列表里划分给部门或全企业，检查通过再发布。</p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <button @click="openIntegrate" data-testid="agent-integrate"
            class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700">
            <Plus :size="13" />接入智能体服务
          </button>
          <button @click="openCreateAgent" data-testid="agent-enable-builtin"
            class="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50">
            启用内置智能体
          </button>
        </div>
      </div>
      <table class="w-full text-left text-sm">
        <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="px-4 py-2.5 font-medium">名称</th>
            <th class="px-4 py-2.5 font-medium">类型</th>
            <th class="px-4 py-2.5 font-medium">划分给</th>
            <th class="px-4 py-2.5 font-medium">发布状态</th>
            <th class="px-4 py-2.5 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in managedAgents" :key="a.id" class="border-b border-slate-100">
            <td class="max-w-xs px-4 py-2.5 text-slate-900">
              <span class="font-medium">{{ a.name }}</span>
              <span v-if="a.runtime_type === 'external'" class="ml-1.5 rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">外部服务</span>
              <span v-else class="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">内置</span>
              <p v-if="a.description" class="mt-0.5 truncate text-xs text-slate-500" :title="a.description">{{ a.description }}</p>
              <p v-if="a.maintainer" class="truncate text-[11px] text-slate-400">维护：{{ a.maintainer }}</p>
            </td>
            <td class="px-4 py-2.5">
              <span class="rounded px-2 py-0.5 text-xs" :class="a.agent_type === 'central' ? 'bg-purple-50 text-purple-700' : 'bg-sky-50 text-sky-700'">
                {{ a.agent_type === 'central' ? '中央' : '部门' }}
              </span>
            </td>
            <td class="px-4 py-2.5 text-slate-600">
              <span v-if="a.assignment === 'enterprise'" class="rounded bg-purple-50 px-2 py-0.5 text-xs text-purple-700">全企业</span>
              <span v-else-if="a.assignment === 'department'">{{ a.team_name || '—' }}（{{ a.department_code ? departmentCodeLabel(a.department_code) : '通用办公' }}）</span>
              <span v-else class="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700" data-testid="agent-unassigned">未划分</span>
              <p v-if="a.knowledge_gaps?.length" class="mt-1 max-w-xs text-[11px] leading-snug text-amber-700" data-testid="agent-gaps"
                :title="a.knowledge_gaps.map(g => `${g.name}：${g.reason}`).join('\n')">
                有 {{ a.knowledge_gaps.length }} 份绑定的资料，使用者读不到：{{ a.knowledge_gaps.map(g => g.name).join('、') }}。到「企业知识库」里把它们划分给对应部门。
              </p>
            </td>
            <td class="px-4 py-2.5">
              <span class="rounded px-2 py-0.5 text-xs" :class="lifecycleBadgeClass(a.lifecycle_status)">
                {{ lifecycleLabel(a.lifecycle_status) }}
              </span>
            </td>
            <td class="px-4 py-2.5">
              <div class="flex flex-wrap gap-1.5">
                <button @click="openEditAgent(a)" class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50">
                  编辑
                </button>
                <button
                  @click="openAssign(a)"
                  :disabled="a.lifecycle_status === 'published'"
                  :title="a.lifecycle_status === 'published' ? '已发布的智能体正在被使用，请先停用，再调整划分' : '划分给部门或全企业'"
                  data-testid="agent-assign"
                  class="rounded border border-indigo-200 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  划分
                </button>
                <button
                  v-if="a.lifecycle_status !== 'published'"
                  @click="onSetAgentLifecycle(a, 'published')"
                  :disabled="a.assignment === 'unassigned'"
                  :title="a.assignment === 'unassigned' ? '还没有划分，先划分再发布' : ''"
                  class="rounded border border-emerald-200 px-2.5 py-1 text-xs text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  发布
                </button>
                <button
                  v-if="a.lifecycle_status === 'published'"
                  @click="onSetAgentLifecycle(a, 'retired')"
                  class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50"
                >
                  停用
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!managedAgents.length"><td colspan="5" class="px-4 py-8 text-center text-slate-400">还没有企业智能体：点右上角“接入智能体服务”，或启用一个内置智能体</td></tr>
        </tbody>
      </table>
    </div>

    <!-- ============ 智能体编辑器：新建 / 编辑（设定、模型、知识库、技能、发布前检查、试运行） ============ -->
    <ManagedAgentEditor v-if="editor.visible" :key="`${editor.agentId ?? 'new'}-${editor.mode}`" :agent-id="editor.agentId" :mode="editor.mode"
      @close="closeEditor" @saved="loadManagedAgents" />

    <!-- ============ 弹窗：划分智能体 ============ -->
    <div v-if="assignDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="assignDialog.visible = false">
      <div class="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl bg-white p-5 shadow-xl" data-testid="agent-assign-dialog">
        <h3 class="mb-1 text-base font-semibold text-slate-800">划分：{{ assignDialog.agent?.name }}</h3>
        <p class="mb-4 text-xs text-slate-500">智能体统一创建，再由管理员决定它服务哪些人。划分后，对应范围内的成员就能使用它（发布之后）。</p>
        <div class="space-y-3 text-sm">
          <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
            <input v-model="assignDialog.target" type="radio" value="department" class="mt-0.5" />
            <span class="min-w-0 flex-1">
              <span class="block font-medium text-slate-800">划分给一个部门</span>
              <span class="block text-xs text-slate-500">该部门的成员使用，并成为这个部门的主助手（每个部门同时只能发布一个）。</span>
              <template v-if="assignDialog.target === 'department'">
                <select v-model.number="assignDialog.teamId" data-testid="agent-assign-team"
                  class="mt-2 h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                  <option :value="null" disabled>选择部门</option>
                  <option v-for="t in agentOptions?.teams || []" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select>
                <select v-model="assignDialog.departmentCode"
                  class="mt-2 h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                  <option value="">业务方向：沿用智能体和部门的设置</option>
                  <option value="hr">业务方向：人事</option>
                  <option value="procurement">业务方向：采购</option>
                  <option value="sales">业务方向：销售</option>
                  <option value="finance">业务方向：财务</option>
                  <option value="it">业务方向：IT</option>
                </select>
              </template>
            </span>
          </label>
          <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
            <input v-model="assignDialog.target" type="radio" value="enterprise" class="mt-0.5" />
            <span>
              <span class="block font-medium text-slate-800">全企业可用</span>
              <span class="block text-xs text-slate-500">成为中央智能体，所有在职成员都能使用。</span>
            </span>
          </label>
          <label class="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2.5 has-[:checked]:border-indigo-400 has-[:checked]:bg-indigo-50/40">
            <input v-model="assignDialog.target" type="radio" value="unassigned" class="mt-0.5" />
            <span>
              <span class="block font-medium text-slate-800">暂不划分</span>
              <span class="block text-xs text-slate-500">只有创建者能试用，不能发布。</span>
            </span>
          </label>
          <p v-if="assignDialog.error" class="text-xs text-red-600">{{ assignDialog.error }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="assignDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button @click="submitAssign" :disabled="assignDialog.saving || (assignDialog.target === 'department' && !assignDialog.teamId)" data-testid="agent-assign-save"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">
            {{ assignDialog.saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：新建/重命名部门 ============ -->
    <div v-if="teamDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="teamDialog.visible = false">
      <div class="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ teamDialog.editing ? '重命名部门' : '新建部门' }}</h3>
        <input
          v-model="teamDialog.name"
          type="text"
          placeholder="部门名称"
          class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500"
          @keyup.enter="submitTeamDialog"
        />
        <label class="mt-3 block text-xs font-medium text-slate-600">业务类型</label>
        <p class="mb-1 text-[11px] text-slate-400">决定部门工作台的业务模块；保存后会按业务类型自动配置部门专业 Agent（草稿，发布后员工可用）</p>
        <select
          v-model="teamDialog.departmentCode"
          class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500"
        >
          <option v-for="o in DEPARTMENT_CODE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="teamDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button
            @click="submitTeamDialog"
            :disabled="acting || !teamDialog.name.trim()"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 弹窗：分配部门成员 / 添加企业成员（共用搜索用户逻辑） ============ -->
    <div v-if="memberDialog.visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="memberDialog.visible = false">
      <div class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 class="mb-4 text-base font-semibold text-slate-800">{{ memberDialog.mode === 'team' ? '分配部门成员' : '添加企业成员' }}</h3>
        <div class="space-y-3">
          <div class="relative">
            <Search class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" :size="14" />
            <input
              v-model="userQuery"
              @input="searchUsers"
              type="text"
              placeholder="搜索用户名"
              class="h-9 w-full rounded border border-slate-300 pl-8 pr-3 text-sm outline-none focus:border-indigo-500"
            />
          </div>
          <ul v-if="userResults.length" class="max-h-40 overflow-y-auto rounded border border-slate-100">
            <li
              v-for="u in userResults"
              :key="u.id"
              @click="memberDialog.userId = u.id"
              :class="memberDialog.userId === u.id ? 'bg-indigo-50' : 'hover:bg-slate-50'"
              class="cursor-pointer px-3 py-2 text-sm text-slate-700"
            >
              {{ u.name }} <span class="text-xs text-slate-400">#{{ u.id }}</span>
            </li>
          </ul>
          <div v-if="memberDialog.userId">
            <label class="mb-1 block text-xs text-slate-500">
              {{ memberDialog.mode === 'team' ? '部门角色' : '企业角色' }}
            </label>
            <select v-model="memberDialog.roleCode" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
              <option
                v-for="r in (memberDialog.mode === 'team' ? roleCatalog.team : roleCatalog.organization)"
                :key="r.code" :value="r.code"
              >{{ r.name }}</option>
            </select>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button @click="memberDialog.visible = false" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">取消</button>
          <button
            @click="submitMemberDialog"
            :disabled="acting || !memberDialog.userId"
            class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            确认添加
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { modelDisplayName, departmentCodeLabel } from '../../utils/displayNames'
import { onMounted, ref } from 'vue'
import { Plus, RefreshCcw, Search, UserPlus } from 'lucide-vue-next'
import * as orgApi from '../../api/organizationAdmin'
import ManagedAgentEditor from '../../components/admin/ManagedAgentEditor.vue'
import type {
  OrgTeam, OrgTeamMember, OrgMember, EnterpriseRoleCatalog, TeamPermissions,
  ManagedAgent, LifecycleStatus,
} from '../../api/organizationAdmin'
import { listAdminUsers, type AdminUser } from '../../api/admin'
import { getErrorMessage } from '../../utils/request'

const tabs: { value: 'teams' | 'members' | 'agents'; label: string }[] = [
  { value: 'teams', label: '部门管理' },
  { value: 'members', label: '企业成员' },
  { value: 'agents', label: '企业智能体' },
]
const activeTab = ref<'teams' | 'members' | 'agents'>('teams')

const loading = ref(true)
const acting = ref(false)
const errorMsg = ref('')

const teams = ref<OrgTeam[]>([])
const orgMembers = ref<OrgMember[]>([])
const roleCatalog = ref<EnterpriseRoleCatalog>({ organization: [], team: [] })
const managedAgents = ref<ManagedAgent[]>([])

const selectedTeam = ref<OrgTeam | null>(null)
const teamMembers = ref<OrgTeamMember[]>([])
const permissions = ref<TeamPermissions | null>(null)

const loadTeams = async () => {
  teams.value = await orgApi.listOrgTeams()
}

const loadOrgMembers = async () => {
  orgMembers.value = await orgApi.listOrgMembers()
}

const loadManagedAgents = async () => {
  managedAgents.value = await orgApi.listManagedAgents()
}

const reloadAll = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    roleCatalog.value = await orgApi.getEnterpriseRoles()
    await Promise.all([loadTeams(), loadOrgMembers(), loadManagedAgents(), loadAgentOptions()])
    if (selectedTeam.value) {
      const stillThere = teams.value.find(t => t.id === selectedTeam.value!.id)
      if (stillThere) await selectTeam(stillThere)
      else selectedTeam.value = null
    }
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载组织架构失败')
  } finally {
    loading.value = false
  }
}

const selectTeam = async (t: OrgTeam) => {
  selectedTeam.value = t
  try {
    const [members, perms, status] = await Promise.all([
      orgApi.listTeamMembers(t.id),
      orgApi.getTeamPermissions(t.id),
      orgApi.getTeamAgentStatus(t.id),
    ])
    teamMembers.value = members
    permissions.value = perms
    agentStatus.value = status
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载部门详情失败')
  }
}

// ---- 部门专业 Agent ----

const agentStatus = ref<orgApi.DepartmentAgentStatus | null>(null)

const agentStateClass = (state: orgApi.DepartmentAgentState) => ({
  ready: 'bg-emerald-50 text-emerald-700',
  pending_publish: 'bg-sky-50 text-sky-700',
  needs_repair: 'bg-amber-50 text-amber-700',
  team_disabled: 'bg-slate-100 text-slate-500',
}[state])

const runAgentAction = async (action: (teamId: number) => Promise<orgApi.DepartmentAgentStatus>, fallback: string) => {
  if (!selectedTeam.value) return
  acting.value = true
  try {
    agentStatus.value = await action(selectedTeam.value.id)
    await Promise.all([loadManagedAgents(), selectTeam(selectedTeam.value)])
  } catch (e: any) {
    alert(getErrorMessage(e, fallback))
  } finally {
    acting.value = false
  }
}

const onRepairAgent = () => runAgentAction(orgApi.repairTeamAgent, '修复失败')
const onPublishAgent = () => runAgentAction(orgApi.publishTeamAgent, '发布失败')

// ---- 部门 ----

const DEPARTMENT_CODE_OPTIONS = [
  { value: '', label: '未分配业务类型' },
  { value: 'hr', label: '人事' },
  { value: 'procurement', label: '采购' },
  { value: 'sales', label: '销售' },
  { value: 'finance', label: '财务' },
  { value: 'it', label: 'IT' },
]

const teamDialog = ref<{ visible: boolean; editing: OrgTeam | null; name: string; departmentCode: string }>({
  visible: false, editing: null, name: '', departmentCode: '',
})

const openCreateTeam = () => {
  teamDialog.value = { visible: true, editing: null, name: '', departmentCode: '' }
}

const openRenameTeam = (t: OrgTeam) => {
  teamDialog.value = { visible: true, editing: t, name: t.name, departmentCode: t.department_code || '' }
}

const submitTeamDialog = async () => {
  acting.value = true
  try {
    const departmentCode = teamDialog.value.departmentCode || null
    let teamId: number
    if (teamDialog.value.editing) {
      teamId = teamDialog.value.editing.id
      await orgApi.updateOrgTeam(teamId, {
        name: teamDialog.value.name.trim(), department_code: departmentCode,
      })
    } else {
      teamId = (await orgApi.createOrgTeam(teamDialog.value.name.trim(), departmentCode)).id
    }
    teamDialog.value.visible = false
    await Promise.all([loadTeams(), loadManagedAgents()])
    const saved = teams.value.find(t => t.id === teamId)
    if (saved) await selectTeam(saved)
  } catch (e: any) {
    alert(getErrorMessage(e, '保存失败'))
  } finally {
    acting.value = false
  }
}

const toggleTeamStatus = async (t: OrgTeam) => {
  const next = t.status === 'active' ? 'disabled' : 'active'
  if (next === 'disabled' && !confirm(`确认停用部门「${t.name}」？停用后该部门成员的部门相关操作会被拒绝，部门已发布的 Agent 会同步退役。`)) return
  try {
    await orgApi.updateOrgTeam(t.id, { status: next })
    await Promise.all([loadTeams(), loadManagedAgents()])
    const saved = teams.value.find(x => x.id === t.id)
    if (saved && selectedTeam.value?.id === t.id) await selectTeam(saved)
  } catch (e: any) {
    alert(getErrorMessage(e, '操作失败'))
  }
}

const onChangeMemberRole = async (m: OrgTeamMember, roleCode: string) => {
  try {
    await orgApi.updateTeamMemberRole(selectedTeam.value!.id, m.user_id, roleCode)
    await selectTeam(selectedTeam.value!)
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '修改角色失败'))
  }
}

const onRemoveMember = async (m: OrgTeamMember) => {
  if (!confirm(`确认将「${m.name}」移出部门？`)) return
  try {
    await orgApi.removeTeamMember(selectedTeam.value!.id, m.user_id)
    await selectTeam(selectedTeam.value!)
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '移除失败'))
  }
}

// ---- 企业成员 ----

const onChangeOrgMemberRole = async (m: OrgMember, roleCode: string) => {
  try {
    await orgApi.updateOrgMember(m.user_id, { role_code: roleCode })
    await loadOrgMembers()
  } catch (e: any) {
    alert(getErrorMessage(e, '修改角色失败'))
  }
}

const onToggleOrgMemberStatus = async (m: OrgMember) => {
  const next = m.status === 'active' ? 'disabled' : 'active'
  try {
    await orgApi.updateOrgMember(m.user_id, { status: next })
    await loadOrgMembers()
  } catch (e: any) {
    alert(getErrorMessage(e, '操作失败'))
  }
}

const onRemoveOrgMember = async (m: OrgMember) => {
  if (!confirm(`确认将「${m.name}」移出企业？该操作会连带清除他在所有部门里的身份。`)) return
  try {
    await orgApi.removeOrgMember(m.user_id)
    await loadOrgMembers()
    await loadTeams()
  } catch (e: any) {
    alert(getErrorMessage(e, '移除失败'))
  }
}

// ---- 添加成员弹窗（部门/企业共用） ----

const memberDialog = ref<{ visible: boolean; mode: 'team' | 'org'; userId: number | null; roleCode: string }>({
  visible: false, mode: 'team', userId: null, roleCode: 'member',
})
const userQuery = ref('')
const userResults = ref<AdminUser[]>([])

const searchUsers = async () => {
  if (!userQuery.value.trim()) {
    userResults.value = []
    return
  }
  try {
    const page = await listAdminUsers({ search: userQuery.value.trim(), limit: 10 })
    userResults.value = page.items
  } catch {
    userResults.value = []
  }
}

const openAddMember = () => {
  memberDialog.value = { visible: true, mode: 'team', userId: null, roleCode: 'member' }
  userQuery.value = ''
  userResults.value = []
}

const openAddOrgMember = () => {
  memberDialog.value = { visible: true, mode: 'org', userId: null, roleCode: 'member' }
  userQuery.value = ''
  userResults.value = []
}

const submitMemberDialog = async () => {
  if (!memberDialog.value.userId) return
  acting.value = true
  try {
    if (memberDialog.value.mode === 'team') {
      await orgApi.addTeamMember(selectedTeam.value!.id, memberDialog.value.userId, memberDialog.value.roleCode)
      await selectTeam(selectedTeam.value!)
      await loadTeams()
    } else {
      await orgApi.addOrgMember(memberDialog.value.userId, memberDialog.value.roleCode)
      await loadOrgMembers()
    }
    memberDialog.value.visible = false
  } catch (e: any) {
    alert(getErrorMessage(e, '添加失败'))
  } finally {
    acting.value = false
  }
}

// ---- 中央/部门 Agent ----

const lifecycleLabel = (status: LifecycleStatus) => ({
  draft: '草稿', reviewing: '待审核', published: '已发布', retired: '已停用',
}[status])

const lifecycleBadgeClass = (status: LifecycleStatus) => ({
  draft: 'bg-slate-100 text-slate-500',
  reviewing: 'bg-amber-50 text-amber-700',
  published: 'bg-emerald-50 text-emerald-700',
  retired: 'bg-red-50 text-red-700',
}[status])

// ---- 智能体编辑器（设定、模型、知识库、技能、发布与试运行都在编辑器组件里）----
const editor = ref<{ visible: boolean; agentId: number | null; mode: 'template' | 'external' }>({ visible: false, agentId: null, mode: 'template' })
const openCreateAgent = () => { editor.value = { visible: true, agentId: null, mode: 'template' } }      // 启用内置智能体
const openIntegrate = () => { editor.value = { visible: true, agentId: null, mode: 'external' } }          // 接入外部智能体服务
const openEditAgent = (a: ManagedAgent) => { editor.value = { visible: true, agentId: a.id, mode: 'template' } }
const closeEditor = () => { editor.value.visible = false; void loadManagedAgents() }

// 划分用的可选项（本企业的部门）
const agentOptions = ref<orgApi.AgentOptions | null>(null)
const loadAgentOptions = async () => { agentOptions.value = await orgApi.getAgentOptions() }

// ---- 划分：部门 / 全企业 / 暂不划分（智能体先统一创建，再由管理员划分）----
const assignDialog = ref({
  visible: false,
  agent: null as ManagedAgent | null,
  target: 'department' as 'department' | 'enterprise' | 'unassigned',
  teamId: null as number | null,
  departmentCode: '',
  saving: false,
  error: '',
})

const openAssign = (a: ManagedAgent) => {
  assignDialog.value = {
    visible: true, agent: a, target: a.assignment === 'unassigned' ? 'department' : a.assignment,
    teamId: a.team_id, departmentCode: '', saving: false, error: '',
  }
}

const submitAssign = async () => {
  const d = assignDialog.value
  if (!d.agent) return
  d.saving = true
  d.error = ''
  try {
    await orgApi.assignManagedAgent(d.agent.id, {
      target: d.target,
      team_id: d.target === 'department' ? (d.teamId ?? undefined) : undefined,
      department_code: d.target === 'department' && d.departmentCode ? d.departmentCode : undefined,
      expected_row_version: d.agent.row_version,
    })
    d.visible = false
    await loadManagedAgents()
  } catch (e: any) {
    d.error = getErrorMessage(e, '划分失败')
  } finally {
    d.saving = false
  }
}

const onSetAgentLifecycle = async (a: ManagedAgent, status: LifecycleStatus) => {
  const verb = status === 'published' ? '发布' : '停用'
  if (!confirm(`确认${verb}「${a.name}」？`)) return
  try {
    await orgApi.updateManagedAgent(a.id, { lifecycle_status: status, expected_row_version: a.row_version })
    await loadManagedAgents()
  } catch (e: any) {
    alert(getErrorMessage(e, `${verb}失败`))
  }
}

onMounted(reloadAll)
</script>
