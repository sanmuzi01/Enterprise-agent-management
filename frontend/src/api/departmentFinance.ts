import request from '../utils/request'

export interface ExpenseLineDto {
  category: string
  amount: number
  description: string | null
  invoiceNo: string | null
}

/** 跟 Java `ExpenseClaimDto` 的字段一一对应（camelCase 原样传，Python 层不重新塑形）。 */
export interface ExpenseClaimDto {
  id: number
  applicantUserId: number
  teamId: number
  status: 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
  totalAmount: number
  lines: ExpenseLineDto[]
  approverUserId: number | null
  decisionNote: string | null
  createdAt: string
  submittedAt: string | null
  decidedAt: string | null
}

export async function getMyExpenseClaims(): Promise<ExpenseClaimDto[]> {
  const { data } = await request.get('/enterprise/finance/mine')
  return data as ExpenseClaimDto[]
}

export async function createMyExpenseDraft(payload: {
  team_id: number
  lines: { category: string; amount: number; description?: string; invoice_no?: string }[]
}): Promise<ExpenseClaimDto> {
  const { data } = await request.post('/enterprise/finance/mine', payload)
  return data as ExpenseClaimDto
}

export async function submitMyExpenseClaim(requestId: number): Promise<ExpenseClaimDto> {
  const { data } = await request.post(`/enterprise/finance/${requestId}/submit`)
  return data as ExpenseClaimDto
}

export async function getTeamPendingExpenseClaims(teamId: number): Promise<ExpenseClaimDto[]> {
  const { data } = await request.get('/enterprise/finance/team-pending', { params: { team_id: teamId } })
  return data as ExpenseClaimDto[]
}

export async function decideExpenseClaim(
  requestId: number,
  payload: { team_id: number; action: 'approve' | 'reject'; note?: string },
): Promise<ExpenseClaimDto> {
  const { data } = await request.post(`/enterprise/finance/${requestId}/decide`, payload)
  return data as ExpenseClaimDto
}
