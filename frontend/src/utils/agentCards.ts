/** 把部门助手调用业务工具后返回的数据，整理成用户看得懂的业务卡片。
 *
 * 工具结果是业务服务原样返回的 JSON（报销单、请假单、采购单、工单、凭证、人事事项……）。以前页面只显示
 * “调用工具 / 工具结果：{...}”，用户看不出助手办成了什么。这里按数据的样子识别是哪种业务单据，
 * 抽出用户关心的几项（类型、金额、日期、状态、还缺什么），并告诉页面：
 *   - 这张单子能不能在卡片上直接确认提交（只有草稿可以）；
 *   - 去工作台的哪个分区能看到它（必要时带上具体打开哪一项）；
 *   - 接下来可以让助手做什么（比如自助排查没解决 → 提交工单）。
 * 纯函数，不发请求，便于单测。
 */
import { statusLabel } from './requestStatus'

export type CardKind =
  | 'expense' | 'leave' | 'purchase' | 'ticket' | 'followup' | 'budget'
  | 'leave_balance' | 'inventory' | 'opportunity' | 'customer' | 'voucher'
  | 'hr_precheck' | 'hr_case' | 'hr_task' | 'it_solution' | 'responsibility' | 'resp_plan'

export type CardSection = 'office' | 'business' | 'collab' | 'attendance'

export interface CardField {
  label: string
  value: string
  /** 需要用户注意的项（比如还没填发票号、库存低于安全线） */
  warn?: boolean
}

export interface CardTarget {
  section: CardSection
  /** 责任协同里打开哪个视图 */
  tab?: string
  /** 直接打开的责任计划 */
  planId?: number
  /** 打开 / 高亮的那条记录 */
  record?: { kind: string; id: number }
}

export interface FollowUp {
  label: string
  /** 交给助手的下一句话 */
  prompt: string
}

export interface BusinessCard {
  kind: CardKind
  title: string
  id?: number
  status?: string
  statusText?: string
  /** 状态徽标的颜色倾向 */
  tone?: 'normal' | 'good' | 'warn' | 'danger'
  fields: CardField[]
  /** 草稿：卡片上可以直接确认提交（用户点按钮就是人工确认，不再经过模型） */
  canSubmit?: boolean
  target?: CardTarget
  /** 去工作台按钮的文字（默认“在工作台中查看”） */
  targetLabel?: string
  followUps?: FollowUp[]
}

export interface ToolResultInput {
  name: string
  result: string
}

const EXPENSE_CATEGORIES: Record<string, string> = {
  TRAVEL: '差旅', MEAL: '餐饮', OFFICE_SUPPLY: '办公用品', TRANSPORT: '交通', OTHER: '其它',
}
const LEAVE_TYPES: Record<string, string> = { annual: '年假', sick: '病假', personal: '事假' }
const FOLLOWUP_STATUS: Record<string, string> = { DRAFT: '草稿', CONFIRMED: '已记录' }
const STAGES: Record<string, string> = {
  LEAD: '线索', QUALIFIED: '已确认需求', PROPOSAL: '方案报价', NEGOTIATION: '谈判中', WON: '已成交', LOST: '已流失',
}
const VOUCHER_STATUS: Record<string, string> = { DRAFT: '待核对', POSTED: '已入账', VOID: '已作废' }
const RISK_TONE: Record<string, BusinessCard['tone']> = { BLOCK: 'danger', WARN: 'warn', INFO: 'normal', NONE: 'good' }

/** 一次回答里最多展开几张单据卡片，再多就只给数量，避免一屏全是卡片 */
export const MAX_LIST_CARDS = 5

export function money(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function day(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  return value.slice(0, 10)
}

function dateTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

type Raw = Record<string, any>
const isObj = (v: unknown): v is Raw => !!v && typeof v === 'object' && !Array.isArray(v)
const text = (v: unknown) => (v == null || v === '' ? '—' : String(v))

function requestTone(status: string): BusinessCard['tone'] {
  return status === 'APPROVED' ? 'good' : status === 'REJECTED' ? 'danger' : status === 'SUBMITTED' ? 'warn' : 'normal'
}

// ---------------- 申请单（可在卡片上确认提交） ----------------

function expenseCard(r: Raw): BusinessCard {
  const lines: Raw[] = Array.isArray(r.lines) ? r.lines : []
  const categories = [...new Set(lines.map((l) => EXPENSE_CATEGORIES[l.category] || l.category).filter(Boolean))]
  const fields: CardField[] = [
    { label: '类型', value: categories.join('、') || '—' },
    { label: '金额', value: money(r.totalAmount) },
  ]
  const detail = lines.map((l) => [EXPENSE_CATEGORIES[l.category] || l.category, money(l.amount), l.description].filter(Boolean).join(' '))
  if (detail.length > 1 || lines[0]?.description) fields.push({ label: '明细', value: detail.join('；') })
  const missingInvoice = lines.filter((l) => !l.invoiceNo).length
  fields.push(missingInvoice
    ? { label: '发票', value: lines.length > 1 ? `${missingInvoice} 笔还没有填发票号` : '还没有填发票号', warn: true }
    : { label: '发票', value: lines.map((l) => l.invoiceNo).join('、') })
  if (r.decisionNote) fields.push({ label: '审批意见', value: String(r.decisionNote) })
  fields.push({ label: '创建', value: day(r.createdAt) })
  return {
    kind: 'expense', id: r.id, status: r.status, statusText: statusLabel(r.status), tone: requestTone(r.status),
    title: r.status === 'DRAFT' ? '报销草稿' : `报销单 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', target: { section: 'office', record: { kind: 'expense', id: r.id } },
  }
}

function leaveCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '类型', value: LEAVE_TYPES[r.leaveTypeCode] || r.leaveTypeCode || '—' },
    { label: '日期', value: `${day(r.startDate)} 至 ${day(r.endDate)}（${r.days ?? '—'} 天）` },
  ]
  if (r.reason) fields.push({ label: '事由', value: String(r.reason) })
  if (r.decisionNote) fields.push({ label: '审批意见', value: String(r.decisionNote) })
  return {
    kind: 'leave', id: r.id, status: r.status, statusText: statusLabel(r.status), tone: requestTone(r.status),
    title: r.status === 'DRAFT' ? '请假草稿' : `请假单 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', target: { section: 'office', record: { kind: 'leave', id: r.id } },
  }
}

function purchaseCard(r: Raw): BusinessCard {
  const lines: Raw[] = Array.isArray(r.lines) ? r.lines : []
  const fields: CardField[] = [
    { label: '物品', value: lines.map((l) => `${l.productName || l.sku} × ${l.quantity}`).join('、') || '—' },
    { label: '金额', value: money(r.totalAmount) },
  ]
  if (r.purchaseOrder?.supplierName) fields.push({ label: '供应商', value: String(r.purchaseOrder.supplierName) })
  if (r.decisionNote) fields.push({ label: '审批意见', value: String(r.decisionNote) })
  return {
    kind: 'purchase', id: r.id, status: r.status, statusText: statusLabel(r.status), tone: requestTone(r.status),
    title: r.status === 'DRAFT' ? '采购申请草稿' : `采购申请 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', target: { section: 'business', record: { kind: 'purchase', id: r.id } },
  }
}

function followupCard(r: Raw): BusinessCard {
  return {
    kind: 'followup', id: r.id, status: r.status, statusText: FOLLOWUP_STATUS[r.status] || r.status,
    tone: r.status === 'CONFIRMED' ? 'good' : 'normal',
    title: r.status === 'DRAFT' ? '客户跟进记录草稿' : `客户跟进记录 #${r.id}`,
    fields: [{ label: '内容', value: text(r.content) }, { label: '创建', value: day(r.createdAt) }],
    canSubmit: r.status === 'DRAFT', target: { section: 'business', record: { kind: 'customer', id: r.customerId } },
  }
}

// ---------------- 查询结果 ----------------

function ticketCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '类型', value: r.categoryLabel || r.category || '—' },
    { label: '优先级', value: r.priorityLabel || r.priority || '—' },
  ]
  if (r.assigneeName) fields.push({ label: '处理人', value: String(r.assigneeName) })
  if (r.slaDueAt) fields.push({ label: '处理时限', value: dateTime(r.slaDueAt), warn: r.slaStatus === 'BREACHED' || r.slaStatus === 'AT_RISK' })
  const tone = r.slaStatus === 'BREACHED' ? 'danger' : r.status === 'RESOLVED' || r.status === 'CLOSED' ? 'good' : 'normal'
  return {
    kind: 'ticket', id: r.id, status: r.status, statusText: r.statusLabel || r.status, tone,
    title: `工单 #${r.id}：${r.title || ''}`.replace(/：$/, ''),
    fields, target: { section: 'office', record: { kind: 'ticket', id: r.id } },
  }
}

function budgetCard(r: Raw): BusinessCard {
  return {
    kind: 'budget', title: `${r.year ?? ''} 年部门预算`.trim(),
    fields: [{ label: '剩余', value: money(r.remainingAmount) }],
  }
}

function leaveBalanceCard(r: Raw): BusinessCard {
  return {
    kind: 'leave_balance', title: `${r.year ?? ''} 年${r.leaveTypeName || LEAVE_TYPES[r.leaveTypeCode] || '假期'}余额`.trim(),
    fields: [{ label: '剩余', value: `${r.remainingDays} 天`, warn: Number(r.remainingDays) <= 0 }],
    target: { section: 'office' }, targetLabel: '去请假',
  }
}

function inventoryCard(r: Raw): BusinessCard {
  const low = !!r.belowSafetyStock
  return {
    kind: 'inventory', title: `${r.name || r.sku}（${r.sku}）`,
    statusText: low ? '低于安全库存' : '库存充足', tone: low ? 'warn' : 'good',
    fields: [
      { label: '现有', value: `${r.onHandQty} ${r.unit || ''}`.trim(), warn: low },
      { label: '安全库存', value: `${r.safetyStockQty} ${r.unit || ''}`.trim() },
      { label: '单价', value: money(r.unitPrice) },
    ],
    followUps: low ? [{ label: '起草补货申请', prompt: `${r.name || r.sku}（${r.sku}）低于安全库存，帮我按补到安全库存以上起草采购申请草稿，先查部门预算，不要提交` }] : [],
  }
}

function opportunityCard(r: Raw): BusinessCard {
  const stage = STAGES[r.stage] || r.stage
  return {
    kind: 'opportunity', id: r.id, title: `商机 #${r.id}`, status: r.stage, statusText: stage,
    tone: r.stage === 'WON' ? 'good' : r.stage === 'LOST' ? 'danger' : 'normal',
    fields: [
      { label: '阶段', value: stage },
      { label: '金额', value: money(r.amount) },
      { label: '客户', value: `#${r.customerId}` },
      { label: '更新', value: day(r.updatedAt) },
    ],
    target: { section: 'business', record: { kind: 'customer', id: r.customerId } },
  }
}

function customerCard(r: Raw): BusinessCard {
  const fields: CardField[] = [{ label: '行业', value: text(r.industry) }]
  if (Array.isArray(r.contacts)) fields.push({ label: '联系人', value: r.contacts.map((c: Raw) => c.name).filter(Boolean).join('、') || '—' })
  if (Array.isArray(r.recentFollowUps) && r.recentFollowUps.length) {
    const last = r.recentFollowUps[0]
    fields.push({ label: '最近跟进', value: `${day(last.createdAt)} ${last.content || ''}`.trim() })
  }
  if (Array.isArray(r.opportunities) && r.opportunities.length) {
    fields.push({ label: '商机', value: r.opportunities.map((o: Raw) => `${STAGES[o.stage] || o.stage} ${money(o.amount)}`).join('；') })
  }
  return {
    kind: 'customer', id: r.id, title: `客户：${r.name}`, fields, target: { section: 'business', record: { kind: 'customer', id: r.id } },
    followUps: Array.isArray(r.recentFollowUps)
      ? [{ label: '记录一次跟进', prompt: `我刚和客户「${r.name}」（客户 ID ${r.id}）沟通过，内容是：` }]
      : [],
  }
}

function voucherCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '摘要', value: text(r.summary) },
    { label: '金额', value: money(r.totalAmount) },
  ]
  if (r.applicantName) fields.push({ label: '申请人', value: String(r.applicantName) })
  const risks: Raw[] = Array.isArray(r.risks) ? r.risks : []
  if (risks.length) fields.push({ label: '风险', value: risks.map((x) => x.message).join('；'), warn: risks.some((x) => x.level !== 'INFO') })
  else if (r.riskLabel) fields.push({ label: '风险', value: String(r.riskLabel), warn: r.riskLevel === 'WARN' || r.riskLevel === 'BLOCK' })
  const entries: Raw[] = Array.isArray(r.entries) ? r.entries : []
  if (entries.length) fields.push({ label: '分录', value: entries.map((e) => `${e.direction === 'D' ? '借' : '贷'} ${e.subjectName} ${money(e.amount)}`).join('；') })
  return {
    kind: 'voucher', id: r.id, status: r.status, statusText: VOUCHER_STATUS[r.status] || r.status,
    tone: RISK_TONE[r.riskLevel] || 'normal',
    title: r.voucherNo ? `凭证 ${r.voucherNo}` : `凭证草稿 #${r.id}`,
    fields, target: { section: 'business', record: { kind: 'voucher', id: r.id } }, targetLabel: '去核对',
  }
}

function hrPrecheckCard(r: Raw): BusinessCard {
  const checks: Raw[] = Array.isArray(r.checks) ? r.checks : []
  const blocking = checks.filter((c) => c.level === 'BLOCK')
  const fields: CardField[] = checks.length
    ? checks.map((c) => ({ label: c.level === 'BLOCK' ? '必须处理' : c.level === 'WARN' ? '需要注意' : '提示', value: String(c.message), warn: c.level !== 'INFO' }))
    : [{ label: '结果', value: '没有发现冲突或遗留事项' }]
  return {
    kind: 'hr_precheck', title: '入转调离预检',
    statusText: blocking.length ? '有问题需先处理' : r.canSubmit === false ? '暂不能发起' : '可以发起',
    tone: blocking.length || r.canSubmit === false ? 'danger' : RISK_TONE[r.riskLevel] || 'good',
    fields, target: { section: 'business' }, targetLabel: '去发起办理',
  }
}

function hrCaseCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '员工', value: text(r.employeeName) },
    { label: '生效日期', value: day(r.effectiveDate) },
    { label: '办理进度', value: `${(r.totalTasks ?? 0) - (r.openTasks ?? 0)} / ${r.totalTasks ?? 0} 项完成`, warn: !!r.effectPending },
  ]
  if (r.targetTeamName) fields.push({ label: '调往', value: String(r.targetTeamName) })
  return {
    kind: 'hr_case', id: r.id, status: r.status, statusText: r.statusLabel || r.status,
    tone: r.status === 'COMPLETED' ? 'good' : r.status === 'REJECTED' || r.status === 'CANCELLED' ? 'danger' : 'normal',
    title: `${r.caseTypeLabel || '人事事项'} #${r.id}`, fields, target: { section: 'business', record: { kind: 'hr_case', id: r.id } },
  }
}

function hrTaskCard(r: Raw): BusinessCard {
  return {
    kind: 'hr_task', id: r.taskId, title: r.title || '人事办理任务',
    statusText: r.overdue ? '已逾期' : '待办理', tone: r.overdue ? 'danger' : 'normal',
    fields: [
      { label: '事项', value: `${r.caseTypeLabel || ''} · ${r.employeeName || ''}`.replace(/^ · | · $/g, '') || '—' },
      { label: '负责方', value: text(r.ownerLabel) },
      { label: '期限', value: day(r.dueDate), warn: !!r.overdue },
    ],
    target: { section: 'office', record: { kind: 'hr_case', id: r.caseId } },
  }
}

function itSolutionCard(r: Raw): BusinessCard {
  const c: Raw = r.classification || {}
  const articles: Raw[] = Array.isArray(r.articles) ? r.articles.slice(0, 3) : []
  const fields: CardField[] = articles.length
    ? articles.map((a, i) => ({ label: `方案 ${i + 1}`, value: String(a.title) }))
    : [{ label: '方案', value: '知识库里没有现成的解决办法' }]
  fields.push({ label: '建议类型', value: `${c.categoryLabel || '—'} · 优先级${c.priorityLabel || '—'}` })
  return {
    kind: 'it_solution', title: '自助排查建议', fields,
    followUps: [{ label: '还没解决，帮我提交工单', prompt: `按建议的类型「${c.categoryLabel || ''}」和优先级「${c.priorityLabel || ''}」，帮我把这个问题提交成 IT 工单，先复述内容给我确认` }],
  }
}

function responsibilityCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '主责人', value: text(r.responsibleName) },
    { label: '截止', value: day(r.dueDate), warn: !!r.overdue },
  ]
  if (r.deliverable) fields.push({ label: '交付物', value: String(r.deliverable) })
  if (r.acceptanceCriteria) fields.push({ label: '验收标准', value: String(r.acceptanceCriteria) })
  if (r.blockedReason) fields.push({ label: '受阻原因', value: String(r.blockedReason), warn: true })
  return {
    kind: 'responsibility', id: r.id, title: String(r.title || '责任事项'), statusText: r.overdue ? `${r.statusLabel || ''} · 已逾期` : r.statusLabel,
    tone: r.overdue || r.blockedReason ? 'danger' : 'normal',
    fields, target: { section: 'collab', tab: 'mine' },
  }
}

function respPlanCard(r: Raw): BusinessCard {
  const gaps: Raw[] = Array.isArray(r.needSupplement) ? r.needSupplement : []
  const fields: CardField[] = [{ label: '责任事项', value: `${r.taskCount ?? 0} 项` }]
  if (gaps.length) fields.push({ label: '待补全', value: gaps.map((g) => `第 ${g.seq} 项缺${(g.problems || []).join('、')}`).join('；'), warn: true })
  if (Array.isArray(r.unresolved) && r.unresolved.length) fields.push({ label: '未决事项', value: r.unresolved.join('；') })
  fields.push({ label: '说明', value: '还没有通知任何人，核对后由部门负责人发布' })
  return {
    kind: 'resp_plan', id: r.planId, title: `责任计划草稿 #${r.planId}`, statusText: r.status || '草稿', tone: gaps.length ? 'warn' : 'normal',
    fields, target: { section: 'collab', tab: 'plans', planId: r.planId }, targetLabel: '去核对并发布',
  }
}

/** 按数据的样子判断是哪种单据；认不出来的返回 null（只在执行过程里显示一行，不做卡片）。
 * 顺序有讲究：请假余额也带 leaveTypeCode，要先于请假单判断。 */
export function cardFromRecord(r: unknown): BusinessCard | null {
  if (!isObj(r)) return null
  if ('remainingDays' in r && 'leaveTypeCode' in r) return leaveBalanceCard(r)
  if ('leaveTypeCode' in r) return leaveCard(r)
  if ('remainingAmount' in r && 'year' in r && !('lines' in r)) return budgetCard(r)
  if ('voucherDate' in r || 'expenseClaimId' in r) return voucherCard(r)
  if (Array.isArray(r.lines) && 'applicantUserId' in r) return expenseCard(r)
  if (Array.isArray(r.lines) && 'requesterUserId' in r) return purchaseCard(r)
  if ('slaDueAt' in r && 'title' in r) return ticketCard(r)
  if ('customerId' in r && 'content' in r) return followupCard(r)
  if ('customerId' in r && 'stage' in r) return opportunityCard(r)
  if ('industry' in r && 'name' in r) return customerCard(r)
  if ('onHandQty' in r && 'sku' in r) return inventoryCard(r)
  if ('checks' in r && 'canSubmit' in r) return hrPrecheckCard(r)
  if ('caseTypeLabel' in r && 'taskId' in r) return hrTaskCard(r)
  if ('caseTypeLabel' in r && 'effectiveDate' in r) return hrCaseCard(r)
  if ('classification' in r && 'articles' in r) return itSolutionCard(r)
  if ('planId' in r && 'taskCount' in r) return respPlanCard(r)
  if ('responsibleName' in r && 'statusLabel' in r && 'title' in r) return responsibilityCard(r)
  return null
}

export function parseJson(raw: string | undefined): unknown {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

/** 工具返回了错误（权限不够、预算不足、业务服务不可用……） */
export function toolError(raw: string | undefined): string | null {
  const data = parseJson(raw)
  return isObj(data) && typeof data.error === 'string' && data.error ? data.error : null
}

/** 列表类结果可能包在 {items: [...]} / {tasks: [...]} 里 */
function recordsOf(data: unknown): { records: unknown[]; isList: boolean } {
  if (Array.isArray(data)) return { records: data, isList: true }
  if (isObj(data)) {
    for (const key of ['items', 'tasks', 'cases', 'vouchers']) {
      if (Array.isArray(data[key]) && !cardFromRecord(data)) return { records: data[key], isList: true }
    }
  }
  return { records: [data], isList: false }
}

export interface MessageCards {
  cards: BusinessCard[]
  /** 列表结果里没展开的单据数量 */
  hidden: number
}

/** 一次回答里所有工具结果 → 卡片。同一张单子被查了好几次只留最后一次；
 * 同一次回答里查过预算，就把余额写进报销 / 采购卡片，不再单独占一张卡。 */
export function buildMessageCards(results: ToolResultInput[]): MessageCards {
  const byKey = new Map<string, BusinessCard>()
  let budget: BusinessCard | null = null
  let hidden = 0
  for (const { result } of results) {
    const { records, isList } = recordsOf(parseJson(result))
    let shown = 0
    for (const record of records) {
      const card = cardFromRecord(record)
      if (!card) continue
      if (card.kind === 'budget') {
        budget = card
        continue
      }
      if (isList && shown >= MAX_LIST_CARDS) {
        hidden += 1
        continue
      }
      shown += 1
      const key = `${card.kind}-${card.id ?? card.title}`
      byKey.delete(key)   // 重新插入，保证顺序跟最后一次出现一致
      byKey.set(key, card)
    }
  }
  const cards = [...byKey.values()]
  if (budget) {
    const target = cards.find((c) => c.kind === 'expense' || c.kind === 'purchase')
    if (target) target.fields.splice(2, 0, { label: '预算余额', value: budget.fields[0].value })
    else cards.unshift(budget)
  }
  return { cards, hidden }
}
