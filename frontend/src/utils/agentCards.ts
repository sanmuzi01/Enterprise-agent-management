/** 把部门助手调用业务工具后返回的数据，整理成用户看得懂的业务卡片。
 *
 * 工具结果是业务服务原样返回的 JSON（报销单、请假单、采购单、工单……）。以前页面只显示
 * “调用工具 / 工具结果：{...}”，用户看不出助手办成了什么。这里按数据的样子识别是哪种业务单据，
 * 抽出用户关心的几项（类型、金额、日期、状态、还缺什么），并告诉页面：
 *   - 这张单子能不能在卡片上直接确认提交（只有草稿可以）；
 *   - 去工作台的哪个分区能看到它。
 * 纯函数，不发请求，便于单测。
 */
import { statusLabel } from './requestStatus'

export type CardKind = 'expense' | 'leave' | 'purchase' | 'ticket' | 'followup' | 'budget' | 'error'

export interface CardField {
  label: string
  value: string
  /** 需要用户注意的项（比如还没填发票号） */
  warn?: boolean
}

export interface BusinessCard {
  kind: CardKind
  title: string
  id?: number
  status?: string
  statusText?: string
  fields: CardField[]
  /** 草稿：卡片上可以直接确认提交（用户点按钮就是人工确认，不再经过模型） */
  canSubmit?: boolean
  /** 工作台里能看到这张单子的分区 */
  section?: 'office' | 'business'
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
  fields.push({ label: '创建', value: day(r.createdAt) })
  return {
    kind: 'expense', id: r.id, status: r.status, statusText: statusLabel(r.status),
    title: r.status === 'DRAFT' ? '报销草稿' : `报销单 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', section: 'office',
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
    kind: 'leave', id: r.id, status: r.status, statusText: statusLabel(r.status),
    title: r.status === 'DRAFT' ? '请假草稿' : `请假单 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', section: 'office',
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
    kind: 'purchase', id: r.id, status: r.status, statusText: statusLabel(r.status),
    title: r.status === 'DRAFT' ? '采购申请草稿' : `采购申请 #${r.id}`,
    fields, canSubmit: r.status === 'DRAFT', section: 'business',
  }
}

function ticketCard(r: Raw): BusinessCard {
  const fields: CardField[] = [
    { label: '类型', value: r.categoryLabel || r.category || '—' },
    { label: '优先级', value: r.priorityLabel || r.priority || '—' },
  ]
  if (r.assigneeName) fields.push({ label: '处理人', value: String(r.assigneeName) })
  if (r.slaDueAt) fields.push({ label: '处理时限', value: dateTime(r.slaDueAt), warn: r.slaStatus === 'BREACHED' || r.slaStatus === 'AT_RISK' })
  return {
    kind: 'ticket', id: r.id, status: r.status, statusText: r.statusLabel || r.status,
    title: `工单 #${r.id}：${r.title || ''}`.replace(/：$/, ''),
    fields, section: 'office',
  }
}

function followupCard(r: Raw): BusinessCard {
  return {
    kind: 'followup', id: r.id, status: r.status, statusText: FOLLOWUP_STATUS[r.status] || r.status,
    title: r.status === 'DRAFT' ? '客户跟进记录草稿' : `客户跟进记录 #${r.id}`,
    fields: [{ label: '内容', value: String(r.content || '—') }, { label: '创建', value: day(r.createdAt) }],
    canSubmit: r.status === 'DRAFT', section: 'business',
  }
}

function budgetCard(r: Raw): BusinessCard {
  return {
    kind: 'budget', title: `${r.year ?? ''} 年部门预算`.trim(),
    fields: [{ label: '剩余', value: money(r.remainingAmount) }],
  }
}

/** 按数据的样子判断是哪种单据；认不出来的返回 null（只在执行过程里显示一行，不做卡片） */
export function cardFromRecord(r: unknown): BusinessCard | null {
  if (!isObj(r)) return null
  if ('leaveTypeCode' in r) return leaveCard(r)
  if ('remainingAmount' in r && 'year' in r && !('lines' in r)) return budgetCard(r)
  if (Array.isArray(r.lines) && 'applicantUserId' in r) return expenseCard(r)
  if (Array.isArray(r.lines) && 'requesterUserId' in r) return purchaseCard(r)
  if ('slaDueAt' in r && 'title' in r) return ticketCard(r)
  if ('customerId' in r && 'content' in r) return followupCard(r)
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
    const data = parseJson(result)
    const records = Array.isArray(data) ? data : [data]
    let shown = 0
    for (const record of records) {
      const card = cardFromRecord(record)
      if (!card) continue
      if (card.kind === 'budget') {
        budget = card
        continue
      }
      if (Array.isArray(data) && shown >= MAX_LIST_CARDS) {
        hidden += 1
        continue
      }
      shown += 1
      const key = `${card.kind}-${card.id ?? byKey.size}`
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
