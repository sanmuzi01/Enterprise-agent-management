import { describe, expect, it } from 'vitest'
import { canSelectForShelf, isOnShelf, notOnShelfIds, shelfStatus } from './skillShelf'

const skill = (over: Partial<{ id: number; is_public: number; lifecycle_status: string; config_file: string }> = {}) => ({
  id: 1, is_public: 1, lifecycle_status: 'draft', config_file: 'imported/x.yml', ...over,
})

describe('skillShelf', () => {
  it('公开但还是草稿：未上架，并说明原因（以前这种看起来像已上架）', () => {
    expect(isOnShelf(skill())).toBe(false)
    expect(shelfStatus(skill())).toEqual({ onShelf: false, text: '未上架：用户看不到（还没发布）' })
  })

  it('公开 + 已发布才算上架', () => {
    expect(shelfStatus(skill({ lifecycle_status: 'published' }))).toEqual({ onShelf: true, text: '已上架：用户的技能中心能看到' })
    expect(shelfStatus(skill({ is_public: 0, lifecycle_status: 'published' })).text).toBe('未上架：用户看不到（私有）')
    expect(shelfStatus(skill({ is_public: 0, lifecycle_status: 'retired' })).text).toBe('未上架：用户看不到（私有、已退役）')
  })

  it('部门助手的专属技能不能选、不算进“未上架”', () => {
    const own = skill({ id: 9, is_public: 0, lifecycle_status: 'published', config_file: 'enterprise/agent_10932.yml' })
    expect(canSelectForShelf(own)).toBe(false)
    expect(canSelectForShelf(skill({ config_file: 'enterprise\\agent_1.yml' }))).toBe(false)
    expect(shelfStatus(own).text).toBe('部门助手专属，不上架')
    expect(notOnShelfIds([own, skill({ id: 2 }), skill({ id: 3, lifecycle_status: 'published' })])).toEqual([2])
  })
})
