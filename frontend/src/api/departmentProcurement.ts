import request from '../utils/request'

export interface PurchaseLineDto {
  sku: string
  productName: string
  quantity: number
  unitPrice: number
}

export interface PurchaseOrderDto {
  supplierCode: string | null
  supplierName: string | null
  status: string
}

/** 跟 Java `PurchaseRequestDto` 的字段一一对应（camelCase 原样传，Python 层不重新塑形）。 */
export interface PurchaseRequestDto {
  id: number
  requesterUserId: number
  teamId: number
  status: 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
  totalAmount: number
  lines: PurchaseLineDto[]
  approverUserId: number | null
  decisionNote: string | null
  createdAt: string
  submittedAt: string | null
  decidedAt: string | null
  purchaseOrder: PurchaseOrderDto | null
}

export async function getMyPurchaseRequests(): Promise<PurchaseRequestDto[]> {
  const { data } = await request.get('/enterprise/procurement/mine')
  return data as PurchaseRequestDto[]
}

export async function createMyPurchaseDraft(payload: {
  team_id: number
  lines: { sku: string; quantity: number }[]
}): Promise<PurchaseRequestDto> {
  const { data } = await request.post('/enterprise/procurement/mine', payload)
  return data as PurchaseRequestDto
}

export async function submitMyPurchaseRequest(requestId: number): Promise<PurchaseRequestDto> {
  const { data } = await request.post(`/enterprise/procurement/${requestId}/submit`)
  return data as PurchaseRequestDto
}

export async function getTeamPendingPurchaseRequests(teamId: number): Promise<PurchaseRequestDto[]> {
  const { data } = await request.get('/enterprise/procurement/team-pending', { params: { team_id: teamId } })
  return data as PurchaseRequestDto[]
}

export async function decidePurchaseRequest(
  requestId: number,
  payload: { team_id: number; action: 'approve' | 'reject'; note?: string },
): Promise<PurchaseRequestDto> {
  const { data } = await request.post(`/enterprise/procurement/${requestId}/decide`, payload)
  return data as PurchaseRequestDto
}
