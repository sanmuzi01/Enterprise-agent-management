import request from '../utils/request'

export type CaseType = 'ONBOARDING' | 'PROBATION' | 'TRANSFER' | 'OFFBOARDING'
export type CaseStatus = 'PENDING_APPROVAL' | 'IN_PROGRESS' | 'COMPLETED' | 'REJECTED' | 'CANCELLED'
export type TaskOwner = 'HR' | 'IT' | 'FINANCE' | 'MANAGER' | 'EMPLOYEE'

export interface HrCheck {
  code: string
  level: 'INFO' | 'WARN' | 'BLOCK'
  message: string
}

export interface HrTask {
  id: number
  seq: number
  title: string
  owner: TaskOwner
  ownerLabel: string
  required: boolean
  status: 'OPEN' | 'DONE' | 'SKIPPED'
  dueDate: string
  overdue: boolean
  doneByName: string | null
  doneAt: string | null
  note: string | null
}

export interface HrCaseSummary {
  id: number
  caseType: CaseType
  caseTypeLabel: string
  employeeUserId: number
  employeeName: string | null
  teamId: number
  teamName: string | null
  targetTeamId: number | null
  targetTeamName: string | null
  effectiveDate: string
  status: CaseStatus
  statusLabel: string
  openTasks: number
  totalTasks: number
  effectPending: boolean
}

export interface HrCase extends HrCaseSummary {
  position: string | null
  reason: string | null
  initiatorName: string | null
  approverName: string | null
  decisionNote: string | null
  employeeIsHead: boolean
  checks: HrCheck[]
  riskLevel: 'NONE' | 'INFO' | 'WARN' | 'BLOCK'
  tasks: HrTask[]
  myRoles: string[]
  completedAt: string | null
}

export interface MyHrTask {
  taskId: number
  caseId: number
  caseTypeLabel: string
  employeeName: string | null
  title: string
  owner: TaskOwner
  ownerLabel: string
  dueDate: string
  overdue: boolean
}

export interface HrMe {
  roles: string[]
  is_hr: boolean
  is_head: boolean
  org_admin: boolean
}

export interface CaseInput {
  case_type: CaseType
  employee_user_id: number
  employee_team_id: number
  target_team_id?: number | null
  position?: string
  effective_date: string
  reason?: string
}

const BASE = '/enterprise/hr'

export async function getMe(teamId: number): Promise<HrMe> {
  const { data } = await request.get(`${BASE}/me`, { params: { team_id: teamId }, skipErrorToast: true })
  return data as HrMe
}

export async function listCases(teamId: number, view: 'hr' | 'approval' | 'mine', status?: string): Promise<HrCaseSummary[]> {
  const { data } = await request.get(`${BASE}/cases`, { params: { team_id: teamId, view, status } })
  return data as HrCaseSummary[]
}

export async function getCase(teamId: number, id: number): Promise<HrCase> {
  const { data } = await request.get(`${BASE}/cases/${id}`, { params: { team_id: teamId } })
  return data as HrCase
}

export async function myTasks(teamId: number): Promise<MyHrTask[]> {
  const { data } = await request.get(`${BASE}/cases/my-tasks`, { params: { team_id: teamId } })
  return data as MyHrTask[]
}

export async function summary(teamId: number): Promise<{ narrative: string; overdueTasks: number; effectPending: number; avgDaysToComplete: number | null }> {
  const { data } = await request.get(`${BASE}/cases/summary`, { params: { team_id: teamId } })
  return data
}

export async function candidates(teamId: number): Promise<{
  teams: { id: number; name: string; department_code: string | null }[]
  members: { team_id: number; user_id: number; name: string; is_head: boolean }[]
}> {
  const { data } = await request.get(`${BASE}/candidates`, { params: { team_id: teamId } })
  return data
}

export async function precheck(teamId: number, input: CaseInput): Promise<{ checks: HrCheck[]; riskLevel: string; canSubmit: boolean }> {
  const { data } = await request.post(`${BASE}/cases/precheck`, { team_id: teamId, ...input }, { skipErrorToast: true })
  return data
}

export async function createCase(teamId: number, input: CaseInput): Promise<HrCase> {
  const { data } = await request.post(`${BASE}/cases`, { team_id: teamId, ...input })
  return data as HrCase
}

export async function act(teamId: number, id: number, action: 'approve' | 'reject' | 'cancel' | 'complete' | 'recheck' | 'apply-effect', note?: string): Promise<HrCase> {
  const { data } = await request.post(`${BASE}/cases/${id}/${action}`, { team_id: teamId, note })
  return data as HrCase
}

export async function finishTask(teamId: number, caseId: number, taskId: number, result: 'done' | 'skip', note?: string): Promise<HrCase> {
  const { data } = await request.post(`${BASE}/cases/${caseId}/tasks/${taskId}/${result}`, { team_id: teamId, note })
  return data as HrCase
}
