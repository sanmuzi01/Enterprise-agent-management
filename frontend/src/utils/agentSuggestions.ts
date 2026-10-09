/** 部门助手的“今日建议”：把部门首页已经算好的数字（待我审批、逾期工单、待接受的责任……）
 * 变成一句话提醒，并配好一句可以直接交给助手的话。
 *
 * 不调模型：数字来自首页接口，权限和口径沿用各业务模块自己的规则；助手只在用户点了之后才出场。
 */
import type { HomeCard } from '../api/enterpriseWorkspace'

export interface Suggestion {
  key: string
  /** 一句话提醒，例如“3 项等你审批” */
  text: string
  hint: string
  tone: HomeCard['tone']
  /** 点“让助手处理”时交给助手的话 */
  prompt: string
  card: HomeCard
}

interface Rule {
  text: (value: number, card: HomeCard) => string
  prompt: string
  /** 数字大于 0 就提醒；否则只在首页卡片标了警示色时提醒 */
  always?: boolean
}

/** 首页提示常写成“其中逾期 1 项”，拼成一句短话：“执行中的责任逾期 1 项” */
function withHint(prefix: string, hint: string): string {
  if (!hint.startsWith('其中')) return `${prefix}：${hint}`
  const rest = hint.slice(2).trimStart()
  return `${prefix}${/^\d/.test(rest) ? ' ' : ''}${rest}`
}

const RULES: Record<string, Rule> = {
  approvals: { always: true, text: (n) => `${n} 项等你审批`, prompt: '列出等我审批的请假、报销、采购和 IT 申请，按紧急程度排序，并说明每一项需要我决定什么' },
  resp_accept: { always: true, text: (n) => `${n} 项责任等你接受`, prompt: '列出指派给我、等我接受的责任事项，说明各自的截止时间和验收标准' },
  resp_doing: { text: (_n, c) => withHint('执行中的责任', c.hint), prompt: '我执行中的责任事项里哪些已经逾期或快到期？给出今天的处理顺序' },
  resp_blocked: { always: true, text: (n) => `${n} 项责任受阻`, prompt: '列出我受阻的责任事项和阻塞原因，建议怎么推进' },
  resp_review: { always: true, text: (n) => `${n} 项成果等你验收`, prompt: '列出等我验收的责任事项和员工提交的成果' },
  resp_team_overdue: { always: true, text: (n) => `部门有 ${n} 项责任逾期`, prompt: '部门有哪些逾期的责任事项？按负责人列出，并建议怎么催办' },
  todos: { text: (_n, c) => withHint('待办', c.hint), prompt: '汇总我今天的待办，先列逾期和快到期的，给出处理顺序' },
  att_explain: { always: true, text: (n) => `${n} 条考勤异常等你说明`, prompt: '我有哪些考勤异常需要说明？' },
  att_decide: { always: true, text: (n) => `${n} 条考勤异常等你认定`, prompt: '列出等我认定的考勤异常和员工的说明' },
  vouchers: { text: (_n, c) => withHint('待核对凭证', c.hint), prompt: '有哪些记账凭证待我核对？先列有风险的，并说明风险点' },
  unbooked: { always: true, text: (n) => `${n} 笔已批准报销还没有凭证`, prompt: '哪些已批准的报销还没有生成记账凭证？' },
  it_unassigned: { always: true, text: (n) => `${n} 张工单等待接单`, prompt: '现在有哪些未接单的工单？按处理时限排序' },
  it_overdue: { always: true, text: (n) => `${n} 张工单已超过处理时限`, prompt: '列出已超时的 IT 工单，并给出处理建议' },
  hr_overdue: { always: true, text: (n) => `${n} 项人事办理任务逾期`, prompt: '列出逾期的人事办理任务和负责人' },
  hr_tasks: { text: (_n, c) => withHint('人事办理任务', c.hint), prompt: '列出分给我的人事办理任务，先列逾期的' },
  tickets: { text: (_n, c) => withHint('我的工单', c.hint), prompt: '查看我的工单进度，哪些需要我补充信息或确认解决？' },
}

const TONE_ORDER: Record<HomeCard['tone'], number> = { danger: 0, warn: 1, normal: 2 }

export const MAX_SUGGESTIONS = 4

export function suggestionsFromCards(cards: HomeCard[] | undefined | null): Suggestion[] {
  const list: Suggestion[] = []
  for (const card of cards || []) {
    const rule = RULES[card.key]
    const value = typeof card.value === 'number' ? card.value : Number(card.value)
    if (!rule || !Number.isFinite(value) || value <= 0) continue
    if (!rule.always && card.tone === 'normal') continue
    list.push({ key: card.key, text: rule.text(value, card), hint: card.hint, tone: card.tone, prompt: rule.prompt, card })
  }
  return list
    .sort((a, b) => TONE_ORDER[a.tone] - TONE_ORDER[b.tone])
    .slice(0, MAX_SUGGESTIONS)
}
