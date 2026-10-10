<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-sky-200/70 bg-white/80 px-5 py-4 shadow-sm backdrop-blur-xl lg:px-8">
      <div class="mx-auto flex max-w-6xl items-start justify-between gap-3">
        <div class="flex items-start gap-3">
          <button
            @click="router.push('/agents')"
            class="mt-0.5 inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-black/[.06] hover:text-slate-900"
            title="返回工作台"
          >
            <ArrowLeft :size="16" />
          </button>
          <div>
            <h1 class="text-xl font-semibold">知识库中心</h1>
            <p class="mt-1 text-sm text-slate-500">
              为不同业务建立独立的知识库空间（客服 / 制度 / 产品 / 合同…），文档统一在空间里管理，Agent 按需绑定。
            </p>
          </div>
        </div>
        <button
          data-guide="new-space"
          @click="openCreate"
          class="ui-primary inline-flex h-10 shrink-0 items-center gap-2 rounded px-4 text-sm font-medium text-white"
        >
          <Plus :size="16" />新建空间
        </button>
      </div>
    </header>

    <main class="mx-auto w-full max-w-6xl flex-1 overflow-y-auto p-5 lg:p-8">
      <div v-if="loading" class="py-20 text-center text-sm text-slate-500">加载中…</div>
      <div v-else-if="loadError" class="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
        <div class="flex items-center justify-between gap-3">
          <span>{{ loadError }}</span>
          <button @click="reload" class="shrink-0 rounded border border-red-200 bg-white px-3 py-1.5 text-xs hover:bg-red-100">重试</button>
        </div>
      </div>
      <div v-else-if="!spaces.length" class="rounded-lg border border-dashed border-sky-200 bg-white/60 p-12 text-center text-sm text-slate-500">
        还没有知识库空间。点右上角「新建空间」，把一类企业资料归到一起。
      </div>

      <template v-else>
        <div class="mb-4 flex flex-wrap items-center gap-2 text-xs" data-testid="space-scope-filter">
          <button v-for="option in scopeOptions" :key="option.key" @click="scopeFilter = option.key"
            class="rounded-full border px-3 py-1 transition-colors"
            :class="scopeFilter === option.key ? 'border-sky-400 bg-sky-50 text-sky-700' : 'border-sky-200 bg-white text-slate-600 hover:bg-sky-50'">
            {{ option.label }}<span class="ml-1 text-slate-400">{{ option.count }}</span>
          </button>
        </div>
        <div v-if="!visibleSpaces.length" class="rounded-lg border border-dashed border-sky-200 bg-white/60 p-10 text-center text-sm text-slate-500">
          这个分类下还没有知识库空间。
        </div>
      <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <SpaceCard
          v-for="s in visibleSpaces"
          :key="s.id"
          :space="s"
          @open="router.push(`/knowledge-spaces/${s.id}`)"
          @edit="openEdit(s)"
          @delete="onDelete(s)"
        />
      </div>
      </template>
    </main>

    <!-- 新建 / 编辑弹窗 -->
    <div v-if="formOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="formOpen = false">
      <div class="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
        <h3 class="text-sm font-semibold text-slate-900">{{ editing ? '编辑知识库空间' : '新建知识库空间' }}</h3>

        <label class="mt-3 block text-xs text-slate-500">名称</label>
        <input v-model="form.name" maxlength="120" placeholder="例如：企业制度知识库"
          class="mt-1 w-full rounded border border-sky-200 px-3 py-2 text-sm outline-none focus:border-sky-400" />

        <label class="mt-3 block text-xs text-slate-500">用途</label>
        <select v-model="form.purpose"
          class="mt-1 w-full rounded border border-sky-200 px-3 py-2 text-sm outline-none focus:border-sky-400">
          <option :value="null">不指定</option>
          <option v-for="p in purposes" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>

        <p v-if="editing && editing.scope !== 'personal' && editing.scope !== 'shared'" class="mt-3 rounded bg-slate-50 px-3 py-2 text-xs text-slate-500" data-testid="space-assigned-note">
          这个知识库已由管理员划分给{{ assignedText(editing) }}。划分范围和密级由管理员在「企业知识库」里统一调整。
        </p>

        <label class="mt-3 block text-xs text-slate-500">密级</label>
        <select v-model="form.sensitivity" :disabled="!canChangeLevel" data-testid="space-sensitivity-select"
          class="mt-1 w-full rounded border border-sky-200 px-3 py-2 text-sm outline-none focus:border-sky-400 disabled:bg-slate-50 disabled:text-slate-400">
          <option v-for="s in sensitivities" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
        <p class="mt-1 text-[11px] text-slate-400">{{ sensitivityHint }}</p>

        <label class="mt-3 block text-xs text-slate-500">描述（可选）</label>
        <textarea v-model="form.description" rows="2" maxlength="500"
          class="mt-1 w-full resize-none rounded border border-sky-200 px-3 py-2 text-sm outline-none focus:border-sky-400"></textarea>

        <label class="mt-3 block text-xs text-slate-500">标签（逗号分隔，可选）</label>
        <input v-model="tagsText" placeholder="制度, 2024"
          class="mt-1 w-full rounded border border-sky-200 px-3 py-2 text-sm outline-none focus:border-sky-400" />

        <p v-if="formError" class="mt-2 text-xs text-red-600">{{ formError }}</p>

        <div class="mt-4 flex justify-end gap-2">
          <button @click="formOpen = false" class="rounded border border-sky-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-sky-50">取消</button>
          <button @click="submit" :disabled="saving || !form.name.trim()"
            class="ui-primary rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Plus } from 'lucide-vue-next'
import {
  createSpace, deleteSpace, listSpaces, updateSpace,
  type KnowledgeSpace, type SpaceCreatePayload,
} from '../../api/knowledgeSpace'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'
import SpaceCard from '../../components/knowledge/SpaceCard.vue'

const router = useRouter()

const spaces = ref<KnowledgeSpace[]>([])
const purposes = ref<{ key: string; label: string }[]>([])
const sensitivities = ref<{ key: string; label: string }[]>([])
const scopeFilter = ref<string>('all')
const loading = ref(true)
const loadError = ref('')

const formOpen = ref(false)
const editing = ref<KnowledgeSpace | null>(null)
const form = reactive<SpaceCreatePayload>({ name: '', description: '', purpose: null, tags: [], sensitivity: 'internal' })
const tagsText = ref('')
const formError = ref('')
const saving = ref(false)

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await listSpaces()
    spaces.value = res.items
    purposes.value = res.purposes
    sensitivities.value = res.sensitivities || []
  } catch (e: any) {
    loadError.value = getErrorMessage(e, '加载知识库空间失败')
  } finally {
    loading.value = false
  }
}

// 筛选：全部 / 我的与分享 / 全企业 / 各部门（一个知识库划分给了多个部门，就出现在每个部门下）
const scopeOptions = computed(() => {
  const departments = new Map<number, { name: string; count: number }>()
  let mine = 0
  let company = 0
  for (const s of spaces.value) {
    if (s.scope === 'department') {
      for (const d of s.departments) {
        const entry = departments.get(d.id) || { name: d.name, count: 0 }
        entry.count += 1
        departments.set(d.id, entry)
      }
    } else if (s.scope === 'enterprise') {
      company += 1
    } else {
      mine += 1
    }
  }
  const options = [{ key: 'all', label: '全部', count: spaces.value.length }]
  if (mine) options.push({ key: 'mine', label: '我的与分享', count: mine })
  if (company) options.push({ key: 'enterprise', label: '全企业', count: company })
  for (const [id, entry] of departments) options.push({ key: `team:${id}`, label: entry.name, count: entry.count })
  return options
})

const visibleSpaces = computed(() => {
  const key = scopeFilter.value
  if (key === 'all') return spaces.value
  if (key === 'mine') return spaces.value.filter((s) => s.scope === 'personal' || s.scope === 'shared')
  if (key === 'enterprise') return spaces.value.filter((s) => s.scope === 'enterprise')
  const teamId = Number(key.replace('team:', ''))
  return spaces.value.filter((s) => s.scope === 'department' && s.departments.some((d) => d.id === teamId))
})

// 已经划分给部门 / 全企业的知识库，密级由管理员统一调整
const canChangeLevel = computed(() => !editing.value || editing.value.scope === 'personal')

const assignedText = (s: KnowledgeSpace) =>
  s.scope === 'enterprise' ? '全企业' : s.departments.map((d) => `「${d.name}」`).join('、') || '部门'

const sensitivityHint = computed(() => {
  switch (form.sensitivity) {
    case 'public': return '公开：可以发给任何模型。'
    case 'confidential': return '机密：只会发给企业批准的模型，不会发给外部云模型和外部智能体服务。'
    case 'restricted': return '绝密：不会发给任何模型；发布在部门里时，部门成员也不会自动获得访问权限。'
    default: return '内部：默认级别，可以发给已配置的模型。'
  }
})

function openCreate() {
  editing.value = null
  form.name = ''
  form.description = ''
  form.sensitivity = 'internal'
  form.purpose = null
  tagsText.value = ''
  formError.value = ''
  formOpen.value = true
}

function openEdit(s: KnowledgeSpace) {
  editing.value = s
  form.name = s.name
  form.description = s.description
  form.sensitivity = s.sensitivity || 'internal'
  form.purpose = s.purpose
  tagsText.value = s.tags.join(', ')
  formError.value = ''
  formOpen.value = true
}

async function submit() {
  saving.value = true
  formError.value = ''
  const payload: SpaceCreatePayload = {
    name: form.name.trim(),
    description: form.description?.trim() || '',
    purpose: form.purpose,
    tags: tagsText.value.split(',').map((t) => t.trim()).filter(Boolean),
  }
  // 已划分的知识库密级由管理员统一调整（后端也会拒绝），这里不带
  if (canChangeLevel.value) payload.sensitivity = form.sensitivity
  try {
    if (editing.value) await updateSpace(editing.value.id, payload)
    else await createSpace(payload)
    formOpen.value = false
    toastSuccess(editing.value ? '已更新' : '已创建')
    await reload()
  } catch (e: any) {
    formError.value = getErrorMessage(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function onDelete(s: KnowledgeSpace) {
  if (!window.confirm(`删除「${s.name}」？空间下有文档时无法删除。`)) return
  try {
    await deleteSpace(s.id)
    toastSuccess('已删除')
    await reload()
  } catch (e: any) {
    toastError(getErrorMessage(e, '删除失败'))
  }
}

onMounted(reload)
</script>
