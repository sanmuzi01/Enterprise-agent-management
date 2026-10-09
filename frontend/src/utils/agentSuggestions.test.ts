import { describe, expect, it } from 'vitest'
import type { HomeCard } from '../api/enterpriseWorkspace'
import { MAX_SUGGESTIONS, suggestionsFromCards } from './agentSuggestions'

const card = (key: string, value: number | string, tone: HomeCard['tone'] = 'normal', hint = ''): HomeCard =>
  ({ key, label: key, value, hint, section: 'office', tone })

describe('agentSuggestions', () => {
  it('有数字的事项变成一句提醒，并带上交给助手的话', () => {
    const [s] = suggestionsFromCards([card('approvals', 3, 'warn')])
    expect(s.text).toBe('3 项等你审批')
    expect(s.prompt).toContain('审批')
  })

  it('数字为 0、认不出来的卡片、没有警示的统计项都不提醒', () => {
    const list = suggestionsFromCards([
      card('approvals', 0), card('customers', 12), card('it_sla', '98%'), card('resp_doing', 2, 'normal', '我已接受、正在做的责任'),
    ])
    expect(list).toEqual([])
  })

  it('标了警示色的统计项会提醒，红色排在最前，最多几条', () => {
    const list = suggestionsFromCards([
      card('approvals', 1, 'warn'),
      card('resp_doing', 2, 'danger', '其中逾期 1 项'),
      card('it_overdue', 4, 'danger'),
      card('resp_accept', 1, 'warn'),
      card('att_explain', 2, 'warn'),
    ])
    expect(list).toHaveLength(MAX_SUGGESTIONS)
    expect(list.slice(0, 2).every((s) => s.tone === 'danger')).toBe(true)
    expect(list[0].text).toBe('执行中的责任逾期 1 项')
    expect(suggestionsFromCards([card('vouchers', 4, 'warn', '其中 2 张有风险需核对')])[0].text).toBe('待核对凭证 2 张有风险需核对')
  })
})
