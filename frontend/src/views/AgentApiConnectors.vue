<template>
  <div class="flex h-screen flex-col bg-transparent text-slate-950">
    <header class="border-b border-sky-200/70 ui-glass px-5 py-3 shadow-sm backdrop-blur-xl lg:px-8">
      <AgentSubnav :agent-id="agentId" :agent-name="agent?.name" active="tools" />
    </header>

    <main class="mx-auto w-full max-w-5xl flex-1 space-y-5 overflow-y-auto p-5 lg:p-8">
      <!-- 已配置的接口工具 -->
      <section class="rounded-lg border border-sky-200 bg-white/80 p-4">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-slate-900">企业接口工具</h2>
          <button
            @click="loadConnectors"
            class="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >刷新</button>
        </div>
        <p class="mb-3 text-xs text-slate-500">
          给这个助手配一个真实的企业接口，运行时大模型会按下面的参数说明自己决定要不要调、传什么参数——
          接口地址和认证信息由你在这里配好，大模型改不了。
        </p>

        <div v-if="loadingList" class="py-6 text-center text-xs text-slate-400">加载中…</div>
        <div v-else-if="connectors.length === 0"
             class="rounded border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-400">
          还没有配置任何接口工具，在下面添加一个吧。
        </div>
        <div v-else class="space-y-2">
          <div v-for="c in connectors" :key="c.id"
               class="rounded border border-slate-200 bg-white px-3 py-2.5">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="font-mono text-sm font-medium text-slate-800">{{ c.name }}</span>
                  <span class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ c.method }}</span>
                  <span v-if="!c.is_enabled" class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">已停用</span>
                  <span v-if="c.has_headers" class="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-700">带认证头</span>
                </div>
                <p class="mt-0.5 truncate text-xs text-slate-500">{{ c.description }}</p>
                <p class="mt-0.5 truncate text-[11px] text-slate-400">{{ c.url }}</p>
                <div v-if="paramNames(c).length" class="mt-1 flex flex-wrap gap-1">
                  <span v-for="p in paramNames(c)" :key="p"
                        class="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-700">{{ p }}</span>
                </div>
              </div>
              <div class="flex shrink-0 items-center gap-1.5">
                <button
                  @click="toggleEnabled(c)"
                  class="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
                >{{ c.is_enabled ? '停用' : '启用' }}</button>
                <button
                  @click="removeConnector(c)"
                  class="rounded border border-red-200 bg-white px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                >删除</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 新建接口工具：默认只留给管理员——单企业部署下接入外部系统应由管理员统一审核配置，
           不是员工自助接的（后端 FasdtApi/agent.py 的 create_api_connector 同步做了强制校验，
           这里只是不显示入口，不是唯一防线）。 -->
      <p v-if="!isAdmin" class="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
        新增接口工具需要管理员权限，如需接入新的企业系统请联系管理员配置。
      </p>
      <section v-else class="rounded-lg border border-sky-200 bg-white/80 p-4">
        <h2 class="mb-3 text-sm font-semibold text-slate-900">新增接口工具</h2>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">工具名（给大模型看，英文标识符）</label>
            <input v-model="form.name" placeholder="英文小写字母开头，只能含字母、数字和下划线"
                   class="h-9 w-full rounded border border-slate-300 px-2 text-sm outline-none focus:border-sky-400" />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-600">请求方式</label>
            <select v-model="form.method"
                    class="h-9 w-full rounded border border-slate-300 px-2 text-sm outline-none focus:border-sky-400">
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
          </div>
        </div>

        <div class="mt-3">
          <label class="mb-1 block text-xs font-medium text-slate-600">接口说明（告诉大模型什么时候该用、参数是什么意思）</label>
          <textarea v-model="form.description" rows="2" placeholder="查询订单状态，输入订单号返回当前物流状态"
                    class="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-sky-400"></textarea>
        </div>

        <div class="mt-3">
          <label class="mb-1 block text-xs font-medium text-slate-600">接口地址</label>
          <input v-model="form.url" placeholder="https://api.your-company.com/orders"
                 class="h-9 w-full rounded border border-slate-300 px-2 text-sm outline-none focus:border-sky-400" />
        </div>

        <!-- 参数说明：大模型运行时只能填这些参数 -->
        <div class="mt-4">
          <div class="mb-1.5 flex items-center justify-between">
            <label class="text-xs font-medium text-slate-600">参数（大模型调用时需要填的值）</label>
            <button @click="addParamRow" type="button"
                    class="rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50">+ 加一个参数</button>
          </div>
          <div v-if="form.params.length === 0" class="rounded border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-center text-[11px] text-slate-400">
            没有参数也可以——比如一个"获取当前库存总览"的接口
          </div>
          <div v-for="(p, i) in form.params" :key="i" class="mb-1.5 grid grid-cols-[1fr_90px_1fr_60px_28px] items-center gap-1.5">
            <input v-model="p.name" placeholder="参数名（英文小写字母、数字或下划线）"
                   class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-sky-400" />
            <select v-model="p.type" class="h-8 rounded border border-slate-300 px-1 text-xs outline-none focus:border-sky-400">
              <option value="string">string</option>
              <option value="integer">integer</option>
              <option value="number">number</option>
              <option value="boolean">boolean</option>
            </select>
            <input v-model="p.description" placeholder="参数说明"
                   class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-sky-400" />
            <label class="flex items-center justify-center gap-1 text-[11px] text-slate-500">
              <input type="checkbox" v-model="p.required" /> 必填
            </label>
            <button @click="form.params.splice(i, 1)" type="button" class="text-slate-400 hover:text-red-600" title="删除">
              <X :size="14" />
            </button>
          </div>
        </div>

        <!-- 认证头：可选，加密存储 -->
        <div class="mt-4">
          <div class="mb-1.5 flex items-center justify-between">
            <label class="text-xs font-medium text-slate-600">固定请求头（可选，比如 Authorization，加密存储）</label>
            <button @click="addHeaderRow" type="button"
                    class="rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50">+ 加一行</button>
          </div>
          <div v-for="(h, i) in form.headers" :key="i" class="mb-1.5 grid grid-cols-[1fr_1fr_28px] items-center gap-1.5">
            <input v-model="h.key" placeholder="Authorization"
                   class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-sky-400" />
            <input v-model="h.value" placeholder="Bearer xxx" type="password"
                   class="h-8 rounded border border-slate-300 px-2 text-xs outline-none focus:border-sky-400" />
            <button @click="form.headers.splice(i, 1)" type="button" class="text-slate-400 hover:text-red-600" title="删除">
              <X :size="14" />
            </button>
          </div>
        </div>

        <p v-if="errorMsg" class="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{{ errorMsg }}</p>
        <p v-if="successMsg" class="mt-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{{ successMsg }}</p>

        <button
          @click="submit" :disabled="submitting"
          class="ui-primary mt-3 h-9 rounded px-4 text-sm font-medium text-white disabled:opacity-40"
        >{{ submitting ? '创建中…' : '创建接口工具' }}</button>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { X } from 'lucide-vue-next'
import AgentSubnav from '../components/agent/AgentSubnav.vue'
import { getAgent, type AgentInfo } from '../api/agent'
import {
  listApiConnectors, createApiConnector, setApiConnectorEnabled, deleteApiConnector,
  type ApiConnector,
} from '../api/agentConnector'
import { getErrorMessage } from '../utils/request'
import { useUserStore } from '../stores/user'

const props = defineProps<{ agentId: string | number }>()
const agentId = computed(() => Number(props.agentId))
const userStore = useUserStore()
const isAdmin = computed(() => Boolean(userStore.user?.is_admin))

const agent = ref<AgentInfo | null>(null)
const connectors = ref<ApiConnector[]>([])
const loadingList = ref(false)

const errorMsg = ref('')
const successMsg = ref('')
const submitting = ref(false)

interface ParamRow { name: string; type: 'string' | 'integer' | 'number' | 'boolean'; description: string; required: boolean }
interface HeaderRow { key: string; value: string }

const form = reactive({
  name: '',
  description: '',
  url: '',
  method: 'GET' as 'GET' | 'POST',
  params: [] as ParamRow[],
  headers: [] as HeaderRow[],
})

const addParamRow = () => form.params.push({ name: '', type: 'string', description: '', required: true })
const addHeaderRow = () => form.headers.push({ key: '', value: '' })

const paramNames = (c: ApiConnector) => Object.keys(c.param_schema?.properties || {})

const loadConnectors = async () => {
  loadingList.value = true
  try {
    connectors.value = await listApiConnectors(agentId.value)
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '加载失败')
  } finally {
    loadingList.value = false
  }
}

const toggleEnabled = async (c: ApiConnector) => {
  try {
    await setApiConnectorEnabled(c.id, !c.is_enabled)
    await loadConnectors()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '操作失败')
  }
}

const removeConnector = async (c: ApiConnector) => {
  if (!confirm(`确定删除「${c.name}」吗？`)) return
  try {
    await deleteApiConnector(c.id)
    await loadConnectors()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '删除失败')
  }
}

const resetForm = () => {
  form.name = ''
  form.description = ''
  form.url = ''
  form.method = 'GET'
  form.params = []
  form.headers = []
}

const submit = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  const name = form.name.trim()
  const description = form.description.trim()
  const url = form.url.trim()
  if (!name || !description || !url) {
    errorMsg.value = '工具名、接口说明、接口地址都不能为空'
    return
  }
  const cleanParams = form.params.filter((p) => p.name.trim())
  const properties: Record<string, any> = {}
  const required: string[] = []
  for (const p of cleanParams) {
    properties[p.name.trim()] = { type: p.type, description: p.description.trim() || undefined }
    if (p.required) required.push(p.name.trim())
  }
  const headers: Record<string, string> = {}
  for (const h of form.headers) {
    if (h.key.trim()) headers[h.key.trim()] = h.value
  }

  submitting.value = true
  try {
    await createApiConnector(agentId.value, {
      name, description, url, method: form.method,
      headers: Object.keys(headers).length ? headers : undefined,
      param_schema: { type: 'object', properties, required },
    })
    successMsg.value = '创建成功'
    resetForm()
    await loadConnectors()
  } catch (e: any) {
    errorMsg.value = getErrorMessage(e, '创建失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    agent.value = await getAgent(agentId.value)
  } catch { agent.value = null }
  await loadConnectors()
})
</script>
