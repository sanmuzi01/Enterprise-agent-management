/** 每个部门一个“招牌场景”：用户粘贴原始材料（报销单据、故障描述、会议纪要……），一句话交给部门助手办到草稿为止。
 *
 * 办理步骤写在交给助手的话里，而不是只写在助手模板里：已经在用的部门助手，提示词是创建时从模板生成的，
 * 改模板不会同步过去；写在这里，新老助手都按同一套步骤办。每一步都只到“草稿 / 建议”为止，
 * 提交、审批、发起这类决定仍由用户在卡片或工作台上确认。
 */
import type { HomeCard } from '../api/enterpriseWorkspace'

export interface FlagshipScenario {
  /** 部门业务类型；null 是没有专属业务类型的部门（综合办公室、运营部等） */
  code: string | null
  title: string
  /** 一行说明这个场景会做哪几步 */
  steps: string[]
  placeholder: string
  buildPrompt: (input: string) => string
}

export const FLAGSHIP_SCENARIOS: FlagshipScenario[] = [
  {
    code: 'finance',
    title: '把报销材料办成报销单',
    steps: ['核对部门预算', '拆成费用明细', '起草报销单', '提示凭证风险'],
    placeholder: '粘贴报销材料，例如：10 月 8 日上海出差住宿 680 元，发票号 12345678；打车 86 元，没有发票',
    buildPrompt: (input) => [
      '请帮我把下面的报销材料办成报销草稿：',
      '1. 先查本部门报销预算余额；',
      '2. 按材料拆成费用明细（类别、金额、说明、发票号），缺发票号的明细逐条指出来；',
      '3. 创建报销草稿，不要提交——提交由我在卡片上确认；',
      '4. 如果超出预算余额，或属于业务招待、大额、没有发票这类审批后生成凭证时会被标为风险的情况，提前告诉我。',
      '', '材料：', input,
    ].join('\n'),
  },
  {
    code: 'it',
    title: '描述故障，先自助排查再报修',
    steps: ['查知识库', '给出排查步骤', '解决不了再起工单', '等你确认后提交'],
    placeholder: '描述遇到的问题，例如：打印机一直显示脱机，重启电脑也不行，急着打合同',
    buildPrompt: (input) => [
      `我遇到一个 IT 问题：${input}`,
      '请先查知识库，按顺序给我自助排查步骤（每步一句话）。',
      '如果这类问题需要 IT 人员处理（或者账号、权限、设备申请），复述工单的类型、优先级、标题和描述给我确认；我确认后再提交工单。',
    ].join('\n'),
  },
  {
    code: 'hr',
    title: '入转调离：先预检，再列办理清单',
    steps: ['预检冲突与遗留事项', '说明必须先处理的', '列出各方办理清单'],
    placeholder: '谁、办什么、哪天生效，例如：王小明 10 月 20 日入职销售部，岗位客户经理',
    buildPrompt: (input) => [
      input,
      '请先做入转调离预检，逐条说明发现的问题（重复或冲突的事项、名下设备、未结报销、待批请假、未休年假等），并标出哪些必须先处理；',
      '再列出发起后人事、IT、财务、部门负责人和员工本人各自要办理的事项，以及建议完成时间。',
      '发起和批准由我在工作台「人事办理」里操作，你不要代办。',
    ].join('\n'),
  },
  {
    code: 'sales',
    title: '会议纪要变成跟进记录和商机更新',
    steps: ['确认客户', '起草跟进记录', '建议商机更新', '等你确认'],
    placeholder: '粘贴和客户沟通的纪要，例如：今天和华东医美王总通话，对方确认采购 20 台，预算 50 万，下周三前要报价',
    buildPrompt: (input) => [
      '下面是我和客户沟通的纪要，请：',
      '1. 先确认是哪个客户（不确定就列出部门客户让我选），并查看客户摘要；',
      '2. 按纪要起草一条跟进记录（只写纪要里有的事实，不推测客户意向）；',
      '3. 如果纪要里提到阶段或金额变化，说明商机应该怎么更新，等我确认后再更新。',
      '', '纪要：', input,
    ].join('\n'),
  },
  {
    code: 'procurement',
    title: '采购需求 → 查库存和预算 → 采购申请',
    steps: ['查库存', '查部门预算', '建议采购量', '起草采购申请'],
    placeholder: '要买什么、多少、为什么，例如：A4 纸（SKU A4-80）20 箱，月底会议物料',
    buildPrompt: (input) => [
      `采购需求：${input}`,
      '请逐个查询物品库存（是否低于安全库存）和本部门采购预算，说明是否需要买、买多少合适；',
      '然后创建采购申请草稿，不要提交——提交由我在卡片上确认。缺 SKU 或数量就先问我。',
    ].join('\n'),
  },
  {
    code: null,
    title: '会议纪要变成责任计划',
    steps: ['提取谁做什么', '期限与验收标准', '标出缺什么', '生成草稿待发布'],
    placeholder: '粘贴会议纪要、聊天记录或通知',
    buildPrompt: (input) => [
      '请把下面的工作文本整理成责任计划草稿：只提取原文明确要某人去做的事，人名、期限、交付物、验收标准都要来自原文，缺的留空并告诉我还缺什么。',
      '', input,
    ].join('\n'),
  },
]

export function scenarioFor(departmentCode: string | null | undefined): FlagshipScenario {
  return FLAGSHIP_SCENARIOS.find((s) => s.code === (departmentCode ?? null)) || FLAGSHIP_SCENARIOS[FLAGSHIP_SCENARIOS.length - 1]
}

/** 负责人的“今日摘要”：把工作台上已经算好的数字原样交给助手，让它排出今天的处理顺序。
 * 数字是用户本来就能看到的，不额外开放数据；助手需要明细时再用自己的工具去查。 */
export function dailyBriefPrompt(cards: HomeCard[] | undefined | null, departmentName: string): string {
  const lines = (cards || [])
    .filter((c) => c.value !== '—' && c.value !== 0 && c.value !== '0')
    .map((c) => `- ${c.label}：${c.value}${c.hint ? `（${c.hint}）` : ''}`)
  return [
    `我是${departmentName}的负责人，以下是我今天工作台上的数字：`,
    ...(lines.length ? lines : ['- 暂无需要处理的事项']),
    '请据此生成今日摘要：先说最需要关注的三件事和原因，再给今天的处理顺序。',
    '需要明细时用你的工具查询（例如待我审批的单子、逾期的责任）；查不到的项目告诉我去工作台哪个分区处理。只使用上面的数字和查询结果，不要编造。',
  ].join('\n')
}
