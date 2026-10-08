<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3 sm:p-6" @click.self="requestClose" data-testid="agent-editor">
    <div class="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-white shadow-xl">
      <header class="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-3">
        <div class="min-w-0">
          <h3 class="truncate text-base font-semibold text-slate-900">{{ currentId ? `编辑智能体：${form.name || ''}` : '新建智能体' }}</h3>
          <p class="mt-0.5 text-xs text-slate-500">
            {{ currentId ? '修改后点保存生效；发布前请看「发布与试运行」里的检查。' : '先把它的设定、模型、知识和技能配好；创建后再在列表里「划分」给部门或全企业。' }}
          </p>
        </div>
        <button @click="requestClose" class="shrink-0 rounded p-1.5 text-slate-500 hover:bg-slate-100" aria-label="关闭"><X :size="18" /></button>
      </header>

      <!-- 签名密钥只显示这一次 -->
      <div v-if="runtime.secret" class="space-y-3 overflow-y-auto p-5" data-testid="runtime-secret-panel">
        <p class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          签名密钥只显示这一次，关闭后无法再查看，只能重新生成。请现在复制，配置到你的智能体服务里。
        </p>
        <div class="flex items-center gap-2">
          <code class="min-w-0 flex-1 break-all rounded bg-slate-100 px-3 py-2 text-xs text-slate-800">{{ runtime.secret }}</code>
          <button @click="copySecret" class="shrink-0 rounded border border-slate-200 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50">{{ runtime.copied ? '已复制' : '复制' }}</button>
        </div>
        <p class="text-xs text-slate-500">你的服务用它校验平台请求的签名，写法见项目文档 docs/external-agent-protocol.md。</p>
        <div class="flex justify-end">
          <button @click="closeSecretPanel" class="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">我已保存密钥</button>
        </div>
      </div>

      <div v-else-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</div>

      <div v-else class="flex min-h-0 flex-1 flex-col md:flex-row">
        <!-- 分区导航 -->
        <nav class="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 p-2 md:w-44 md:flex-col md:overflow-visible md:border-b-0 md:border-r" role="tablist" aria-label="配置分区">
          <button v-for="item in sections" :key="item.key" role="tab" :aria-selected="section === item.key"
            :disabled="item.disabled" :data-testid="`agent-section-${item.key}`" @click="section = item.key"
            class="flex shrink-0 items-center justify-between gap-2 rounded px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            :class="section === item.key ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-700 hover:bg-slate-50'">
            <span>{{ item.label }}</span>
            <span v-if="item.badge" class="rounded-full px-1.5 text-[11px]" :class="item.badgeClass">{{ item.badge }}</span>
          </button>
        </nav>

        <div class="min-h-0 flex-1 overflow-y-auto p-5">
          <!-- ========== 基本信息 ========== -->
          <section v-show="section === 'basic'" class="space-y-4">
            <div>
              <label for="agent-name" class="mb-1 block text-xs text-slate-500">名称</label>
              <input id="agent-name" v-model="form.name" maxlength="255" placeholder="例如：销售部报价助手"
                class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
            </div>

            <div v-if="!currentId" class="grid gap-4 sm:grid-cols-2">
              <div>
                <label for="agent-type" class="mb-1 block text-xs text-slate-500">类型</label>
                <select id="agent-type" v-model="form.agent_type" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                  <option value="central">中央智能体（全企业可用）</option>
                  <option value="department">业务智能体（创建后再划分给部门）</option>
                </select>
              </div>
              <div v-if="form.agent_type === 'department'">
                <label for="agent-direction" class="mb-1 block text-xs text-slate-500">业务方向</label>
                <select id="agent-direction" v-model="form.department_code" class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                  <option value="">通用办公</option>
                  <option v-for="(label, code) in DIRECTIONS" :key="code" :value="code">{{ label }}</option>
                </select>
              </div>
            </div>
            <p v-else class="text-xs text-slate-500">
              {{ detail?.assignment === 'enterprise' ? '中央智能体，全企业可用。' : detail?.assignment === 'department' ? `业务智能体，划分给「${detail?.team_name || '部门'}」。` : '业务智能体，还没有划分。' }}
              调整划分请回到列表点「划分」。
            </p>

            <div v-if="!currentId && templatesForType.length">
              <label for="agent-template" class="mb-1 block text-xs text-slate-500">使用预置模板（可选）</label>
              <select id="agent-template" :value="form.template_id" @change="applyTemplate(($event.target as HTMLSelectElement).value)"
                class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                <option value="">不使用模板，自己填写</option>
                <option v-for="tpl in templatesForType" :key="tpl.id" :value="tpl.id">{{ tpl.name }}</option>
              </select>
              <p v-if="chosenTemplate" class="mt-1 text-xs text-slate-500">
                {{ chosenTemplate.description }} 会自动填好角色和任务，并配一个专属的专业技能（{{ chosenTemplate.tools.length }} 个业务工具）。
              </p>
            </div>

            <!-- 运行方式 -->
            <div class="rounded-lg border border-slate-200 p-3">
              <label for="agent-runtime" class="mb-1 block text-xs text-slate-500">运行方式</label>
              <select id="agent-runtime" v-model="runtime.type" data-testid="runtime-type-select"
                class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                <option value="builtin">平台自带（设定 + 模型 + 知识库 + 技能，在本页配置）</option>
                <option value="external">接入我们自己部署的智能体服务</option>
              </select>
              <div v-if="runtime.type === 'external'" class="mt-3 space-y-3 rounded-lg border border-amber-200 bg-amber-50/40 p-3" data-testid="runtime-external-fields">
                <p class="text-xs text-slate-600">
                  平台会把提问者的身份和对话转发到你的服务，由你的服务决定怎么思考、调用什么工具、使用什么模型；
                  平台继续负责权限、限流、审计和密级。请求带签名，协议见 docs/external-agent-protocol.md。
                </p>
                <div>
                  <label for="runtime-url" class="mb-1 block text-xs text-slate-500">服务地址</label>
                  <input id="runtime-url" v-model="runtime.url" type="text" placeholder="https://agent.example.com/chat" data-testid="runtime-url"
                    class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
                  <p class="mt-1 text-[11px] text-slate-400">企业内网里的服务需要先让运维把它的主机加入平台的允许名单。</p>
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label for="runtime-timeout" class="mb-1 block text-xs text-slate-500">超时（秒）</label>
                    <input id="runtime-timeout" v-model.number="runtime.timeout" type="number" min="1" max="300"
                      class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500" />
                  </div>
                  <label class="flex items-end gap-2 pb-2 text-xs text-slate-600">
                    <input v-model="runtime.sendKnowledge" type="checkbox" class="h-4 w-4" />
                    同时发送检索到的资料片段
                  </label>
                </div>
                <div>
                  <label for="runtime-headers" class="mb-1 block text-xs text-slate-500">附加请求头（每行一个，格式：名称: 值；留空表示不修改）</label>
                  <textarea id="runtime-headers" v-model="runtime.headersText" rows="2" placeholder="Authorization: Bearer xxxx"
                    class="w-full rounded border border-slate-300 px-3 py-1.5 font-mono text-xs outline-none focus:border-indigo-500"></textarea>
                  <p v-if="runtime.info?.endpoint?.header_names?.length" class="mt-1 text-[11px] text-slate-400">
                    已保存的请求头：{{ runtime.info.endpoint.header_names.join('、') }}（出于安全不显示内容）
                  </p>
                </div>
                <div v-if="currentId && runtime.info?.endpoint" class="flex flex-wrap items-center gap-2 text-xs">
                  <button @click="runConnectionTest" :disabled="runtime.testing" data-testid="runtime-test"
                    class="rounded border border-slate-300 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                    {{ runtime.testing ? '测试中…' : '测试连接' }}
                  </button>
                  <button @click="rotateSecret" :disabled="runtime.testing"
                    class="rounded border border-slate-300 bg-white px-2.5 py-1 text-slate-700 hover:bg-slate-50 disabled:opacity-50">重新生成签名密钥</button>
                  <span v-if="runtime.info.endpoint.last_test_at" :class="runtime.info.endpoint.last_test_ok ? 'text-emerald-700' : 'text-red-600'">
                    {{ runtime.info.endpoint.last_test_ok ? '上次测试通过' : '上次测试失败' }}：{{ runtime.info.endpoint.last_test_message }}
                  </span>
                  <span v-else class="text-slate-400">还没有测试过连接</span>
                </div>
              </div>
            </div>
          </section>

          <!-- ========== 助手设定 ========== -->
          <section v-show="section === 'persona'" class="space-y-4">
            <p class="text-xs text-slate-500">这四段合起来就是它的“工作说明书”。写得越具体，回答越稳定。</p>
            <div v-for="field in PERSONA_FIELDS" :key="field.key">
              <label :for="`persona-${field.key}`" class="mb-1 flex items-baseline justify-between text-xs text-slate-500">
                <span class="font-medium text-slate-700">{{ field.label }}</span>
                <span class="text-slate-400">{{ (form[field.key] || '').length }} 字</span>
              </label>
              <textarea :id="`persona-${field.key}`" v-model="form[field.key]" :rows="field.rows" :placeholder="field.placeholder"
                class="w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500"></textarea>
              <p class="mt-1 text-[11px] text-slate-400">{{ field.hint }}</p>
            </div>
          </section>

          <!-- ========== 模型与记忆 ========== -->
          <section v-show="section === 'model'" class="space-y-5">
            <div>
              <label for="agent-model" class="mb-1 block text-xs text-slate-500">模型</label>
              <select id="agent-model" v-model="form.model_name" data-testid="agent-model"
                class="h-9 w-full rounded border border-slate-300 px-3 text-sm outline-none focus:border-indigo-500">
                <option v-for="m in options?.models || []" :key="m" :value="m">
                  {{ modelDisplayName(m) }}{{ (options?.connected_models || []).includes(m) ? '' : '（还没连接 API Key）' }}
                </option>
              </select>
              <p class="mt-1 text-[11px] text-slate-400">
                模型的 API Key 由管理员在左侧「模型连接」里统一配置一次，全公司共用；没连接的模型，使用者要自己填密钥才能用。
              </p>
            </div>
            <div>
              <label for="agent-temperature" class="mb-1 flex items-baseline justify-between text-xs text-slate-500">
                <span>回答风格</span><span class="text-slate-700">{{ temperatureLabel }}（{{ form.temperature }}）</span>
              </label>
              <input id="agent-temperature" v-model.number="form.temperature" type="range" min="0" max="100" step="5" class="w-full" data-testid="agent-temperature" />
              <div class="flex justify-between text-[11px] text-slate-400"><span>严谨、稳定</span><span>均衡</span><span>灵活、发散</span></div>
              <p class="mt-1 text-[11px] text-slate-400">制度、财务、合同这类需要准确的场景建议调低；创意、文案类可以调高。</p>
            </div>
            <label class="flex items-start justify-between gap-3 rounded border border-slate-200 px-3 py-2.5">
              <span>
                <span class="block text-sm font-medium text-slate-800">长期记忆</span>
                <span class="block text-xs text-slate-500">记住每位使用者的偏好和常用信息，下次对话自动带上。涉及敏感业务的助手建议关闭。</span>
              </span>
              <input v-model="form.memory_enabled" type="checkbox" class="mt-1 h-4 w-4 shrink-0" data-testid="agent-memory" />
            </label>
          </section>

          <!-- ========== 知识库 ========== -->
          <section v-show="section === 'knowledge'" class="space-y-4">
            <label class="flex items-start justify-between gap-3 rounded border border-slate-200 px-3 py-2.5">
              <span>
                <span class="block text-sm font-medium text-slate-800">回答前先检索知识库</span>
                <span class="block text-xs text-slate-500">打开后，助手会先从下面绑定的资料里找依据，再回答。</span>
              </span>
              <input v-model="form.rag_enabled" type="checkbox" class="mt-1 h-4 w-4 shrink-0" data-testid="agent-rag" />
            </label>

            <div :class="form.rag_enabled ? '' : 'pointer-events-none opacity-50'">
              <div class="mb-1.5 flex items-center justify-between">
                <p class="text-sm font-medium text-slate-800">绑定的知识库</p>
                <span class="text-xs text-slate-400">已选 {{ form.space_ids.length }} 个</span>
              </div>
              <p class="mb-2 text-xs text-slate-500">使用者要能读到这些资料，助手才检索得到。资料没划分给使用者所在的部门，会标红提示。</p>
              <div class="max-h-60 divide-y divide-slate-100 overflow-y-auto rounded border border-slate-200" data-testid="agent-space-list">
                <label v-for="s in options?.spaces || []" :key="s.id" class="flex cursor-pointer items-start gap-3 px-3 py-2 text-sm hover:bg-slate-50">
                  <input type="checkbox" :value="s.id" v-model="form.space_ids" class="mt-1 h-4 w-4" />
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-slate-800">{{ s.name }}</span>
                    <span class="mt-0.5 flex flex-wrap items-center gap-1 text-[11px]">
                      <span v-if="s.scope_type === 'enterprise'" class="rounded bg-purple-50 px-1.5 py-0.5 text-purple-700">全企业</span>
                      <template v-else-if="s.scope_type === 'department'">
                        <span v-for="d in s.departments" :key="d.id" class="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-700">{{ d.name }}</span>
                      </template>
                      <span v-else class="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">未划分</span>
                      <span v-if="s.sensitivity === 'restricted'" class="rounded bg-red-50 px-1.5 py-0.5 text-red-700">绝密</span>
                      <span v-else-if="s.sensitivity === 'confidential'" class="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">机密</span>
                      <span class="text-slate-400">{{ s.doc_count }} 份文档</span>
                      <span v-if="form.space_ids.includes(s.id) && unreadable(s)" class="rounded bg-red-50 px-1.5 py-0.5 text-red-600" data-testid="agent-space-unreadable">使用者读不到</span>
                    </span>
                  </span>
                </label>
                <p v-if="!(options?.spaces || []).length" class="px-3 py-6 text-center text-xs text-slate-400">还没有知识库，先去「企业知识库」新建。</p>
              </div>
            </div>

            <div class="grid gap-4 sm:grid-cols-2" :class="form.rag_enabled ? '' : 'pointer-events-none opacity-50'">
              <div class="sm:col-span-2">
                <label for="agent-topk" class="mb-1 flex items-baseline justify-between text-xs text-slate-500">
                  <span>每次参考几段资料</span><span class="text-slate-700">{{ form.kb_top_k }} 段</span>
                </label>
                <input id="agent-topk" v-model.number="form.kb_top_k" type="range" min="1" max="20" step="1" class="w-full" />
                <p class="text-[11px] text-slate-400">越多越全面，但回答更慢、更容易夹杂不相关的内容。一般 4～8 段。</p>
              </div>
              <label class="flex items-start justify-between gap-3 rounded border border-slate-200 px-3 py-2">
                <span><span class="block text-sm text-slate-800">检索结果重新排序</span><span class="block text-xs text-slate-500">更准，但每次多花一点时间。</span></span>
                <input v-model="form.kb_rerank_enabled" type="checkbox" class="mt-1 h-4 w-4 shrink-0" />
              </label>
              <label class="flex items-start justify-between gap-3 rounded border border-slate-200 px-3 py-2">
                <span><span class="block text-sm text-slate-800">回答必须标注来源</span><span class="block text-xs text-slate-500">方便使用者核对依据。</span></span>
                <input v-model="form.kb_force_citation" type="checkbox" class="mt-1 h-4 w-4 shrink-0" />
              </label>
              <label class="flex items-start justify-between gap-3 rounded border border-slate-200 px-3 py-2 sm:col-span-2">
                <span><span class="block text-sm text-slate-800">资料里没有依据时，不硬答</span><span class="block text-xs text-slate-500">明确告诉使用者“资料里没找到”，而不是凭空编一个。</span></span>
                <input v-model="form.kb_refuse_when_empty" type="checkbox" class="mt-1 h-4 w-4 shrink-0" />
              </label>
            </div>
          </section>

          <!-- ========== 技能 ========== -->
          <section v-show="section === 'skills'" class="space-y-3">
            <div class="flex items-center justify-between">
              <p class="text-sm font-medium text-slate-800">给它添加能力</p>
              <span class="text-xs text-slate-400">已选 {{ form.skill_ids.length }} 个</span>
            </div>
            <p class="text-xs text-slate-500">技能决定它能调用哪些工具、遵循哪些固定流程。只列出已发布的技能和你自己的草稿技能。</p>
            <div class="max-h-80 divide-y divide-slate-100 overflow-y-auto rounded border border-slate-200" data-testid="agent-skill-list">
              <label v-for="s in options?.skills || []" :key="s.id" class="flex cursor-pointer items-start gap-3 px-3 py-2 text-sm hover:bg-slate-50">
                <input type="checkbox" :value="s.id" v-model="form.skill_ids" class="mt-1 h-4 w-4" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-slate-800">{{ s.name }}</span>
                  <span v-if="s.description" class="block truncate text-xs text-slate-500">{{ s.description }}</span>
                </span>
                <span v-if="s.lifecycle_status !== 'published'" class="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">草稿</span>
              </label>
              <p v-if="!(options?.skills || []).length" class="px-3 py-6 text-center text-xs text-slate-400">还没有可添加的技能，先去「技能管理」创建并发布。</p>
            </div>
          </section>

          <!-- ========== 发布与试运行 ========== -->
          <section v-show="section === 'publish'" class="space-y-5">
            <div>
              <p class="mb-2 text-sm font-medium text-slate-800">发布前检查</p>
              <ul class="divide-y divide-slate-100 rounded border border-slate-200" data-testid="agent-readiness">
                <li v-for="item in detail?.readiness.items || []" :key="item.key" class="flex items-start gap-2.5 px-3 py-2 text-sm">
                  <CheckCircle2 v-if="item.level === 'ok'" :size="16" class="mt-0.5 shrink-0 text-emerald-500" />
                  <AlertTriangle v-else :size="16" class="mt-0.5 shrink-0" :class="item.level === 'error' ? 'text-red-500' : 'text-amber-500'" />
                  <span class="min-w-0"><span class="font-medium text-slate-800">{{ item.label }}</span>
                    <span class="ml-2 text-xs" :class="item.level === 'error' ? 'text-red-600' : item.level === 'warn' ? 'text-amber-700' : 'text-slate-500'">{{ item.message }}</span></span>
                </li>
              </ul>
              <div class="mt-3 flex items-center justify-between gap-3">
                <p class="text-xs" :class="detail?.readiness.ready ? 'text-emerald-700' : 'text-red-600'">
                  {{ detail?.readiness.ready ? '没有阻止发布的问题。' : '还有必须处理的问题，处理完才能发布。' }}
                </p>
                <button v-if="detail && detail.lifecycle_status !== 'published'" @click="publish" :disabled="!detail.readiness.ready || saving" data-testid="agent-publish"
                  class="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-40">发布</button>
                <span v-else-if="detail" class="rounded bg-emerald-50 px-2 py-1 text-xs text-emerald-700">已发布</span>
              </div>
            </div>

            <div>
              <p class="mb-1 text-sm font-medium text-slate-800">试运行</p>
              <p class="mb-2 text-xs text-slate-500">用你自己的账号和这份配置真实对话一次，看回答符不符合预期。会调用真实模型（需要你已配置好对应模型的密钥），并产生用量。记得先保存再试。</p>
              <div v-if="currentId" class="overflow-hidden rounded border border-slate-200">
                <EmbeddedAgentChatPanel :agent-id="currentId" />
              </div>
              <p v-else class="rounded border border-dashed border-slate-200 px-3 py-6 text-center text-xs text-slate-400">先保存创建，才能试运行。</p>
            </div>
          </section>
        </div>
      </div>

      <footer v-if="!runtime.secret && !loading" class="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
        <p class="min-w-0 truncate text-xs text-red-600" role="alert">{{ error }}</p>
        <div class="flex shrink-0 items-center gap-2">
          <button @click="requestClose" class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">关闭</button>
          <button @click="save" :disabled="saving || !form.name.trim()" data-testid="agent-save"
            class="rounded bg-indigo-600 px-4 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">
            {{ saving ? '保存中…' : currentId ? '保存' : '创建' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertTriangle, CheckCircle2, X } from 'lucide-vue-next'
import EmbeddedAgentChatPanel from '../EmbeddedAgentChatPanel.vue'
import * as orgApi from '../../api/organizationAdmin'
import type { AgentConfig, AgentOptions, AgentTemplate, ManagedAgentDetail, ManagedAgentType } from '../../api/organizationAdmin'
import { modelDisplayName } from '../../utils/displayNames'
import { getErrorMessage } from '../../utils/request'
import { toastSuccess } from '../../utils/toast'

const props = defineProps<{ agentId: number | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved'): void }>()

const DIRECTIONS: Record<string, string> = { hr: '人事', procurement: '采购', sales: '销售', finance: '财务', it: 'IT' }

type PersonaKey = 'role' | 'task' | 'constraints' | 'output'
const PERSONA_FIELDS: { key: PersonaKey; label: string; rows: number; placeholder: string; hint: string }[] = [
  { key: 'role', label: '角色设定', rows: 3, placeholder: '例如：你是公司的销售助理，熟悉报价流程和客户跟进规范。', hint: '它是谁、站在什么立场、说话什么口吻。' },
  { key: 'task', label: '任务说明', rows: 3, placeholder: '例如：帮销售同事查询客户资料、整理跟进记录、起草报价说明。', hint: '它要帮使用者完成哪些事，边界在哪里。' },
  { key: 'constraints', label: '约束', rows: 3, placeholder: '例如：不得透露其他客户的信息；金额必须以系统数据为准，不能估算。', hint: '哪些事不能做、必须遵守的规则。' },
  { key: 'output', label: '输出格式', rows: 2, placeholder: '例如：先给结论，再列依据；金额用表格。', hint: '回答的结构和格式，让不同使用者看到一致的结果。' },
]

type Section = 'basic' | 'persona' | 'model' | 'knowledge' | 'skills' | 'publish'
const section = ref<Section>('basic')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const currentId = ref<number | null>(props.agentId)
const detail = ref<ManagedAgentDetail | null>(null)
const options = ref<AgentOptions | null>(null)
const templates = ref<AgentTemplate[]>([])

const form = reactive({
  name: '',
  agent_type: 'department' as ManagedAgentType,
  department_code: '',
  template_id: '',
  model_name: 'glm-4',
  role: '', task: '', constraints: '', output: '',
  temperature: 70,
  memory_enabled: true,
  rag_enabled: false,
  kb_top_k: 5,
  kb_rerank_enabled: false,
  kb_force_citation: true,
  kb_refuse_when_empty: true,
  space_ids: [] as number[],
  skill_ids: [] as number[],
})

// 运行方式：平台自带 / 接入外部智能体服务（签名密钥只在生成那一刻返回一次）
const runtime = reactive({
  type: 'builtin' as 'builtin' | 'external',
  url: '', timeout: 60, sendKnowledge: false, headersText: '',
  info: null as orgApi.AgentRuntimeInfo | null,
  secret: null as string | null, copied: false, testing: false,
})

const isExternal = computed(() => runtime.type === 'external')

const templatesForType = computed(() => templates.value.filter((t) => t.agent_type === form.agent_type))
const chosenTemplate = computed(() => templates.value.find((t) => t.id === form.template_id) || null)

const temperatureLabel = computed(() => (form.temperature <= 30 ? '严谨' : form.temperature >= 70 ? '灵活' : '均衡'))

const sections = computed(() => {
  const gaps = detail.value?.knowledge_gaps?.length || 0
  const errors = detail.value?.readiness.items.filter((i) => i.level === 'error').length || 0
  const external = isExternal.value
  return [
    { key: 'basic' as Section, label: '基本信息', disabled: false },
    { key: 'persona' as Section, label: '助手设定', disabled: external },
    { key: 'model' as Section, label: '模型与记忆', disabled: external },
    { key: 'knowledge' as Section, label: '知识库', disabled: external, badge: gaps ? `${gaps}` : form.rag_enabled ? `${form.space_ids.length}` : '', badgeClass: gaps ? 'bg-red-50 text-red-600' : 'bg-slate-100 text-slate-500' },
    { key: 'skills' as Section, label: '技能', disabled: external, badge: form.skill_ids.length ? `${form.skill_ids.length}` : '', badgeClass: 'bg-slate-100 text-slate-500' },
    { key: 'publish' as Section, label: '发布与试运行', disabled: !currentId.value, badge: errors ? `${errors}` : '', badgeClass: 'bg-red-50 text-red-600' },
  ]
})

/** 这份资料，这个智能体的使用者读得到吗？（和后端“划分”的规则一致，未划分的智能体还没有使用者，不判断） */
const unreadable = (space: AgentOptions['spaces'][number]) => {
  const d = detail.value
  if (!d || d.assignment === 'unassigned') return false
  if (space.sensitivity === 'restricted') return true
  if (space.scope_type === 'enterprise') return false
  if (d.assignment === 'enterprise') return true
  return !(space.scope_type === 'department' && space.departments.some((x) => x.id === d.team_id))
}

const applyTemplate = (id: string) => {
  form.template_id = id
  const tpl = templates.value.find((t) => t.id === id)
  if (!tpl) return
  if (tpl.department_code !== undefined && tpl.department_code !== null) form.department_code = tpl.department_code
  form.role = tpl.role
  form.task = tpl.task
}

const parseHeaders = (text: string): Record<string, string> | undefined => {
  const lines = text.split('\n').map((line) => line.trim()).filter(Boolean)
  if (!lines.length) return undefined
  const headers: Record<string, string> = {}
  for (const line of lines) {
    const index = line.indexOf(':')
    if (index <= 0) throw new Error(`请求头格式不对：${line.slice(0, 30)}（应为 名称: 值）`)
    headers[line.slice(0, index).trim()] = line.slice(index + 1).trim()
  }
  return headers
}

const configPayload = (): AgentConfig => ({
  temperature: form.temperature,
  memory_enabled: form.memory_enabled ? 1 : 0,
  rag_enabled: form.rag_enabled ? 1 : 0,
  kb_top_k: form.kb_top_k,
  kb_rerank_enabled: form.kb_rerank_enabled ? 1 : 0,
  kb_force_citation: form.kb_force_citation ? 1 : 0,
  kb_refuse_when_empty: form.kb_refuse_when_empty ? 1 : 0,
})

const fillFrom = (d: ManagedAgentDetail) => {
  detail.value = d
  form.name = d.name
  form.agent_type = d.agent_type
  form.department_code = d.department_code || ''
  form.model_name = d.model_name
  form.role = d.prompt?.role || ''
  form.task = d.prompt?.task || ''
  form.constraints = d.prompt?.constraints || ''
  form.output = d.prompt?.output || ''
  form.temperature = d.config.temperature
  form.memory_enabled = !!d.config.memory_enabled
  form.rag_enabled = !!d.config.rag_enabled
  form.kb_top_k = d.config.kb_top_k
  form.kb_rerank_enabled = !!d.config.kb_rerank_enabled
  form.kb_force_citation = !!d.config.kb_force_citation
  form.kb_refuse_when_empty = !!d.config.kb_refuse_when_empty
  form.space_ids = [...d.space_ids]
  form.skill_ids = [...d.skill_ids]
}

const loadRuntime = async (id: number) => {
  const info = await orgApi.getAgentRuntime(id)
  runtime.info = info
  runtime.type = info.runtime_type
  if (info.endpoint) {
    runtime.url = info.endpoint.url
    runtime.timeout = info.endpoint.timeout_seconds
    runtime.sendKnowledge = info.endpoint.send_knowledge
  }
}

const reload = async (id: number) => {
  fillFrom(await orgApi.getManagedAgentDetail(id))
  await loadRuntime(id)
}

onMounted(async () => {
  loading.value = true
  try {
    const [opts, tpls] = await Promise.all([orgApi.getAgentOptions(props.agentId ?? undefined), orgApi.getAgentTemplates()])
    options.value = opts
    templates.value = tpls
    if (props.agentId) await reload(props.agentId)
    else if (opts.models.length && !opts.models.includes(form.model_name)) form.model_name = opts.models[0]
  } catch (e: any) {
    error.value = getErrorMessage(e, '加载失败')
  } finally {
    loading.value = false
  }
})

const saveRuntime = async (id: number, rotate = false): Promise<string | null> => {
  const info = await orgApi.setAgentRuntime(id, isExternal.value
    ? {
      runtime_type: 'external', url: runtime.url.trim(), timeout_seconds: runtime.timeout,
      send_knowledge: runtime.sendKnowledge, headers: parseHeaders(runtime.headersText), rotate_secret: rotate,
    }
    : { runtime_type: 'builtin' })
  runtime.info = info
  runtime.headersText = ''
  return info.secret || null
}

const save = async () => {
  saving.value = true
  error.value = ''
  try {
    if (isExternal.value && !runtime.url.trim()) {
      section.value = 'basic'
      error.value = '请填写外部服务地址'
      return
    }
    let secret: string | null = null
    const builtin = !isExternal.value
    const persona = { role: form.role, task: form.task, constraints: form.constraints, output: form.output }
    if (currentId.value) {
      await orgApi.updateManagedAgent(currentId.value, {
        name: form.name.trim(),
        ...(builtin ? {
          model_name: form.model_name, ...persona, config: configPayload(),
          space_ids: form.space_ids, skill_ids: form.skill_ids,
        } : {}),
        expected_row_version: detail.value?.row_version,
      })
      if (isExternal.value || detail.value?.runtime_type === 'external') secret = await saveRuntime(currentId.value)
    } else {
      const created = await orgApi.createManagedAgent({
        name: form.name.trim(),
        agent_type: form.agent_type,
        department_code: form.agent_type === 'department' && form.department_code ? form.department_code : undefined,
        template_id: form.template_id || undefined,
        model_name: form.model_name,
        ...persona,
        config: configPayload(),
        space_ids: form.space_ids,
        skill_ids: form.skill_ids,
      })
      currentId.value = created.id          // 已经建好：之后的保存都是编辑，不会重复创建
      emit('saved')
      if (isExternal.value) secret = await saveRuntime(created.id)
    }
    await reload(currentId.value!)
    emit('saved')
    if (secret) {
      runtime.secret = secret              // 保持打开，先让管理员复制密钥
      return
    }
    toastSuccess('已保存')
    if (section.value === 'basic' && !props.agentId) section.value = 'publish'
  } catch (e: any) {
    error.value = e?.message && !e?.response ? e.message : getErrorMessage(e, '保存失败')
  } finally {
    saving.value = false
  }
}

const publish = async () => {
  if (!currentId.value || !detail.value) return
  saving.value = true
  error.value = ''
  try {
    await orgApi.updateManagedAgent(currentId.value, { lifecycle_status: 'published', expected_row_version: detail.value.row_version })
    await reload(currentId.value)
    emit('saved')
    toastSuccess('已发布')
  } catch (e: any) {
    error.value = getErrorMessage(e, '发布失败')
  } finally {
    saving.value = false
  }
}

const runConnectionTest = async () => {
  if (!currentId.value) return
  runtime.testing = true
  error.value = ''
  try {
    await saveRuntime(currentId.value)          // 先保存当前填写的地址，再测
    runtime.info = await orgApi.testAgentRuntime(currentId.value)
    await reload(currentId.value)
  } catch (e: any) {
    error.value = e?.message && !e?.response ? e.message : getErrorMessage(e, '测试失败')
  } finally {
    runtime.testing = false
  }
}

const rotateSecret = async () => {
  if (!currentId.value || !confirm('重新生成后，旧密钥立即失效，你的服务需要换成新密钥。继续吗？')) return
  error.value = ''
  try {
    runtime.secret = await saveRuntime(currentId.value, true)
    await reload(currentId.value)
  } catch (e: any) {
    error.value = e?.message && !e?.response ? e.message : getErrorMessage(e, '重新生成失败')
  }
}

const copySecret = async () => {
  try {
    await navigator.clipboard.writeText(runtime.secret || '')
    runtime.copied = true
  } catch {
    runtime.copied = false
  }
}

const closeSecretPanel = () => {
  runtime.secret = null
  runtime.copied = false
}

const requestClose = () => emit('close')
</script>
