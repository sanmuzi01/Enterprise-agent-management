import request from '../utils/request'

export type TicketCategory = 'INCIDENT' | 'ACCOUNT' | 'PERMISSION' | 'DEVICE' | 'OTHER'
export type TicketPriority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT'
export type TicketStatus =
  | 'PENDING_APPROVAL' | 'OPEN' | 'IN_PROGRESS' | 'WAITING_USER' | 'RESOLVED' | 'CLOSED' | 'CANCELLED' | 'REJECTED'
export type SlaStatus = 'OK' | 'AT_RISK' | 'BREACHED' | 'MET' | 'PAUSED' | 'NONE'

export interface TicketSummary {
  id: number
  requesterUserId: number
  requesterName: string | null
  teamId: number
  teamName: string | null
  category: TicketCategory
  categoryLabel: string
  priority: TicketPriority
  priorityLabel: string
  title: string
  status: TicketStatus
  statusLabel: string
  assigneeUserId: number | null
  assigneeName: string | null
  createdAt: string
  updatedAt: string
  slaDueAt: string
  slaStatus: SlaStatus
}

export interface TicketComment {
  id: number
  authorUserId: number
  authorName: string | null
  internal: boolean
  kind: 'COMMENT' | 'STATUS' | 'SYSTEM'
  body: string
  createdAt: string
}

export interface TicketDetail extends TicketSummary {
  description: string
  approverUserId: number | null
  approverName: string | null
  decisionNote: string | null
  suggestedCategory: TicketCategory | null
  suggestedPriority: TicketPriority | null
  classifyReason: string | null
  firstResponseAt: string | null
  resolvedAt: string | null
  resolution: string | null
  closedAt: string | null
  reopenCount: number
  comments: TicketComment[]
}

export interface Suggestion {
  classification: { category: TicketCategory; categoryLabel: string; priority: TicketPriority; priorityLabel: string; reasons: string[] }
  articles: { id: number; title: string; steps: string; score: number }[]
}

export interface DeviceEvent {
  eventType: string
  actorName: string | null
  subjectName: string | null
  ticketId: number | null
  note: string | null
  createdAt: string
}

export interface Device {
  id: number
  assetNo: string
  deviceType: string
  model: string
  status: 'IN_STOCK' | 'ASSIGNED' | 'REPAIR' | 'RETIRED'
  assigneeUserId: number | null
  assigneeName: string | null
  purchasedOn: string | null
  warrantyUntil: string | null
  warrantyStatus: 'OK' | 'EXPIRING' | 'EXPIRED' | 'UNKNOWN'
  note: string | null
  events: DeviceEvent[]
}

export interface DeskSummary {
  days: number
  byStatus: Record<TicketStatus, number>
  byCategory: Record<TicketCategory, number>
  unassigned: number
  overdue: number
  load: { userId: number; userName: string | null; open: number }[]
  effect: { resolved: number; slaMetRate: number | null; avgResolveHours: number | null; avgFirstResponseHours: number | null; reopenedTickets: number }
  narrative: string
}

const BASE = '/enterprise/it'

// ---------------- 员工侧 ----------------

export async function createTicket(payload: {
  team_id: number; category: TicketCategory; priority?: TicketPriority; title: string; description: string
}): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets`, payload)
  return data as TicketDetail
}

export async function getMyTickets(): Promise<TicketSummary[]> {
  const { data } = await request.get(`${BASE}/tickets/mine`)
  return data as TicketSummary[]
}

export async function getTicket(ticketId: number, teamId?: number): Promise<TicketDetail> {
  const { data } = await request.get(`${BASE}/tickets/${ticketId}`, { params: { team_id: teamId } })
  return data as TicketDetail
}

export async function cancelTicket(ticketId: number): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets/${ticketId}/cancel`)
  return data as TicketDetail
}

export async function commentTicket(ticketId: number, body: string): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets/${ticketId}/comments`, { body })
  return data as TicketDetail
}

export async function confirmTicket(ticketId: number): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets/${ticketId}/confirm`)
  return data as TicketDetail
}

export async function reopenTicket(ticketId: number, reason: string): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets/${ticketId}/reopen`, { reason })
  return data as TicketDetail
}

export async function getTeamPendingTickets(teamId: number): Promise<TicketSummary[]> {
  // 普通员工没有审批权限（403）是正常状态，由调用方隐藏区块，不弹全局错误提示
  const { data } = await request.get(`${BASE}/tickets/team-pending`, { params: { team_id: teamId }, skipErrorToast: true })
  return data as TicketSummary[]
}

export async function decideTicket(ticketId: number, payload: { team_id: number; action: 'approve' | 'reject'; note?: string }): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/tickets/${ticketId}/decide`, payload)
  return data as TicketDetail
}

export async function suggestSolutions(text: string, teamId: number): Promise<Suggestion> {
  const { data } = await request.get(`${BASE}/suggest`, { params: { text, team_id: teamId }, skipErrorToast: true })
  return data as Suggestion
}

export async function getMyDevices(): Promise<Device[]> {
  const { data } = await request.get(`${BASE}/devices/mine`, { skipErrorToast: true })
  return data as Device[]
}

// ---------------- IT 台 ----------------

export async function deskTickets(teamId: number, params: { status?: string; assignee?: string; overdue?: boolean }): Promise<TicketSummary[]> {
  const { data } = await request.get(`${BASE}/desk/tickets`, { params: { team_id: teamId, ...params } })
  return data as TicketSummary[]
}

export async function deskTicket(teamId: number, ticketId: number): Promise<TicketDetail> {
  const { data } = await request.get(`${BASE}/desk/tickets/${ticketId}`, { params: { team_id: teamId } })
  return data as TicketDetail
}

export async function deskSummary(teamId: number, days = 30): Promise<DeskSummary> {
  const { data } = await request.get(`${BASE}/desk/summary`, { params: { team_id: teamId, days } })
  return data as DeskSummary
}

export async function deskStaff(teamId: number): Promise<{ id: number; name: string }[]> {
  const { data } = await request.get(`${BASE}/desk/staff`, { params: { team_id: teamId } })
  return data as { id: number; name: string }[]
}

export async function deskAssign(teamId: number, ticketId: number, payload: { assignee_user_id?: number | null; take?: boolean }): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/desk/tickets/${ticketId}/assign`, { team_id: teamId, ...payload })
  return data as TicketDetail
}

export async function deskStatus(teamId: number, ticketId: number, status: 'IN_PROGRESS' | 'WAITING_USER', note?: string): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/desk/tickets/${ticketId}/status`, { team_id: teamId, status, note })
  return data as TicketDetail
}

export async function deskResolve(teamId: number, ticketId: number, resolution: string): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/desk/tickets/${ticketId}/resolve`, { team_id: teamId, resolution })
  return data as TicketDetail
}

export async function deskComment(teamId: number, ticketId: number, body: string, internal: boolean): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/desk/tickets/${ticketId}/comments`, { team_id: teamId, body, internal })
  return data as TicketDetail
}

export async function deskReclassify(
  teamId: number, ticketId: number, payload: { category: TicketCategory; priority: TicketPriority; reason: string },
): Promise<TicketDetail> {
  const { data } = await request.post(`${BASE}/desk/tickets/${ticketId}/reclassify`, { team_id: teamId, ...payload })
  return data as TicketDetail
}

export async function deskDevices(teamId: number, status?: string): Promise<Device[]> {
  const { data } = await request.get(`${BASE}/desk/devices`, { params: { team_id: teamId, status } })
  return data as Device[]
}

export async function deskDevice(teamId: number, deviceId: number): Promise<Device> {
  const { data } = await request.get(`${BASE}/desk/devices/${deviceId}`, { params: { team_id: teamId } })
  return data as Device
}

export async function deskCreateDevice(teamId: number, payload: {
  asset_no: string; device_type: string; model: string; purchased_on?: string; warranty_until?: string; note?: string
}): Promise<Device> {
  const { data } = await request.post(`${BASE}/desk/devices`, { team_id: teamId, ...payload })
  return data as Device
}

export async function deskAssignDevice(teamId: number, deviceId: number, payload: { user_id: number; ticket_id?: number; note?: string }): Promise<Device> {
  const { data } = await request.post(`${BASE}/desk/devices/${deviceId}/assign`, { team_id: teamId, ...payload })
  return data as Device
}

export async function deskDeviceAction(teamId: number, deviceId: number, action: 'return' | 'repair' | 'repair-done' | 'retire', note?: string): Promise<Device> {
  const { data } = await request.post(`${BASE}/desk/devices/${deviceId}/${action}`, { team_id: teamId, note })
  return data as Device
}
