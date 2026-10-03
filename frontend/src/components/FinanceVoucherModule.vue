<template>
  <section class="rounded-lg border border-slate-200 bg-white" data-testid="voucher-module">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-900">记账凭证</h2>
        <p class="mt-0.5 text-xs text-slate-400">报销批准后系统按规则自动生成凭证草稿，核对科目与风险后由财务人员确认入账。</p>
      </div>
      <div class="flex items-center gap-1 text-xs">
        <button v-for="tab in TABS" :key="tab.value" @click="setTab(tab.value)"
          class="rounded px-2.5 py-1" :class="activeTab === tab.value ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50'"
          :data-testid="`voucher-tab-${tab.value}`">{{ tab.label }}</button>
      </div>
    </div>

    <!-- 已批准但没有凭证的报销单 -->
    <div v-if="unbooked.length" class="border-b border-amber-100 bg-amber-50 px-4 py-3" data-testid="voucher-unbooked">
      <p class="text-xs font-medium text-amber-800">有 {{ unbooked.length }} 张已批准的报销单还没有凭证</p>
      <ul class="mt-1.5 space-y-1">
        <li v-for="c in unbooked" :key="c.id" class="flex items-center justify-between text-xs text-amber-900">
          <span>报销单 #{{ c.id }} · {{ c.teamName || `部门 #${c.teamId}` }} · {{ c.applicantName || `#${c.applicantUserId}` }} · {{ money(c.totalAmount) }}</span>
          <button @click="generate(c.id)" :disabled="busy" class="rounded border border-amber-300 bg-white px-2 py-0.5 hover:bg-amber-100 disabled:opacity-50">生成凭证草稿</button>
        </li>
      </ul>
    </div>

    <!-- 月度汇总 -->
    <div v-if="activeTab === 'summary'" class="space-y-3 px-4 py-4" data-testid="voucher-summary">
      <div class="flex items-center gap-2 text-sm">
        <label for="voucher-period" class="text-slate-500">期间</label>
        <input id="voucher-period" v-model="period" type="month" @change="loadSummary" class="rounded border border-slate-200 px-2 py-1 text-sm" />
      </div>
      <template v-if="summary">
        <p class="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="voucher-narrative">{{ summary.narrative }}</p>
        <div class="grid grid-cols-3 gap-2 text-center text-xs">
          <div v-for="s in STATUS_ORDER" :key="s" class="rounded border border-slate-200 px-2 py-2">
            <p class="text-slate-400">{{ STATUS_LABELS[s] }}</p>
            <p class="mt-0.5 text-base font-semibold text-slate-900">{{ summary.byStatus[s].count }}</p>
            <p class="text-slate-500">{{ money(summary.byStatus[s].amount) }}</p>
          </div>
        </div>
        <p v-if="summary.efficiency.postedCount" class="text-xs text-slate-500" data-testid="voucher-efficiency">
          自动记账效果：科目建议原样采纳 {{ summary.efficiency.adoptedAsIs }}/{{ summary.efficiency.postedCount }}（{{ summary.efficiency.adoptedRate }}%）<span v-if="summary.efficiency.avgHoursToPost !== null">，生成到入账平均 {{ summary.efficiency.avgHoursToPost }} 小时</span>
        </p>
        <table v-if="summary.bySubject.length" class="w-full text-left text-xs">
          <thead class="text-slate-400"><tr><th class="py-1">科目</th><th class="py-1 text-right">借方</th><th class="py-1 text-right">贷方</th></tr></thead>
          <tbody>
            <tr v-for="r in summary.bySubject" :key="r.code + r.direction" class="border-t border-slate-100">
              <td class="py-1.5">{{ r.code }} {{ r.name }}</td>
              <td class="py-1.5 text-right tabular-nums">{{ r.direction === 'D' ? money(r.amount) : '' }}</td>
              <td class="py-1.5 text-right tabular-nums">{{ r.direction === 'C' ? money(r.amount) : '' }}</td>
            </tr>
            <tr class="border-t border-slate-200 font-medium">
              <td class="py-1.5">合计 <span :class="summary.balanced ? 'text-emerald-600' : 'text-red-600'">{{ summary.balanced ? '借贷平衡' : '借贷不平衡' }}</span></td>
              <td class="py-1.5 text-right tabular-nums">{{ money(summary.debitTotal) }}</td>
              <td class="py-1.5 text-right tabular-nums">{{ money(summary.creditTotal) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-xs text-slate-400">本期还没有已入账的凭证</p>
        <table v-if="summary.byTeam.length" class="w-full text-left text-xs">
          <thead class="text-slate-400"><tr><th class="py-1">部门</th><th class="py-1 text-right">凭证数</th><th class="py-1 text-right">金额</th></tr></thead>
          <tbody>
            <tr v-for="t in summary.byTeam" :key="t.teamId" class="border-t border-slate-100">
              <td class="py-1.5">{{ t.teamName || `部门 #${t.teamId}` }}</td>
              <td class="py-1.5 text-right tabular-nums">{{ t.count }}</td>
              <td class="py-1.5 text-right tabular-nums">{{ money(t.amount) }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>

    <!-- 凭证列表 -->
    <template v-else>
      <ul class="divide-y divide-slate-100" data-testid="voucher-list">
        <li v-for="v in vouchers" :key="v.id">
          <button class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50"
            :class="selected?.id === v.id ? 'bg-indigo-50/50' : ''" @click="select(v.id)" :data-testid="`voucher-row-${v.id}`">
            <div class="min-w-0">
              <p class="truncate text-sm text-slate-900">{{ v.voucherNo || '（未编号）' }} · {{ v.summary }}</p>
              <p class="mt-0.5 text-xs text-slate-400">{{ v.teamName || `部门 #${v.teamId}` }} · 申请人 {{ v.applicantName || `#${v.applicantUserId}` }} · {{ v.voucherDate }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <span class="text-sm tabular-nums text-slate-700">{{ money(v.totalAmount) }}</span>
              <span class="rounded px-2 py-0.5 text-xs" :class="riskBadge(v.riskLevel)">{{ v.riskLabel }}</span>
              <span class="rounded px-2 py-0.5 text-xs" :class="statusBadge(v.status)">{{ STATUS_LABELS[v.status] }}</span>
            </div>
          </button>
        </li>
        <li v-if="!vouchers.length && !loading" class="px-4 py-8 text-center text-sm text-slate-400">
          {{ activeTab === 'DRAFT' ? '没有待核对的凭证' : '没有凭证' }}
        </li>
      </ul>

      <!-- 凭证详情与核对 -->
      <div v-if="selected" class="space-y-4 border-t border-slate-200 bg-slate-50/60 px-4 py-4" data-testid="voucher-detail">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm font-medium text-slate-900">
            {{ selected.voucherNo || '草稿' }} · 报销单 #{{ selected.expenseClaimId }}
            <span class="ml-1 text-xs font-normal text-slate-400">{{ selected.expenseClass === 'SALES' ? '销售费用' : '管理费用' }}</span>
          </p>
          <div v-if="selected.status === 'DRAFT'" class="flex items-center gap-2 text-xs">
            <label :for="`voucher-date-${selected.id}`" class="text-slate-500">凭证日期</label>
            <input :id="`voucher-date-${selected.id}`" type="date" :value="selected.voucherDate" @change="changeDate(($event.target as HTMLInputElement).value)"
              class="rounded border border-slate-200 px-2 py-1" />
          </div>
          <p v-else class="text-xs text-slate-400">{{ selected.voucherDate }}（{{ selected.period }}）</p>
        </div>

        <!-- 风险项：每一项都说明为什么要看 -->
        <ul v-if="selected.risks.length" class="space-y-1" data-testid="voucher-risks">
          <li v-for="r in selected.risks" :key="r.code + r.message" class="flex items-start gap-2 rounded border px-2.5 py-1.5 text-xs" :class="riskItemClass(r.level)">
            <span class="mt-px font-medium">{{ RISK_LEVEL_LABELS[r.level] }}</span><span>{{ r.message }}</span>
          </li>
        </ul>
        <p v-else-if="selected.status === 'DRAFT'" class="rounded border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-700">规则检查没有发现风险项</p>

        <!-- 分录 -->
        <div class="overflow-x-auto">
          <table class="w-full min-w-[640px] text-left text-xs" data-testid="voucher-entries">
            <thead class="text-slate-400">
              <tr><th class="py-1">科目</th><th class="py-1">摘要</th><th class="py-1 text-right">借方</th><th class="py-1 text-right">贷方</th><th class="py-1">依据</th></tr>
            </thead>
            <tbody>
              <tr v-for="e in selected.entries" :key="e.id" class="border-t border-slate-200 align-top">
                <td class="py-2 pr-2">
                  <template v-if="selected.status === 'DRAFT' && e.direction === 'D'">
                    <select :value="e.subjectCode" @change="startChange(e, ($event.target as HTMLSelectElement).value)"
                      class="max-w-[180px] rounded border border-slate-200 px-1.5 py-1" :data-testid="`entry-subject-${e.id}`">
                      <optgroup :label="selected.expenseClass === 'SALES' ? '销售费用科目' : '管理费用科目'">
                        <option v-for="s in sameClassSubjects" :key="s.code" :value="s.code">{{ s.code }} {{ s.name }}</option>
                      </optgroup>
                      <optgroup label="其他费用科目（跨类调整请写明原因）">
                        <option v-for="s in otherClassSubjects" :key="s.code" :value="s.code">{{ s.code }} {{ s.name }}</option>
                      </optgroup>
                    </select>
                  </template>
                  <span v-else>{{ e.subjectCode }} {{ e.subjectName }}</span>
                </td>
                <td class="py-2 pr-2 text-slate-600">{{ e.summary }}</td>
                <td class="py-2 text-right tabular-nums">{{ e.direction === 'D' ? money(e.amount) : '' }}</td>
                <td class="py-2 text-right tabular-nums">{{ e.direction === 'C' ? money(e.amount) : '' }}</td>
                <td class="py-2 pl-3 text-slate-500">
                  <span class="mr-1 rounded px-1.5 py-0.5" :class="confidenceBadge(e.confidence)">{{ CONFIDENCE_LABELS[e.confidence] }}</span>{{ e.basis }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 修改科目需要写明原因（留痕） -->
        <div v-if="pendingChange" class="flex flex-wrap items-center gap-2 rounded border border-indigo-200 bg-white px-3 py-2 text-xs" data-testid="subject-change">
          <span>把分录 {{ pendingChange.entry.lineNo }} 的科目改为 <b>{{ pendingChange.code }}</b>，原因：</span>
          <input v-model="pendingChange.reason" placeholder="例如：实为办公用品采购" class="min-w-[200px] flex-1 rounded border border-slate-200 px-2 py-1" data-testid="subject-reason" />
          <button @click="applyChange" :disabled="busy || pendingChange.reason.trim().length < 2" class="rounded bg-indigo-600 px-2.5 py-1 text-white disabled:opacity-50" data-testid="subject-apply">确认修改</button>
          <button @click="pendingChange = null" class="rounded border border-slate-200 px-2.5 py-1 text-slate-600">取消</button>
        </div>

        <!-- 来源报销明细：核对时对照 -->
        <details class="text-xs text-slate-500">
          <summary class="cursor-pointer select-none">来源报销明细（{{ selected.claimLines.length }} 条）</summary>
          <ul class="mt-1 space-y-0.5 pl-3">
            <li v-for="(l, i) in selected.claimLines" :key="i">{{ CATEGORY_LABELS[l.category] || l.category }} · {{ money(l.amount) }} · {{ l.description || '（无说明）' }} · 发票 {{ l.invoiceNo || '无' }}</li>
          </ul>
        </details>

        <!-- 操作 -->
        <div v-if="selected.status === 'DRAFT'" class="space-y-2 border-t border-slate-200 pt-3">
          <label v-if="warnCount" class="flex items-center gap-2 text-xs text-amber-800">
            <input v-model="acknowledged" type="checkbox" data-testid="voucher-ack" />
            我已逐项核对上面 {{ warnCount }} 项「需核对」的风险
          </label>
          <input v-model="confirmNote" placeholder="入账备注（可选）" class="w-full rounded border border-slate-200 px-2 py-1.5 text-xs" />
          <div class="flex flex-wrap items-center gap-2">
            <button @click="confirm" :disabled="busy || hasBlock || (warnCount > 0 && !acknowledged)"
              class="rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" data-testid="voucher-confirm">确认入账</button>
            <button @click="recheck" :disabled="busy" class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-white">重新检查</button>
            <button @click="regenerate" :disabled="busy" class="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-white" title="丢弃人工修改，按规则重新建议科目">按规则重新生成</button>
            <button @click="voidOpen = !voidOpen" class="rounded border border-red-200 px-3 py-1.5 text-xs text-red-700 hover:bg-red-50">作废</button>
            <span v-if="hasBlock" class="text-xs text-red-600">存在不能入账的问题，处理后重新检查</span>
          </div>
          <div v-if="voidOpen" class="flex items-center gap-2 text-xs">
            <input v-model="voidReason" placeholder="作废原因（必填）" class="flex-1 rounded border border-slate-200 px-2 py-1.5" data-testid="void-reason" />
            <button @click="doVoid" :disabled="busy || voidReason.trim().length < 2" class="rounded bg-red-600 px-3 py-1.5 text-white disabled:opacity-50" data-testid="void-confirm">确认作废</button>
          </div>
        </div>
        <p v-else-if="selected.status === 'POSTED'" class="text-xs text-slate-500">
          已入账（{{ selected.postedAt?.slice(0, 16).replace('T', ' ') }}），确认人 #{{ selected.confirmedBy }}<span v-if="selected.warningsAcknowledged">，已核对风险项</span><span v-if="selected.confirmNote">；备注：{{ selected.confirmNote }}</span>
        </p>
        <p v-else class="text-xs text-red-600">已作废：{{ selected.voidReason }}</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import * as api from '../api/financeVouchers'
import type { AccountSubject, RiskLevel, UnbookedClaim, VoucherDetail, VoucherEntry, VoucherMonthlySummary, VoucherStatus, VoucherSummary } from '../api/financeVouchers'
import { getErrorMessage } from '../utils/request'
import { toastError, toastSuccess } from '../utils/toast'

const props = defineProps<{ teamId: number }>()

const TABS: { value: VoucherStatus | 'summary'; label: string }[] = [
  { value: 'DRAFT', label: '待核对' }, { value: 'POSTED', label: '已入账' }, { value: 'VOID', label: '已作废' }, { value: 'summary', label: '月度汇总' },
]
const STATUS_ORDER: VoucherStatus[] = ['DRAFT', 'POSTED', 'VOID']
const STATUS_LABELS: Record<VoucherStatus, string> = { DRAFT: '待核对', POSTED: '已入账', VOID: '已作废' }
const RISK_LEVEL_LABELS = { INFO: '提示', WARN: '需核对', BLOCK: '不能入账' } as const
const CONFIDENCE_LABELS = { HIGH: '规则确定', MEDIUM: '推断', LOW: '低置信', MANUAL: '人工' } as const
const CATEGORY_LABELS: Record<string, string> = { TRAVEL: '差旅', MEAL: '餐饮', OFFICE_SUPPLY: '办公用品', TRANSPORT: '交通', OTHER: '其他' }

const activeTab = ref<VoucherStatus | 'summary'>('DRAFT')
const vouchers = ref<VoucherSummary[]>([])
const unbooked = ref<UnbookedClaim[]>([])
const subjects = ref<AccountSubject[]>([])
const selected = ref<VoucherDetail | null>(null)
const summary = ref<VoucherMonthlySummary | null>(null)
const period = ref(new Date().toISOString().slice(0, 7))
const loading = ref(false)
const busy = ref(false)
const acknowledged = ref(false)
const confirmNote = ref('')
const voidOpen = ref(false)
const voidReason = ref('')
const pendingChange = ref<{ entry: VoucherEntry; code: string; reason: string } | null>(null)

const expenseSubjects = computed(() => subjects.value.filter((s) => s.category === 'EXPENSE'))
const classPrefix = computed(() => (selected.value?.expenseClass === 'SALES' ? '6601' : '6602'))
const sameClassSubjects = computed(() => expenseSubjects.value.filter((s) => s.code.startsWith(classPrefix.value)))
const otherClassSubjects = computed(() => expenseSubjects.value.filter((s) => !s.code.startsWith(classPrefix.value)))
const warnCount = computed(() => selected.value?.risks.filter((r) => r.level === 'WARN').length ?? 0)
const hasBlock = computed(() => selected.value?.risks.some((r) => r.level === 'BLOCK') ?? false)

const money = (v: number | string | null | undefined) => `¥${Number(v ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const RISK_BADGES: Record<RiskLevel, string> = {
  NONE: 'bg-emerald-50 text-emerald-700', INFO: 'bg-sky-50 text-sky-700', WARN: 'bg-amber-50 text-amber-700', BLOCK: 'bg-red-50 text-red-700',
}
const riskBadge = (level: RiskLevel) => RISK_BADGES[level] || 'bg-slate-100 text-slate-500'
const riskItemClass = (level: string) => ({
  BLOCK: 'border-red-200 bg-red-50 text-red-700', WARN: 'border-amber-200 bg-amber-50 text-amber-800', INFO: 'border-sky-200 bg-sky-50 text-sky-700',
}[level] || 'border-slate-200 bg-white text-slate-600')
const statusBadge = (s: VoucherStatus) => ({ DRAFT: 'bg-slate-100 text-slate-600', POSTED: 'bg-emerald-50 text-emerald-700', VOID: 'bg-red-50 text-red-600' }[s])
const confidenceBadge = (c: string) => ({
  HIGH: 'bg-emerald-50 text-emerald-700', MEDIUM: 'bg-sky-50 text-sky-700', LOW: 'bg-amber-50 text-amber-700', MANUAL: 'bg-violet-50 text-violet-700',
}[c] || 'bg-slate-100 text-slate-500')

async function guarded(action: () => Promise<void>, failure: string) {
  busy.value = true
  try {
    await action()
  } catch (e) {
    toastError(getErrorMessage(e, failure))
  } finally {
    busy.value = false
  }
}

async function loadList() {
  if (activeTab.value === 'summary') return
  loading.value = true
  try {
    vouchers.value = await api.listVouchers(props.teamId, activeTab.value)
  } catch (e) {
    toastError(getErrorMessage(e, '加载凭证列表失败'))
  } finally {
    loading.value = false
  }
}

async function loadUnbooked() {
  try {
    unbooked.value = await api.listUnbooked(props.teamId)
  } catch {
    unbooked.value = []
  }
}

async function loadSummary() {
  try {
    summary.value = await api.getMonthlySummary(props.teamId, period.value || undefined)
  } catch (e) {
    toastError(getErrorMessage(e, '加载月度汇总失败'))
  }
}

async function reloadAll() {
  await Promise.all([loadList(), loadUnbooked(), activeTab.value === 'summary' ? loadSummary() : Promise.resolve()])
}

function setTab(tab: VoucherStatus | 'summary') {
  activeTab.value = tab
  selected.value = null
  if (tab === 'summary') loadSummary()
  else loadList()
}

function resetForm() {
  acknowledged.value = false
  confirmNote.value = ''
  voidOpen.value = false
  voidReason.value = ''
  pendingChange.value = null
}

async function select(id: number) {
  await guarded(async () => {
    selected.value = await api.getVoucher(props.teamId, id)
    resetForm()
  }, '加载凭证失败')
}

function adopt(detail: VoucherDetail) {
  selected.value = detail
  const row = vouchers.value.findIndex((v) => v.id === detail.id)
  if (row >= 0) vouchers.value[row] = { ...vouchers.value[row], ...detail }
}

async function generate(claimId: number) {
  await guarded(async () => {
    const detail = await api.generateFromClaim(props.teamId, claimId)
    toastSuccess(`已生成凭证草稿（报销单 #${claimId}）`)
    activeTab.value = 'DRAFT'
    await reloadAll()
    selected.value = detail
    resetForm()
  }, '生成凭证失败')
}

function startChange(entry: VoucherEntry, code: string) {
  if (code === entry.subjectCode) return
  pendingChange.value = { entry, code, reason: '' }
}

async function applyChange() {
  const change = pendingChange.value
  if (!change || !selected.value) return
  await guarded(async () => {
    adopt(await api.updateEntrySubject(props.teamId, selected.value!.id, change.entry.id, change.code, change.reason.trim()))
    pendingChange.value = null
    toastSuccess('科目已修改，风险项已重新检查')
  }, '修改科目失败')
}

async function changeDate(value: string) {
  if (!selected.value || !value) return
  await guarded(async () => {
    adopt(await api.updateVoucherDate(props.teamId, selected.value!.id, value))
    toastSuccess('凭证日期已修改')
  }, '修改日期失败')
}

async function recheck() {
  if (!selected.value) return
  await guarded(async () => {
    adopt(await api.recheckVoucher(props.teamId, selected.value!.id))
    toastSuccess('已重新检查')
  }, '重新检查失败')
}

async function regenerate() {
  if (!selected.value) return
  await guarded(async () => {
    adopt(await api.regenerateVoucher(props.teamId, selected.value!.id))
    resetForm()
    toastSuccess('已按规则重新生成')
  }, '重新生成失败')
}

async function confirm() {
  if (!selected.value) return
  await guarded(async () => {
    const detail = await api.confirmVoucher(props.teamId, selected.value!.id, {
      note: confirmNote.value.trim() || undefined, acknowledge_warnings: acknowledged.value,
    })
    toastSuccess(`已入账：${detail.voucherNo}`)
    selected.value = detail
    resetForm()
    await reloadAll()
  }, '确认入账失败')
}

async function doVoid() {
  if (!selected.value) return
  await guarded(async () => {
    selected.value = await api.voidVoucher(props.teamId, selected.value!.id, voidReason.value.trim())
    resetForm()
    toastSuccess('凭证已作废')
    await reloadAll()
  }, '作废失败')
}

async function init() {
  selected.value = null
  resetForm()
  try {
    subjects.value = await api.listSubjects(props.teamId)
  } catch (e) {
    toastError(getErrorMessage(e, '加载科目表失败'))
  }
  await reloadAll()
}

watch(() => props.teamId, init)
onMounted(init)
</script>
