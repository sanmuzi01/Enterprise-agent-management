import request from '../utils/request'

export interface ContactDto {
  name: string
  title: string | null
  phone: string | null
  email: string | null
}

export interface FollowUpDto {
  id: number
  customerId: number
  content: string
  status: 'DRAFT' | 'CONFIRMED'
  createdAt: string
  confirmedAt: string | null
}

export interface OpportunityDto {
  id: number
  customerId: number
  stage: string
  amount: number
  ownerUserId: number
  teamId: number
  createdAt: string
  updatedAt: string
  expectedCloseDate?: string | null
  nextStep?: string | null
}

export interface CustomerDto {
  id: number
  name: string
  industry: string | null
  ownerUserId: number
  teamId: number
  createdAt: string
}

export interface CustomerSummaryDto extends CustomerDto {
  contacts: ContactDto[]
  recentFollowUps: FollowUpDto[]
  opportunities: OpportunityDto[]
}

export async function getTeamCustomers(teamId: number): Promise<CustomerDto[]> {
  const { data } = await request.get('/enterprise/crm/customers', { params: { team_id: teamId } })
  return data as CustomerDto[]
}

export async function getCustomerSummary(teamId: number, customerId: number): Promise<CustomerSummaryDto> {
  const { data } = await request.get(`/enterprise/crm/customers/${customerId}`, { params: { team_id: teamId } })
  return data as CustomerSummaryDto
}

export async function createFollowupDraft(
  teamId: number, customerId: number, content: string,
): Promise<FollowUpDto> {
  const { data } = await request.post(`/enterprise/crm/customers/${customerId}/followups`, {
    team_id: teamId, content,
  })
  return data as FollowUpDto
}

export async function confirmFollowup(followupId: number): Promise<FollowUpDto> {
  const { data } = await request.post(`/enterprise/crm/followups/${followupId}/confirm`)
  return data as FollowUpDto
}

export async function upsertOpportunity(
  teamId: number, customerId: number,
  payload: { opportunityId?: number; stage: string; amount: number; expectedCloseDate?: string; nextStep?: string },
): Promise<OpportunityDto> {
  const { data } = await request.post(`/enterprise/crm/customers/${customerId}/opportunities`, {
    team_id: teamId, opportunity_id: payload.opportunityId, stage: payload.stage, amount: payload.amount,
    expected_close_date: payload.expectedCloseDate || null, next_step: payload.nextStep ?? null,
  })
  return data as OpportunityDto
}
