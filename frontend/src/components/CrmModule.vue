<template>
  <div class="space-y-4">
  <CrmInbox :team-id="teamId" :customers="customers" @changed="onActivitiesChanged" />
  <section class="rounded-lg border border-slate-200 bg-white">
    <div class="border-b border-slate-200 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-900">部门客户</h2>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-[220px_1fr]">
      <ul class="divide-y divide-slate-100 border-b border-slate-100 sm:border-b-0 sm:border-r sm:border-slate-100">
        <li
          v-for="c in customers"
          :key="c.id"
          @click="selectCustomer(c.id)"
          class="cursor-pointer px-4 py-3 text-sm hover:bg-slate-50"
          :class="selectedCustomerId === c.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-slate-700'"
        >
          {{ c.name }}
          <span class="block text-xs text-slate-400">{{ c.industry || '行业未知' }}</span>
        </li>
        <li v-if="!customers.length" class="px-4 py-8 text-center text-sm text-slate-400">本部门还没有客户数据</li>
      </ul>

      <div class="p-4">
        <div v-if="!selectedCustomerId" class="py-12 text-center text-sm text-slate-400">
          从左侧选择一个客户查看详情
        </div>
        <div v-else-if="summary" class="space-y-4">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h3 class="text-sm font-semibold text-slate-900">{{ summary.name }}</h3>
              <p class="text-xs text-slate-400">{{ summary.industry || '行业未知' }}</p>
            </div>
            <button v-if="askAgent" data-testid="customer-ask-agent" class="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
              @click="askAgent(`分析客户「${summary.name}」（客户 ID ${summary.id}）：最近的跟进和商机进展怎么样，下一步该怎么跟进？`)"
            >让助手分析</button>
          </div>

          <div>
            <h4 class="mb-1.5 text-xs font-semibold text-slate-500">联系人</h4>
            <ul class="space-y-1">
              <li v-for="(c, i) in summary.contacts" :key="i" class="text-xs text-slate-600">
                {{ c.name }}<span v-if="c.title"> · {{ c.title }}</span><span v-if="c.phone"> · {{ c.phone }}</span>
              </li>
              <li v-if="!summary.contacts.length" class="text-xs text-slate-400">暂无联系人</li>
            </ul>
          </div>

          <div>
            <div class="mb-1.5 flex items-center justify-between">
              <h4 class="text-xs font-semibold text-slate-500">最近跟进</h4>
              <button
                @click="followupForm.visible = true"
                class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2 py-1 text-xs text-white hover:bg-indigo-700"
              >
                <Plus :size="12" /> 新建跟进
              </button>
            </div>
            <div v-if="followupForm.visible" class="mb-2 space-y-1.5 rounded border border-slate-100 bg-slate-50 p-2">
              <textarea
                v-model="followupForm.content"
                placeholder="跟进内容"
                rows="2"
                class="w-full rounded border border-slate-200 px-2 py-1.5 text-xs"
              />
              <div class="flex gap-1.5">
                <button
                  @click="submitFollowup"
                  :disabled="followupForm.submitting"
                  class="rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
                >保存草稿</button>
                <button @click="followupForm.visible = false" class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-600">取消</button>
              </div>
            </div>
            <ul class="space-y-1.5">
              <li v-for="f in summary.recentFollowUps" :key="f.id" class="flex items-start justify-between gap-2 text-xs">
                <span class="text-slate-600">{{ f.content }}</span>
                <span v-if="f.status === 'CONFIRMED'" class="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">已确认</span>
                <button
                  v-else
                  @click="doConfirmFollowup(f.id)"
                  class="shrink-0 rounded border border-indigo-200 px-1.5 py-0.5 text-indigo-700 hover:bg-indigo-50"
                >确认</button>
              </li>
              <li v-if="!summary.recentFollowUps.length" class="text-xs text-slate-400">暂无跟进记录</li>
            </ul>
          </div>

          <div>
            <div class="mb-1.5 flex items-center justify-between">
              <h4 class="text-xs font-semibold text-slate-500">商机</h4>
              <button
                @click="openOpportunityForm(null)"
                class="inline-flex items-center gap-1 rounded bg-indigo-600 px-2 py-1 text-xs text-white hover:bg-indigo-700"
              >
                <Plus :size="12" /> 新建商机
              </button>
            </div>
            <div v-if="opportunityForm.visible" class="mb-2 space-y-1.5 rounded border border-slate-100 bg-slate-50 p-2">
              <div class="grid grid-cols-2 gap-1.5">
                <select v-model="opportunityForm.stage" aria-label="阶段" class="rounded border border-slate-200 px-2 py-1.5 text-xs">
                  <option v-for="s in STAGE_OPTIONS" :key="s" :value="s">{{ STAGE_LABELS[s] }}</option>
                </select>
                <input v-model.number="opportunityForm.amount" type="number" min="0" placeholder="金额" aria-label="金额" class="rounded border border-slate-200 px-2 py-1.5 text-xs" />
                <input v-model="opportunityForm.expectedCloseDate" type="date" aria-label="预计成交日期" title="预计成交日期" class="rounded border border-slate-200 px-2 py-1.5 text-xs" />
                <input v-model="opportunityForm.nextStep" maxlength="500" placeholder="下一步（例：周三给客户演示）" aria-label="下一步" class="rounded border border-slate-200 px-2 py-1.5 text-xs" />
              </div>
              <div class="flex gap-1.5">
                <button
                  @click="submitOpportunity"
                  :disabled="opportunityForm.submitting"
                  class="rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
                >{{ opportunityForm.opportunityId ? '保存更新' : '创建' }}</button>
                <button @click="opportunityForm.visible = false" class="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-600">取消</button>
              </div>
            </div>
            <ul class="space-y-1.5">
              <li
                v-for="o in summary.opportunities" :key="o.id"
                @click="openOpportunityForm(o)"
                class="flex items-center justify-between text-xs cursor-pointer hover:text-indigo-700"
              >
                <span>{{ STAGE_LABELS[o.stage] || o.stage }} · ¥{{ o.amount.toFixed(2) }}<template v-if="o.expectedCloseDate"> · 预计 {{ o.expectedCloseDate }} 成交</template><template v-if="o.nextStep"> · 下一步：{{ o.nextStep }}</template></span>
                <span class="text-slate-400">点击编辑</span>
              </li>
              <li v-if="!summary.opportunities.length" class="text-xs text-slate-400">暂无商机</li>
            </ul>
          </div>

          <CustomerCopilot ref="copilot" :team-id="teamId" :customer-id="summary.id" />
        </div>
      </div>
    </div>
  </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import * as crmApi from '../api/departmentCrm'
import type { CustomerDto, CustomerSummaryDto, OpportunityDto } from '../api/departmentCrm'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'
import { useFocusRecord, useAskDeptAgent } from './department/askAgent'
import CrmInbox from './department/CrmInbox.vue'
import CustomerCopilot from './department/CustomerCopilot.vue'

const props = defineProps<{ teamId: number }>()
const askAgent = useAskDeptAgent()

const customers = ref<CustomerDto[]>([])
const selectedCustomerId = ref<number | null>(null)
const summary = ref<CustomerSummaryDto | null>(null)

const followupForm = reactive({ visible: false, submitting: false, content: '' })

const STAGE_OPTIONS = ['LEAD', 'QUALIFIED', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST']
const STAGE_LABELS: Record<string, string> = {
  LEAD: '线索', QUALIFIED: '已确认需求', PROPOSAL: '方案报价',
  NEGOTIATION: '谈判中', WON: '已成交', LOST: '已流失',
}
const opportunityForm = reactive({
  visible: false, submitting: false,
  opportunityId: null as number | null, stage: 'LEAD', amount: 0, expectedCloseDate: '', nextStep: '',
})
const copilot = ref<InstanceType<typeof CustomerCopilot> | null>(null)

function onActivitiesChanged() {
  copilot.value?.load()
}

async function loadCustomers() {
  try {
    customers.value = await crmApi.getTeamCustomers(props.teamId)
  } catch (e) {
    toastError(getErrorMessage(e, '加载客户列表失败'))
  }
}

async function loadSummary() {
  if (selectedCustomerId.value == null) return
  try {
    summary.value = await crmApi.getCustomerSummary(props.teamId, selectedCustomerId.value)
  } catch (e) {
    toastError(getErrorMessage(e, '加载客户详情失败'))
  }
}

function selectCustomer(id: number) {
  selectedCustomerId.value = id
  followupForm.visible = false
  opportunityForm.visible = false
  loadSummary()
}

async function submitFollowup() {
  if (!followupForm.content.trim() || selectedCustomerId.value == null) {
    toastError('请填写跟进内容')
    return
  }
  followupForm.submitting = true
  try {
    await crmApi.createFollowupDraft(props.teamId, selectedCustomerId.value, followupForm.content)
    toastSuccess('已保存跟进草稿')
    followupForm.visible = false
    followupForm.content = ''
    await loadSummary()
  } catch (e) {
    toastError(getErrorMessage(e, '保存失败'))
  } finally {
    followupForm.submitting = false
  }
}

async function doConfirmFollowup(followupId: number) {
  try {
    await crmApi.confirmFollowup(followupId)
    toastSuccess('已确认跟进')
    await loadSummary()
  } catch (e) {
    toastError(getErrorMessage(e, '确认失败'))
  }
}

function openOpportunityForm(existing: OpportunityDto | null) {
  opportunityForm.visible = true
  opportunityForm.opportunityId = existing?.id ?? null
  opportunityForm.stage = existing?.stage ?? 'LEAD'
  opportunityForm.amount = existing?.amount ?? 0
  opportunityForm.expectedCloseDate = existing?.expectedCloseDate ?? ''
  opportunityForm.nextStep = existing?.nextStep ?? ''
}

async function submitOpportunity() {
  if (selectedCustomerId.value == null || opportunityForm.amount <= 0) {
    toastError('请填写有效的商机金额')
    return
  }
  opportunityForm.submitting = true
  try {
    await crmApi.upsertOpportunity(props.teamId, selectedCustomerId.value, {
      opportunityId: opportunityForm.opportunityId ?? undefined,
      stage: opportunityForm.stage,
      amount: opportunityForm.amount,
      expectedCloseDate: opportunityForm.expectedCloseDate || undefined,
      nextStep: opportunityForm.nextStep,
    })
    toastSuccess(opportunityForm.opportunityId ? '已更新商机' : '已创建商机')
    opportunityForm.visible = false
    await loadSummary()
    copilot.value?.load()   // 阶段变化进时间线
  } catch (e) {
    toastError(getErrorMessage(e, '保存失败'))
  } finally {
    opportunityForm.submitting = false
  }
}

watch(() => props.teamId, () => {
  selectedCustomerId.value = null
  summary.value = null
  loadCustomers()
})

onMounted(() => loadCustomers())

// 助手回复里的卡片 → 打开这条记录
useFocusRecord(['customer'], (id) => selectCustomer(id))
</script>
