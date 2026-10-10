<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-5">
      <p class="text-xs text-slate-500">智能体由工程师开发好再接入平台；也可以启用平台内置的业务智能体。先划分给部门或全企业，检查通过再发布。</p>
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

    <div class="rounded-lg border border-slate-200 bg-white">
      <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <p class="text-sm font-medium text-slate-700">共 {{ managedAgents.length }} 个智能体</p>
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
  </div>
</template>

<script setup lang="ts">
import { departmentCodeLabel } from '../../utils/displayNames'
import { onMounted, ref } from 'vue'
import { Plus, RefreshCcw } from 'lucide-vue-next'
import * as orgApi from '../../api/organizationAdmin'
import ManagedAgentEditor from '../../components/admin/ManagedAgentEditor.vue'
import type { ManagedAgent, LifecycleStatus } from '../../api/organizationAdmin'
import { getErrorMessage } from '../../utils/request'

const loading = ref(true)
const errorMsg = ref('')
const managedAgents = ref<ManagedAgent[]>([])

const loadManagedAgents = async () => {
  managedAgents.value = await orgApi.listManagedAgents()
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

const reloadAll = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    await Promise.all([loadManagedAgents(), loadAgentOptions()])
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载智能体失败')
  } finally {
    loading.value = false
  }
}

onMounted(reloadAll)
</script>
