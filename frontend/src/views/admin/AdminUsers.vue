<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-5">
      <p class="text-xs text-slate-500">管理账号、所属部门和企业角色。成员注册后自动属于本企业；部门的划分与负责人在「组织架构」里设置。</p>
      <button
        @click="loadUsers"
        :disabled="loading"
        class="inline-flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:text-slate-300"
      >
        <RefreshCcw :size="14" :class="loading ? 'animate-spin' : ''" />
        刷新
      </button>
    </div>

    <p v-if="errorMsg" class="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{{ errorMsg }}</p>

    <section class="rounded-lg border border-slate-200 bg-white">
      <div class="border-b border-slate-200 px-4 py-3">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h2 class="text-sm font-semibold text-slate-900">用户列表</h2>
          <span class="text-xs text-slate-400">{{ filteredUsers.length }} / 共 {{ total }} 个用户</span>
        </div>
        <div class="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto]">
          <div class="relative">
            <Search class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" :size="14" />
            <input
              v-model="userQuery"
              type="text"
              class="h-9 w-full rounded border border-slate-300 pl-8 pr-3 text-sm outline-none focus:border-indigo-500"
              placeholder="搜索用户名或手机号"
            />
          </div>
          <div class="grid grid-cols-5 gap-1 rounded bg-slate-100 p-1 text-xs">
            <button
              v-for="item in userFilters"
              :key="item.value"
              @click="userFilter = item.value"
              :class="userFilter === item.value ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
              class="h-7 rounded px-2 transition"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[820px] text-left text-sm">
          <thead class="border-b border-slate-100 bg-slate-50 text-xs text-slate-500">
            <tr>
              <th class="px-4 py-3 font-medium">用户</th>
              <th class="px-4 py-3 font-medium">状态</th>
              <th class="px-4 py-3 font-medium">部门与角色</th>
              <th class="px-4 py-3 font-medium">套餐</th>
              <th class="px-4 py-3 font-medium">资源</th>
              <th class="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in filteredUsers" :key="user.id" class="border-b border-slate-100 hover:bg-slate-50/50">
              <td class="px-4 py-3">
                <p class="font-medium text-slate-900">{{ user.name }}</p>
                <p class="text-xs text-slate-400">ID {{ user.id }} · 手机 {{ user.phone || '-' }}</p>
                <p class="text-xs text-slate-400">年龄 {{ user.age ?? '-' }} · 最后登录 {{ user.last_login_at || '-' }}</p>
              </td>
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1.5">
                  <span
                    class="rounded px-2 py-1 text-xs"
                    :class="user.is_disabled === 1 ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'"
                  >
                    {{ user.is_disabled === 1 ? '已禁用' : '正常' }}
                  </span>
                  <span
                    class="rounded px-2 py-1 text-xs"
                    :class="user.is_online ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'"
                  >
                    {{ user.is_online ? '在线' : '离线' }}
                  </span>
                </div>
                <p class="mt-1 text-xs text-slate-400">最近访问 {{ user.last_seen_at || '-' }}</p>
              </td>
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1" data-testid="user-departments">
                  <span
                    v-for="d in user.departments || []"
                    :key="d.id"
                    class="rounded bg-sky-50 px-2 py-1 text-xs text-sky-700"
                    :class="d.status !== 'active' ? 'opacity-50' : ''"
                  >
                    {{ d.name }}<span v-if="d.role_code === 'admin'" class="ml-1 text-sky-500">负责人</span>
                  </span>
                  <span v-if="!user.departments?.length" class="text-xs text-slate-400">未分配部门</span>
                </div>
                <div class="mt-1.5 flex items-center gap-1.5">
                  <select
                    v-if="user.org_role_code"
                    :value="user.org_role_code"
                    @change="onOrgRoleChange(user, $event)"
                    data-testid="user-org-role"
                    class="h-7 rounded border border-slate-200 px-1.5 text-xs text-slate-700 outline-none focus:border-indigo-500"
                    :title="'企业角色'"
                  >
                    <option v-for="r in orgRoles" :key="r.code" :value="r.code">{{ r.name }}</option>
                  </select>
                  <span v-else class="text-xs text-slate-400">未加入企业</span>
                  <span v-if="user.roles.includes('admin')" class="rounded bg-indigo-50 px-2 py-0.5 text-xs text-indigo-600">后台管理员</span>
                </div>
              </td>
              <td class="px-4 py-3">
                <select
                  :value="planIdForUser(user)"
                  @change="onPlanChange(user, $event)"
                  class="h-8 rounded border border-slate-200 px-1.5 text-xs text-slate-700 outline-none focus:border-indigo-500"
                >
                  <option value="">默认套餐</option>
                  <option v-for="p in plans" :key="p.id" :value="p.id">{{ p.display_name }}</option>
                </select>
              </td>
              <td class="px-4 py-3 text-xs text-slate-500">
                助手 {{ user.agent_count }} · 能力 {{ user.skill_count }} · 文档 {{ user.knowledge_count }} · 任务 {{ user.task_count }}
              </td>
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1.5">
                  <button
                    @click="openDetail(user)"
                    class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
                  >
                    详情
                  </button>
                  <button
                    @click="toggleDisabled(user)"
                    class="rounded border px-2.5 py-1 text-xs transition-colors"
                    :class="user.is_disabled === 1
                      ? 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'
                      : 'border-red-200 text-red-700 hover:bg-red-50'"
                  >
                    {{ user.is_disabled === 1 ? '启用' : '禁用' }}
                  </button>
                  <button
                    @click="toggleAdmin(user)"
                    class="rounded border px-2.5 py-1 text-xs transition-colors"
                    :class="user.roles.includes('admin')
                      ? 'border-amber-200 text-amber-700 hover:bg-amber-50'
                      : 'border-indigo-200 text-indigo-700 hover:bg-indigo-50'"
                  >
                    {{ user.roles.includes('admin') ? '取消管理员' : '设为管理员' }}
                  </button>
                  <button
                    @click="openPasswordDialog(user)"
                    class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
                  >
                    改密码
                  </button>
                  <button
                    @click="deleteUser(user)"
                    class="rounded border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!filteredUsers.length && !loading" class="py-12 text-center text-sm text-slate-500">
          {{ users.length ? '当前页没有匹配筛选条件的用户。' : '没有找到用户。' }}
        </div>
      </div>
      <AdminPagination :total="total" :limit="limit" :offset="offset" @update:offset="onPageChange" />
    </section>

    <!-- 用户详情弹窗 -->
    <div v-if="detailDialog.visible" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50" @click.self="closeDetail">
      <div class="bg-white rounded-xl p-5 w-full max-w-lg shadow-xl">
        <div class="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 class="text-base font-semibold text-slate-800">用户详情</h3>
            <p class="mt-1 text-xs text-slate-500">{{ detailDialog.user?.name }} · ID {{ detailDialog.user?.id }}</p>
          </div>
          <button @click="closeDetail" class="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">×</button>
        </div>
        <div v-if="detailDialog.user" class="grid grid-cols-2 gap-3 text-sm">
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">状态</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.is_disabled === 1 ? '已禁用' : '正常' }}</p>
          </article>
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">在线状态</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.is_online ? '在线' : '离线' }}</p>
          </article>
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">角色</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.roles.join(', ') || '无' }}</p>
          </article>
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">手机号</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.phone || '-' }}</p>
          </article>
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">最后登录</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.last_login_at || '-' }}</p>
          </article>
          <article class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">最近访问</p>
            <p class="mt-1 font-semibold text-slate-900">{{ detailDialog.user.last_seen_at || '-' }}</p>
          </article>
          <article v-for="item in detailCounts" :key="item.key" class="rounded border border-slate-100 p-3">
            <p class="text-xs text-slate-500">{{ item.label }}</p>
            <p class="mt-1 font-semibold text-slate-900">{{ item.value }}</p>
          </article>
        </div>
      </div>
    </div>

    <!-- 改密码弹窗 -->
    <div v-if="passwordDialog.visible" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50" @click.self="closePasswordDialog">
      <div class="bg-white rounded-xl p-5 w-96 shadow-xl">
        <h3 class="text-base font-semibold text-slate-800 mb-1">修改密码</h3>
        <p class="text-xs text-slate-500 mb-3">用户：{{ passwordDialog.userName }} (ID {{ passwordDialog.userId }})</p>
        <input
          v-model="passwordDialog.newPassword"
          type="password"
          placeholder="输入新密码"
          class="h-10 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
        />
        <div class="flex justify-end gap-2 mt-4">
          <button @click="closePasswordDialog" class="px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 rounded">取消</button>
          <button
            @click="submitPassword"
            :disabled="!passwordDialog.newPassword || acting"
            class="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
          >
            确认
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RefreshCcw, Search } from 'lucide-vue-next'
import AdminPagination from '../../components/admin/AdminPagination.vue'
import * as adminApi from '../../api/admin'
import type { AdminPlan, AdminUser } from '../../api/admin'
import * as orgApi from '../../api/organizationAdmin'
import type { EnterpriseRoleOption } from '../../api/organizationAdmin'
import { getErrorMessage } from '../../utils/request'

const users = ref<AdminUser[]>([])
const plans = ref<AdminPlan[]>([])
const orgRoles = ref<EnterpriseRoleOption[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const userQuery = ref('')
const userFilter = ref<'all' | 'active' | 'disabled' | 'online' | 'admin'>('all')
const loading = ref(false)
const errorMsg = ref('')
const acting = ref(false)
let searchDebounce: ReturnType<typeof setTimeout> | undefined

const passwordDialog = ref({
  visible: false,
  userId: 0,
  userName: '',
  newPassword: '',
})

const detailDialog = ref<{
  visible: boolean
  user: AdminUser | null
}>({
  visible: false,
  user: null,
})

const detailLabels: Record<string, string> = {
  llm_configs: '模型连接',
  conversations: '会话',
  messages: '消息',
  runs: '运行记录',
  memories: '记忆',
  legacy_chats: '旧聊天记录',
}

const detailCounts = computed(() => Object.entries(detailLabels).map(([key, label]) => ({
  key,
  label,
  value: detailDialog.value.user?.counts?.[key] ?? 0,
})))

const userFilters: Array<{ label: string; value: 'all' | 'active' | 'disabled' | 'online' | 'admin' }> = [
  { label: '全部', value: 'all' },
  { label: '正常', value: 'active' },
  { label: '禁用', value: 'disabled' },
  { label: '在线', value: 'online' },
  { label: '管理员', value: 'admin' },
]

// 用户名/手机号搜索已经交给后端（分页场景下前端只筛选当前这一页没意义）；
// 这几个快捷筛选（正常/禁用/在线/管理员）只在当前页内做，不额外发请求。
const filteredUsers = computed(() => {
  return users.value.filter((user) => {
    if (userFilter.value === 'active' && user.is_disabled === 1) return false
    if (userFilter.value === 'disabled' && user.is_disabled !== 1) return false
    if (userFilter.value === 'online' && !user.is_online) return false
    if (userFilter.value === 'admin' && !user.roles.includes('admin')) return false
    return true
  })
})

const loadUsers = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const page = await adminApi.listAdminUsers({
      limit: limit.value, offset: offset.value, search: userQuery.value.trim() || undefined,
    })
    users.value = page.items
    total.value = page.total
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

const onPageChange = (nextOffset: number) => {
  offset.value = nextOffset
  loadUsers()
}

watch(userQuery, () => {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    offset.value = 0
    loadUsers()
  }, 300)
})

onUnmounted(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})

const toggleAdmin = async (user: AdminUser) => {
  const nextRoles = user.roles.includes('admin')
    ? user.roles.filter((r) => r !== 'admin')
    : [...user.roles, 'admin']
  try {
    await adminApi.updateUserRoles(user.id, nextRoles)
    await loadUsers()
  } catch (e: any) {
    alert(getErrorMessage(e, '更新角色失败'))
  }
}

const toggleDisabled = async (user: AdminUser) => {
  const disabled = user.is_disabled !== 1
  if (!confirm(`确认${disabled ? '禁用' : '启用'}用户「${user.name}」？`)) return
  try {
    await adminApi.updateUserStatus(user.id, disabled)
    await loadUsers()
  } catch (e: any) {
    alert(getErrorMessage(e, disabled ? '禁用失败' : '启用失败'))
  }
}

const openDetail = async (user: AdminUser) => {
  try {
    detailDialog.value = {
      visible: true,
      user: await adminApi.getAdminUser(user.id),
    }
  } catch (e: any) {
    alert(getErrorMessage(e, '读取用户详情失败'))
  }
}

const closeDetail = () => {
  detailDialog.value.visible = false
  detailDialog.value.user = null
}

const openPasswordDialog = (user: AdminUser) => {
  passwordDialog.value = {
    visible: true,
    userId: user.id,
    userName: user.name,
    newPassword: '',
  }
}

const closePasswordDialog = () => {
  passwordDialog.value.visible = false
}

const submitPassword = async () => {
  acting.value = true
  try {
    await adminApi.resetUserPassword(passwordDialog.value.userId, passwordDialog.value.newPassword)
    closePasswordDialog()
    await loadUsers()
  } catch (e: any) {
    alert(getErrorMessage(e, '重置密码失败'))
  } finally {
    acting.value = false
  }
}

const deleteUser = async (user: AdminUser) => {
  if (!confirm(`确认删除用户「${user.name}」？该用户的所有助手、能力、资料库和会话都会被删除，此操作不可撤销。`)) return
  try {
    await adminApi.deleteAdminUser(user.id)
    await loadUsers()
  } catch (e: any) {
    alert(getErrorMessage(e, '删除失败'))
  }
}

const loadPlans = async () => {
  try {
    plans.value = await adminApi.listAdminPlans()
  } catch {
    // 套餐列表加载失败不影响用户列表主流程，下拉框留空即可
  }
}

const planIdForUser = (user: AdminUser): number | '' => {
  const plan = plans.value.find((p) => p.name === user.plan_name)
  return plan ? plan.id : ''
}

const loadOrgRoles = async () => {
  try {
    orgRoles.value = (await orgApi.getEnterpriseRoles()).organization
  } catch {
    // 角色目录加载失败不影响用户列表，下拉框留空即可
  }
}

const onOrgRoleChange = async (user: AdminUser, event: Event) => {
  const select = event.target as HTMLSelectElement
  try {
    await orgApi.updateOrgMember(user.id, { role_code: select.value })
    await loadUsers()
  } catch (e: any) {
    select.value = user.org_role_code || ''
    alert(getErrorMessage(e, '修改企业角色失败'))
  }
}

const onPlanChange = async (user: AdminUser, event: Event) => {
  const value = (event.target as HTMLSelectElement).value
  if (!value) return
  try {
    await adminApi.assignUserPlan(user.id, Number(value))
    await loadUsers()
  } catch (e: any) {
    alert(getErrorMessage(e, '分配套餐失败'))
  }
}

onMounted(() => {
  loadUsers()
  loadPlans()
  loadOrgRoles()
})
</script>
