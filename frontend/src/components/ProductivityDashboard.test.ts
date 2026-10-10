import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ProductivityDashboard from './ProductivityDashboard.vue'
import type { ProductivityDashboard as DashboardData } from '../api/productivity'

function data(): DashboardData {
  return {
    days: 30,
    totals: {
      items: 4, active_users: 2, estimated_saved_minutes: 135, measured_minutes: 12,
      reported_saved_minutes: 20, reported_count: 1, drafts: 4, adopted: 3,
      adopted_as_is: 2, initiated: 3, completed: 2, runs: 10, failed_runs: 1,
    },
    rates: { adoption: 0.75, as_is: 2 / 3, completion: 2 / 3, failure: 0.1 },
    by_team: [{ id: 1, name: '采购部', items: 4, saved_minutes: 135, adopted: 3, drafts: 4,
      runs: 10, failed_runs: 1, adoption_rate: 0.75, failure_rate: 0.1 }],
    by_agent: [], by_source: [],
    trend: [{ day: '2026-10-10', items: 4, saved_minutes: 135, runs: 10, failed_runs: 1 }],
    failure_reasons: [{ code: 'MODEL_TIMEOUT', count: 1 }],
    changed_fields: [{ field: 'amount', count: 2 }],
  }
}

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

describe('ProductivityDashboard', () => {
  it('loads the default range and renders the three separate time measures', async () => {
    const load = vi.fn().mockResolvedValue(data())
    wrapper = mount(ProductivityDashboard, { props: { load } })
    await flushPromises()

    expect(load).toHaveBeenCalledWith(30)
    expect(wrapper.text()).toContain('2.3 小时')
    expect(wrapper.text()).toContain('12 分钟')
    expect(wrapper.text()).toContain('20 分钟')
    expect(wrapper.text()).toContain('采购部')
    expect(wrapper.text()).toContain('模型响应超时')
    expect(wrapper.text()).toContain('金额')
  })

  it('reloads when the range changes', async () => {
    const load = vi.fn().mockResolvedValue(data())
    wrapper = mount(ProductivityDashboard, { props: { load } })
    await flushPromises()
    await wrapper.get('[data-testid="productivity-days"]').setValue('7')
    await flushPromises()
    expect(load).toHaveBeenLastCalledWith(7)
  })

  it('shows a useful error without discarding an already loaded dashboard', async () => {
    const load = vi.fn().mockResolvedValueOnce(data()).mockRejectedValueOnce(new Error('network down'))
    wrapper = mount(ProductivityDashboard, { props: { load } })
    await flushPromises()
    await wrapper.get('[data-testid="productivity-days"]').setValue('90')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('加载失败')
    expect(wrapper.find('[data-testid="productivity-time"]').exists()).toBe(true)
  })
})
