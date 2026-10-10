import { describe, expect, it } from 'vitest'
import {
  DIAGNOSE_CHECKS,
  TOOL_NAMES,
  diagnoseCheckLabel,
  environmentLabel,
  humanizeConfigText,
  humanizeLogMessage,
  taskModeLabel,
  departmentCodeLabel,
  documentStatusLabel,
  eventConsumerLabel,
  eventTypeLabel,
  hasModelDisplayName,
  messageRoleLabel,
  modelDisplayName,
  sourceTypeLabel,
  targetTypeLabel,
  taskTypeLabel,
  toolDisplayName,
} from './displayNames'

// 内部名字的样子：snake_case、camelCase、点分事件名、带下划线的大写码
const LOOKS_LIKE_CODE = /[a-z]+_[a-z0-9_]+|[a-z]+[A-Z][a-z]+|\w+\.\w+|[A-Z]{2,}_[A-Z_]+/

describe('displayNames：界面上不露出代码里的名字', () => {
  it('内置工具显示中文名', () => {
    expect(toolDisplayName('create_leave_draft')).toBe('创建请假草稿')
    expect(toolDisplayName('word_count')).toBe('字数统计')
    expect(toolDisplayName('external_agent')).toBe('外部智能体服务')
    expect(toolDisplayName('rag_search')).toBe('检索资料')
  })

  it('不认识的工具（用户自己配的企业接口、新增未登记的）显示“自定义工具”，绝不显示原名', () => {
    for (const unknown of ['query_order_status', 'someFutureTool', 'x.y']) {
      const label = toolDisplayName(unknown)
      expect(label).toBe('自定义工具')
      expect(label).not.toContain(unknown)
    }
    expect(toolDisplayName(null)).toBe('工具')
    expect(toolDisplayName('')).toBe('工具')
  })

  it('所有工具的中文名里都没有代码名', () => {
    for (const [name, label] of Object.entries(TOOL_NAMES)) {
      expect(label, name).not.toMatch(LOOKS_LIKE_CODE)
    }
  })

  it('模型：认识的显示中文名，用户自己填的模型名原样显示，没配置显示“未配置模型”', () => {
    expect(modelDisplayName('demo-offline')).toBe('离线演示模型（不联网）')
    expect(modelDisplayName('glm-4')).toBe('中文通用助手')
    expect(modelDisplayName('glm-4v')).toBe('图片识别助手')
    expect(modelDisplayName('my-company-model')).toBe('my-company-model')
    expect(modelDisplayName('')).toBe('未配置模型')
    expect(modelDisplayName(undefined)).toBe('未配置模型')
    expect(hasModelDisplayName('glm-4')).toBe(true)
    expect(hasModelDisplayName('my-company-model')).toBe(false)
  })

  it('任务、对象、来源、角色、状态、部门、事件：都有中文，未知值用通用说法而不是原样显示', () => {
    expect(taskTypeLabel('knowledge_index')).toBe('资料入库')
    expect(taskTypeLabel('some_new_task')).toBe('后台任务')
    expect(targetTypeLabel('space')).toBe('知识库空间')
    expect(targetTypeLabel('weird_type')).toBe('对象')
    expect(sourceTypeLabel('web')).toBe('网页抓取')
    expect(sourceTypeLabel('x_y')).toBe('其他来源')
    expect(messageRoleLabel('assistant')).toBe('助手')
    expect(messageRoleLabel('function_call')).toBe('其他')
    expect(documentStatusLabel('failed')).toBe('失败')
    expect(documentStatusLabel('mystery_state')).toBe('未知')
    expect(departmentCodeLabel('hr')).toBe('人事')
    expect(departmentCodeLabel('')).toBe('未设置业务类型')
    expect(eventConsumerLabel('batch_item_worker')).toBe('批量整理')
    expect(eventConsumerLabel('unknown_worker')).toBe('后台处理')
    expect(eventTypeLabel('issue.created')).toBe('新问题')
    expect(eventTypeLabel('some.new.event')).toBe('后台事件')
  })

  it('所有“未知值”的兜底说法本身也不像代码', () => {
    const fallbacks = [toolDisplayName('zz_unknown'), taskTypeLabel('zz_unknown'), targetTypeLabel('zz_unknown'), sourceTypeLabel('zz_unknown'),
      messageRoleLabel('zz_unknown'), documentStatusLabel('zz_unknown'), departmentCodeLabel('zz_unknown'), eventConsumerLabel('zz_unknown'), eventTypeLabel('zz_unknown')]
    for (const label of fallbacks) expect(label).not.toMatch(LOOKS_LIKE_CODE)
  })
})

describe('系统诊断页：配置项名字换成中文', () => {
  it('检查项标题是中文，不认识的用“配置检查”', () => {
    expect(diagnoseCheckLabel('JWT_SECRET_KEY')).toBe('登录签名密钥')
    expect(diagnoseCheckLabel('NEW_UNKNOWN_SETTING')).toBe('配置检查')
    expect(diagnoseCheckLabel(undefined)).toBe('配置检查')
    for (const [name, label] of Object.entries(DIAGNOSE_CHECKS)) expect(label, name).not.toMatch(LOOKS_LIKE_CODE)
  })

  it('说明文字里夹着的配置项名字也换成中文，长名字不会被短名字截断', () => {
    expect(humanizeConfigText('不能跟 DB_PASSWORD 相同')).toBe('不能跟 数据库密码 相同')
    expect(humanizeConfigText('ENTERPRISE_DB_USER 不能是 root')).toBe('业务库账号 不能是 root')
    expect(humanizeConfigText('POST /auth/token 已开启')).toBe('脚本令牌接口 已开启')
    expect(humanizeConfigText('由 alembic 管理')).toBe('由 数据库迁移工具 管理')
    expect(humanizeConfigText(null)).toBe('')
  })

  it('环境、任务模式、日志里的请求编号', () => {
    expect(environmentLabel('production')).toBe('生产环境')
    expect(environmentLabel('weird')).toBe('未知环境')
    expect(taskModeLabel('worker')).toBe('独立 Worker')
    expect(taskModeLabel('whatever')).toBe('未设置')
    expect(humanizeLogMessage('boom request_id=abc123')).toBe('boom 请求编号 abc123')
  })
})
