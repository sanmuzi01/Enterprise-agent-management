/** 考勤：一次导入多个文件（几个打卡点、几个月）、弹性上班规则。考勤接口用替身。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/attendance', () => ({
  getMe: vi.fn(), listAnomalies: vi.fn(), listImports: vi.fn(), importFile: vi.fn(), listMembers: vi.fn(),
  getRules: vi.fn(), setRule: vi.fn(), getCalendar: vi.fn(),
}))

import * as api from '../api/attendance'
import type { ImportResult } from '../api/attendance'
import AttendanceModule from './AttendanceModule.vue'

const result = (over: Partial<ImportResult> = {}): ImportResult => ({
  import_id: 1, format: 'monthly_matrix', format_label: '月度汇总', period: ['2026-10-01', '2026-10-31'], rows: 3,
  matched_people: 2, matched_rows: 2, no_records: [], no_records_total: 0, punches: 40, new_punches: 40, duplicate_punches: 0,
  unmatched: [], skipped: [], skipped_total: 0, ...over,
} as ImportResult)
const httpError = (detail: string) => Object.assign(new Error(detail), { response: { status: 400, data: { detail } } })

let wrapper: VueWrapper
async function mountAs(tab: 'import' | 'rules') {
  wrapper = mount(AttendanceModule, { props: { teamId: 3 } })
  await flushPromises()
  ;(wrapper.vm as unknown as { setTab: (t: string) => void }).setTab(tab)
  await flushPromises()
  return wrapper
}
async function pick(...names: string[]) {
  const input = wrapper.get('[data-testid=att-file]')
  const files = names.map((n) => new File(['x'], n))
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
}

beforeEach(() => {
  vi.mocked(api.getMe).mockResolvedValue({ user_id: 1, is_hr: true, is_head: false, open_mine: 0, to_decide: 0 })
  vi.mocked(api.listAnomalies).mockResolvedValue([])
  vi.mocked(api.listImports).mockResolvedValue([])
  vi.mocked(api.listMembers).mockResolvedValue([{ user_id: 7, name: 'zhangsan' }])
})
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

describe('导入多个考勤文件', () => {
  it('逐个导入：每个文件各自的结果，一个文件不行不影响其他文件', async () => {
    await mountAs('import')
    vi.mocked(api.importFile)
      .mockResolvedValueOnce(result({ new_punches: 40 }))
      .mockRejectedValueOnce(httpError('这份月度汇总表里只有“正常 / 迟到”这类状态，没有打卡时间'))
      .mockResolvedValueOnce(result({ format_label: '逐条打卡', new_punches: 12 }))
    await pick('一号门.xlsx', '月报.xlsx', '二号门.csv')
    expect(wrapper.get('[data-testid=att-upload]').text()).toBe('导入 3 个文件')
    await wrapper.get('[data-testid=att-upload]').trigger('click')
    await flushPromises()

    expect(api.importFile).toHaveBeenCalledTimes(3)
    const text = wrapper.get('[data-testid=att-import-result]').text()
    expect(text).toContain('一号门.xlsx')
    expect(text).toContain('月度汇总格式')
    expect(text).toContain('月报.xlsx')
    expect(text).toContain('没有导入：这份月度汇总表里只有')
    expect(text).toContain('逐条打卡格式')
    expect(wrapper.text()).toContain('导入完成：2/3 个文件，新增 52 条打卡')
  })

  it('几个文件里对不上的名字合并成一份，选好后带着对应重新导入全部文件', async () => {
    await mountAs('import')
    vi.mocked(api.importFile)
      .mockResolvedValueOnce(result({ unmatched: [{ name: '张三', rows: 20 }] }))
      .mockResolvedValueOnce(result({ unmatched: [{ name: '张三', rows: 22 }, { name: '李四', rows: 3 }] }))
    await pick('9月.xlsx', '10月.xlsx')
    await wrapper.get('[data-testid=att-upload]').trigger('click')
    await flushPromises()
    const unmatched = wrapper.get('[data-testid=att-unmatched]')
    expect(unmatched.text()).toContain('2 个名字对不上')
    expect(unmatched.text()).toContain('张三（42 行）')

    vi.mocked(api.importFile).mockResolvedValue(result())
    await unmatched.get('select[aria-label="张三 对应的账号"]').setValue('7')
    await wrapper.get('[data-testid=att-reupload]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(api.importFile).mock.calls.slice(2)
    expect(calls).toHaveLength(2)
    expect(calls.every(([, , aliases]) => (aliases as Record<string, number>)['张三'] === 7)).toBe(true)
  })

  it('所有文件都失败：明确提示，不显示“导入完成”', async () => {
    await mountAs('import')
    vi.mocked(api.importFile).mockRejectedValue(httpError('考勤文件支持 .xlsx、.xls、.csv'))
    await pick('a.pdf', 'b.pdf')
    await wrapper.get('[data-testid=att-upload]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('所选文件都没有导入成功')
    expect(wrapper.text()).not.toContain('导入完成')
  })
})

describe('弹性上班规则', () => {
  it('显示并保存弹性分钟数（旧数据没有这个字段时按 0）', async () => {
    vi.mocked(api.getRules).mockResolvedValue({
      default: { team_id: null, work_start: '09:00', work_end: '18:00', grace_minutes: 5 },
      teams: [{ team_id: 3, work_start: '08:30', work_end: '17:30', grace_minutes: 5, flex_minutes: 60 }],
      org_teams: [{ id: 3, name: '研发部' }],
    })
    vi.mocked(api.getCalendar).mockResolvedValue([])
    vi.mocked(api.setRule).mockResolvedValue({ default: { team_id: null, work_start: '09:00', work_end: '18:00', grace_minutes: 5 }, teams: [], org_teams: [] })
    await mountAs('rules')
    const defaultFlex = wrapper.get('input[aria-label="企业默认弹性上班分钟"]')
    const teamFlex = wrapper.get('input[aria-label="研发部弹性上班分钟"]')
    expect((defaultFlex.element as HTMLInputElement).value).toBe('0')
    expect((teamFlex.element as HTMLInputElement).value).toBe('60')
    await teamFlex.setValue('90')
    await wrapper.get('[data-testid=att-rule-save-3]').trigger('click')
    await flushPromises()
    expect(api.setRule).toHaveBeenCalledWith(3, 3, '08:30', '17:30', 5, 90)
  })
})
