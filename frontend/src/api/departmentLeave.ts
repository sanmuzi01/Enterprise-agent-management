import request from '../utils/request'

/** 跟 Java `LeaveRequestDto` 的字段一一对应（camelCase 原样传，Python 层不重新塑形）。 */
export interface LeaveRequestDto {
  id: number
  applicantUserId: number
  teamId: number | null
  leaveTypeCode: string
  startDate: string
  endDate: string
  days: number
  reason: string | null
  status: 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
  approverUserId: number | null
  decisionNote: string | null
  createdAt: string
  submittedAt: string | null
  decidedAt: string | null
}

export async function getMyLeaveRequests(): Promise<LeaveRequestDto[]> {
  const { data } = await request.get('/enterprise/oa/leave/mine')
  return data as LeaveRequestDto[]
}

export async function createMyLeaveDraft(payload: {
  team_id: number
  leave_type_code: string
  start_date: string
  end_date: string
  reason?: string
}): Promise<LeaveRequestDto> {
  const { data } = await request.post('/enterprise/oa/leave/mine', payload)
  return data as LeaveRequestDto
}

export async function submitMyLeaveRequest(requestId: number): Promise<LeaveRequestDto> {
  const { data } = await request.post(`/enterprise/oa/leave/${requestId}/submit`)
  return data as LeaveRequestDto
}

export async function getTeamPendingLeaveRequests(teamId: number): Promise<LeaveRequestDto[]> {
  const { data } = await request.get('/enterprise/oa/leave/team-pending', {
    params: { team_id: teamId }, skipErrorToast: true,   // 普通员工没有审批权限（403）是正常状态，由调用方隐藏区块
  })
  return data as LeaveRequestDto[]
}

export async function decideLeaveRequest(
  requestId: number,
  payload: { team_id: number; action: 'approve' | 'reject'; note?: string },
): Promise<LeaveRequestDto> {
  const { data } = await request.post(`/enterprise/oa/leave/${requestId}/decide`, payload)
  return data as LeaveRequestDto
}
