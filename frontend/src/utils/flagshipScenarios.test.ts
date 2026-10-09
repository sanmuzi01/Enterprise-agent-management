import { describe, expect, it } from 'vitest'
import type { HomeCard } from '../api/enterpriseWorkspace'
import { FLAGSHIP_SCENARIOS, dailyBriefPrompt, scenarioFor } from './flagshipScenarios'

describe('flagshipScenarios', () => {
  it('每个部门业务类型都有一个招牌场景，没有业务类型的部门用责任计划', () => {
    for (const code of ['finance', 'it', 'hr', 'sales', 'procurement']) expect(scenarioFor(code).code).toBe(code)
    expect(scenarioFor(null).title).toContain('责任计划')
    expect(scenarioFor('unknown').code).toBeNull()
  })

  it('交给助手的话里带着原始材料，并且只办到草稿、不代为提交', () => {
    for (const s of FLAGSHIP_SCENARIOS) {
      const prompt = s.buildPrompt('材料原文 XYZ')
      expect(prompt).toContain('材料原文 XYZ')
    }
    expect(scenarioFor('finance').buildPrompt('x')).toContain('不要提交')
    expect(scenarioFor('procurement').buildPrompt('x')).toContain('不要提交')
    expect(scenarioFor('hr').buildPrompt('x')).toContain('你不要代办')
    expect(scenarioFor('it').buildPrompt('x')).toContain('我确认后再提交')
  })

  it('今日摘要只带工作台上非零的数字', () => {
    const cards: HomeCard[] = [
      { key: 'approvals', label: '待我审批', value: 3, hint: '请假、报销', section: 'office', tone: 'warn' },
      { key: 'todos', label: '我的待办', value: 0, hint: '', section: 'todos', tone: 'normal' },
      { key: 'resp_week', label: '本周责任完成率', value: '—', hint: '', section: 'collab', tone: 'normal' },
    ]
    const prompt = dailyBriefPrompt(cards, '销售部')
    expect(prompt).toContain('销售部的负责人')
    expect(prompt).toContain('- 待我审批：3（请假、报销）')
    expect(prompt).not.toContain('我的待办')
    expect(prompt).not.toContain('本周责任完成率')
    expect(dailyBriefPrompt([], '销售部')).toContain('暂无需要处理的事项')
  })
})
