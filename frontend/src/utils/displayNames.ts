// 页面上显示给人看的名字，统一从这里取。
// 原则：界面上不出现代码里的名字（函数名、工具名、枚举值、模型标识、部门代码、事件名……）。
// 认识的换成中文；不认识的，用一个通用的中文说法，而不是把内部名字原样露出来。
// 例外：用户自己配置的模型名（比如自己填的第三方模型）原样显示——那是用户自己输入的产品名。
// tests/test_frontend_display_names.py 会核对：后端每注册一个内置工具，这里都必须有对应的中文名。

export const MODEL_NAMES: Record<string, string> = {
  'glm-4': '中文通用助手',
  'glm-4-flash': '轻量快速助手',
  'glm-4-plus': '复杂任务助手',
  'glm-4v': '图片识别助手',
  'deepseek-chat': 'DeepSeek 问答',
  'deepseek-reasoner': 'DeepSeek 推理',
  'deepseek-coder': 'DeepSeek 编程',
  'gpt-4o': 'OpenAI 高能力助手',
  'gpt-4o-mini': 'OpenAI 轻量助手',
  'o3-mini': 'OpenAI 推理助手',
  'o4-mini': 'OpenAI 新一代轻量推理',
  'kimi-k2-0711-preview': 'Kimi K2 预览',
  'kimi-latest': 'Kimi 最新稳定入口',
  'qwen-plus': '通义千问均衡助手',
  'qwen-turbo': '通义千问快速助手',
  'qwen-max': '通义千问高能力助手',
  'qwen-long': '通义千问长文本助手',
  sonar: 'Perplexity 联网检索',
  'sonar-pro': 'Perplexity 联网检索（增强）',
  'gpt-4o-search-preview': 'OpenAI 联网检索',
  'gpt-4o-mini-search-preview': 'OpenAI 轻量联网检索',
  'embedding-3': '中文资料读取',
  'embedding-2': '兼容资料读取',
  'text-embedding-3-small': 'OpenAI 轻量资料读取',
  'text-embedding-3-large': 'OpenAI 高精度资料读取',
  'text-embedding-ada-002': 'OpenAI 旧版资料读取',
  'BAAI/bge-small-zh-v1.5': '本地中文轻量资料读取',
  'BAAI/bge-base-zh-v1.5': '本地中文标准资料读取',
  'BAAI/bge-large-zh-v1.5': '本地中文高精度资料读取',
  'demo-offline': '离线演示模型（不联网）',
}

/** 模型的显示名：认识的给中文名，用户自己填的模型名原样显示。 */
export function modelDisplayName(name?: string | null): string {
  if (!name) return '未配置模型'
  return MODEL_NAMES[name] || name
}

/** 模型有没有现成的中文名（没有就是用户自己配置的模型）。 */
export function hasModelDisplayName(name?: string | null): boolean {
  return !!name && name in MODEL_NAMES
}

// 内置工具的中文名。后端每新增一个内置工具，这里要同步加一行（有测试守着）。
export const TOOL_NAMES: Record<string, string> = {
  accept_responsibility: '接受责任',
  add_it_ticket_comment: '添加 IT 工单备注',
  approve_expense_claim: '批准报销',
  approve_leave_request: '批准请假',
  approve_purchase_request: '批准采购申请',
  calculator: '计算器',
  chart_generator: '生成图表',
  create_expense_draft: '创建报销草稿',
  create_followup_draft: '创建客户跟进草稿',
  create_it_ticket: '提交 IT 工单',
  create_leave_draft: '创建请假草稿',
  create_or_update_opportunity: '创建或更新商机',
  create_purchase_draft: '创建采购草稿',
  datetime_calculator: '日期时间计算',
  extract_responsibility_plan: '整理责任计划',
  generate_voucher_draft: '生成记账凭证草稿',
  get_attendance_summary: '查看考勤异常汇总',
  get_customer_summary: '查询客户摘要',
  get_department_budget: '查询采购预算',
  get_department_responsibility_risks: '查看履责风险',
  get_expense_budget: '查询报销预算',
  get_expense_status: '查询报销状态',
  get_hr_case: '查看人事事项',
  get_hr_summary: '查看人事汇总',
  get_inventory_status: '查询库存',
  get_it_desk_summary: '查看服务台汇总',
  get_it_ticket_detail: '查看 IT 工单详情',
  get_it_ticket_status: '查看 IT 工单进度',
  get_leave_balance: '查询请假余额',
  get_leave_status: '查询请假状态',
  get_my_attendance_anomalies: '查看我的考勤异常',
  get_my_devices: '查看我的设备',
  get_my_expense_claims: '查看我的报销',
  get_my_hr_tasks: '查看我的人事任务',
  get_my_it_tickets: '查看我的 IT 工单',
  get_my_leave_requests: '查看我的请假',
  get_my_purchase_requests: '查看我的采购申请',
  get_opportunities: '查询客户商机',
  get_purchase_status: '查询采购状态',
  get_responsibility_detail: '查看责任详情',
  get_responsibility_weekly_summary: '查看责任周报',
  get_team_pending_expense_claims: '查看待审批报销',
  get_team_pending_leave_requests: '查看待审批请假',
  get_team_pending_purchase_requests: '查看待审批采购',
  get_voucher_detail: '查看凭证详情',
  get_voucher_monthly_summary: '查看凭证月度汇总',
  list_hr_cases: '列出人事事项',
  list_it_devices: '查看设备台账',
  list_it_queue: '查看工单队列',
  list_my_responsibilities: '查看我的责任事项',
  list_pending_acceptance: '查看待接受的责任',
  list_pending_verification: '查看待验收的责任',
  list_pending_vouchers: '查看待核对凭证',
  list_team_customers: '查看部门客户',
  outline_generator: '生成大纲',
  precheck_hr_case: '人事事项预检查',
  raise_responsibility_objection: '对责任提出异议',
  reject_expense_claim: '拒绝报销',
  reject_leave_request: '拒绝请假',
  reject_purchase_request: '拒绝采购申请',
  report_responsibility_blocker: '报告责任受阻',
  report_responsibility_progress: '报告责任进度',
  request_rework: '退回修改',
  run_skill_script: '运行技能脚本',
  search_it_solutions: '查找 IT 自助方案',
  submit_customer_followup: '确认客户跟进',
  submit_deliverable: '提交责任成果',
  submit_expense_claim: '提交报销',
  submit_leave_request: '提交请假',
  submit_purchase_request: '提交采购申请',
  unit_converter: '单位换算',
  verify_deliverable: '验收通过',
  word_count: '字数统计',
}

// 运行轨迹里平台自己写入的步骤名（不是可注册的工具）
const STEP_NAMES: Record<string, string> = {
  rag_search: '检索资料',
  external_agent: '外部智能体服务',
}

/** 工具的显示名。用户自己配置的企业接口工具、以后新增还没来得及加中文名的工具，统一显示成“自定义工具”，不露出代码里的名字。 */
export function toolDisplayName(name?: string | null): string {
  if (!name) return '工具'
  return TOOL_NAMES[name] || STEP_NAMES[name] || '自定义工具'
}

const TASK_TYPES: Record<string, string> = {
  knowledge_index: '资料入库',
  knowledge_reindex: '重建资料',
}
export function taskTypeLabel(type?: string | null): string {
  return (type && TASK_TYPES[type]) || '后台任务'
}

const TARGET_TYPES: Record<string, string> = {
  space: '知识库空间',
  plan: '责任计划',
  knowledge: '资料',
  member: '成员',
  user: '用户',
  document: '文档',
}
export function targetTypeLabel(type?: string | null): string {
  return (type && TARGET_TYPES[type]) || '对象'
}

const SOURCE_TYPES: Record<string, string> = { upload: '上传', web: '网页抓取' }
export function sourceTypeLabel(type?: string | null): string {
  return (type && SOURCE_TYPES[type]) || '其他来源'
}

const MESSAGE_ROLES: Record<string, string> = { system: '系统', user: '用户', assistant: '助手', tool: '工具' }
export function messageRoleLabel(role?: string | null): string {
  return (role && MESSAGE_ROLES[role]) || '其他'
}

const DOCUMENT_STATUSES: Record<string, string> = { pending: '待处理', processing: '处理中', done: '已完成', failed: '失败' }
export function documentStatusLabel(status?: string | null): string {
  return (status && DOCUMENT_STATUSES[status]) || '未知'
}

const DEPARTMENT_CODES: Record<string, string> = { hr: '人事', procurement: '采购', sales: '销售', finance: '财务', it: 'IT' }
export function departmentCodeLabel(code?: string | null): string {
  return (code && DEPARTMENT_CODES[code]) || '未设置业务类型'
}

const EVENT_CONSUMERS: Record<string, string> = { batch_item_worker: '批量整理', issue_notifier: '问题通知' }
export function eventConsumerLabel(name?: string | null): string {
  return (name && EVENT_CONSUMERS[name]) || '后台处理'
}

const EVENT_TYPES: Record<string, string> = {
  'batch.item.queued': '批量整理任务入队',
  'agent.run.failed': '助手运行失败',
  'issue.created': '新问题',
  'issue.regressed': '问题复发',
}
export function eventTypeLabel(type?: string | null): string {
  return (type && EVENT_TYPES[type]) || '后台事件'
}

// 系统诊断页里每个检查项的名字。后端的检查项用配置项名字（DB_PASSWORD、JWT_SECRET_KEY……）当标识，直接显示给人看就是露出代码里的名字。
// 页面上显示中文，原始标识只放在鼠标悬停的提示里。tests/test_frontend_display_names.py 会核对：后端能产生的每个检查项这里都有中文名。
export const DIAGNOSE_CHECKS: Record<string, string> = {
  DB_USER: '数据库用户',
  DB_PASSWORD: '数据库密码',
  DB_HOST: '数据库地址',
  DB_PORT: '数据库端口',
  DB_NAME: '数据库名称',
  MYSQL_ROOT_PASSWORD: '数据库管理密码',
  AUDIT_DB_USER: '审计账号',
  AUDIT_DB_PASSWORD: '审计账号密码',
  ENTERPRISE_DB_USER: '业务库账号',
  ENTERPRISE_DB_PASSWORD: '业务库密码',
  ENTERPRISE_HUB_HMAC_SECRET: '业务系统签名密钥',
  DB_USER_NOT_ROOT: '数据库用户权限',
  AUDIT_DB_USER_NOT_ROOT: '审计账号权限',
  ENTERPRISE_DB_USER_NOT_ROOT: '业务库账号权限',
  DB_AUTO_BOOTSTRAP: '自动建表开关',
  JWT_SECRET_KEY: '登录签名密钥',
  LLM_ENCRYPTION_KEY: '模型密钥加密',
  LLM_ENCRYPTION_KEY_FORMAT: '模型密钥加密格式',
  REDIS_URL: 'Redis 缓存',
  REDIS_PASSWORD: 'Redis 口令',
  SMS_PROVIDER: '短信服务',
  SMS_WEBHOOK_URL: '短信接口地址',
  SMS_WEBHOOK_TOKEN: '短信接口令牌',
  SMS_EXPOSE_DEV_CODE: '验证码展示开关',
  ALIBABA_CLOUD_ACCESS_KEY_ID: '阿里云访问账号',
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: '阿里云访问密钥',
  SMS_ALIYUN_SIGN_NAME: '短信签名',
  SMS_ALIYUN_TEMPLATE_CODE: '短信模板',
  TRUSTED_HOSTS: '允许访问的域名',
  CORS_ALLOW_ORIGINS: '允许的前端来源',
  FORWARDED_ALLOW_IPS: '可信代理地址',
  ADMIN_PASSWORD: '管理员初始密码',
  SESSION_COOKIE_SECURE: '登录 Cookie 安全设置',
  AUTH_TOKEN_ENDPOINT_ENABLED: '脚本令牌接口',
  redis: 'Redis 缓存',
  sms: '短信服务',
  database: '数据库连接',
  static: '静态文件目录',
  knowledge_files: '资料文件目录',
  skills: '技能目录',
  vector_db: '向量库目录',
}

export function diagnoseCheckLabel(name?: string | null): string {
  return (name && DIAGNOSE_CHECKS[name]) || '配置检查'
}

/** 日志里的错误信息：后端拼接的 request_id=xxx 改成“请求编号 xxx”。 */
export function humanizeLogMessage(message?: string | null): string {
  return (message || '').replace(/request_id=/g, '请求编号 ')
}

// 后端写的说明文字里会夹着配置项的名字（比如“不能跟 DB_PASSWORD 相同”）。页面上统一换成中文说法。
const CONFIG_NAME_PATTERN = new RegExp(
  `(?<![A-Za-z0-9_])(${Object.keys(DIAGNOSE_CHECKS).filter((key) => key === key.toUpperCase()).sort((a, b) => b.length - a.length).join('|')})(?![A-Za-z0-9_])`,
  'g',
)

export function humanizeConfigText(text?: string | null): string {
  return (text || '')
    .replace(CONFIG_NAME_PATTERN, (name) => DIAGNOSE_CHECKS[name] || name)
    .replace(/POST \/auth\/token/g, '脚本令牌接口')
    .replace(/\balembic\b/gi, '数据库迁移工具')
    .replace(/Fernet key/g, '加密密钥')
}

export function environmentLabel(env?: string | null): string {
  if (env === 'production') return '生产环境'
  if (env === 'development') return '开发环境'
  return '未知环境'
}

export function taskModeLabel(mode?: string | null): string {
  if (mode === 'worker') return '独立 Worker'
  if (mode === 'fastapi' || mode === 'inline' || mode === 'background') return '随接口执行'
  return '未设置'
}

const DIRECTORY_CHECKS = new Set(['static', 'knowledge_files', 'skills', 'vector_db'])

/** 诊断项的说明：目录类检查后端返回的是服务器上的完整路径，页面上只说“存在 / 缺失”。 */
export function diagnoseMessage(item: { name?: string | null; ok?: boolean; message?: string | null }): string {
  if (item.name && DIRECTORY_CHECKS.has(item.name)) return item.ok ? '目录存在' : '目录缺失'
  return humanizeConfigText(item.message)
}
