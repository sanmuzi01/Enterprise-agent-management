/** 技能“上架”状态：用户的技能中心只显示 公开 + 已发布 的技能，两个条件缺一不可。
 * 后台以前分开显示“公开 / 私有”和“草稿 / 已发布”两个标签，“公开的草稿”看起来像已经上架、其实用户看不到。
 * 这里把它合成一句人话，并决定哪些技能能被批量上架（规则和后端 service/skills_core/shelf.py 一致）。 */

export interface ShelfSkill {
  id: number
  is_public: number
  lifecycle_status?: string
  config_file?: string
}

/** 部门助手的专属技能（skills/enterprise/agent_<id>.yml），只给那个助手用，不能上架 */
export function isAgentPrivate(skill: ShelfSkill): boolean {
  return (skill.config_file || '').replace(/\\/g, '/').startsWith('enterprise/agent_')
}

export function isOnShelf(skill: ShelfSkill): boolean {
  return skill.is_public === 1 && skill.lifecycle_status === 'published'
}

/** 卡片上的一句话：用户能不能在技能中心看到它，看不到是因为什么 */
export function shelfStatus(skill: ShelfSkill): { onShelf: boolean; text: string } {
  if (isAgentPrivate(skill)) return { onShelf: false, text: '部门助手专属，不上架' }
  if (isOnShelf(skill)) return { onShelf: true, text: '已上架：用户的技能中心能看到' }
  const reasons: string[] = []
  if (skill.is_public !== 1) reasons.push('私有')
  if (skill.lifecycle_status !== 'published') reasons.push(skill.lifecycle_status === 'retired' ? '已退役' : '还没发布')
  return { onShelf: false, text: `未上架：用户看不到（${reasons.join('、')}）` }
}

/** 能被勾选做批量操作的技能 */
export function canSelectForShelf(skill: ShelfSkill): boolean {
  return !isAgentPrivate(skill)
}

/** “选中所有未上架的” */
export function notOnShelfIds(skills: ShelfSkill[]): number[] {
  return skills.filter((s) => canSelectForShelf(s) && !isOnShelf(s)).map((s) => s.id)
}
