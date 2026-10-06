import request from '../utils/request'

export type TaskStatus = 'DRAFT' | 'PENDING_ACCEPT' | 'NEGOTIATING' | 'IN_PROGRESS' | 'BLOCKED' | 'PENDING_REVIEW' | 'DONE' | 'CANCELLED'
export type PlanStatus = 'DRAFT' | 'PUBLISHED' | 'COMPLETED' | 'CANCELLED'
export type Priority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT'
export type TaskView = 'mine' | 'collab' | 'review' | 'assigned' | 'team'

export interface Issue { code: string; level: 'BLOCK' | 'WARN'; message: string }

export interface RespTask {
  id: number
  planId: number
  planTitle: string | null
  teamId: number
  seq: number
  title: string
  status: TaskStatus
  statusLabel: string
  priority: Priority
  priorityLabel: string
  responsibleUserId: number | null
  responsibleName: string | null
  collaboratorUserIds: number[]
  collaboratorNames: string[]
  reviewerUserId: number | null
  reviewerName: string | null
  assignedByUserId: number | null
  assignedByName: string | null
  deliverable: string | null
  acceptanceCriteria: string | null
  dueDate: string | null
  sourceEvidence: string | null
  dependsOnSeq: number[]
  blockedReason: string | null
  blockedSince: string | null
  waitingOnName: string | null
  lastProgress: string | null
  progressPercent: number | null
  lastProgressAt: string | null
  objectionNote: string | null
  pendingDueDate: string | null
  extensionReason: string | null
  transferNote: string | null
  reworkCount: number
  assignedAt: string | null
  acceptedAt: string | null
  submittedAt: string | null
  completedAt: string | null
  overdue: boolean
  issues: Issue[]
  myRoles: string[]
  myActions: string[]
}

export interface RespEventChange { field: string; fieldLabel: string; fromText: string | null; toText: string | null }
export interface RespEvent {
  id: number
  taskId: number | null
  type: string
  typeLabel: string
  actorName: string | null
  note: string | null
  createdAt: string
  detailData?: { changes?: RespEventChange[]; [k: string]: unknown }
}
export interface Deliverable { id: number; submissionNo: number; summary: string; link: string | null; submittedByName: string | null; submittedAt: string }

export interface RespTaskDetail {
  task: RespTask
  deliverables: Deliverable[]
  events: RespEvent[]
  sourceText: string | null
  planStatus: PlanStatus
  planCreatedBy: number
}

export interface PlanSummary {
  id: number
  teamId: number
  title: string
  sourceType: string
  sourceLabel: string
  status: PlanStatus
  statusLabel: string
  createdByName: string | null
  taskCount: number
  doneCount: number
  openCount: number
  issueCount: number
  createdAt: string
  publishedAt: string | null
}

export interface RespPlan {
  id: number
  teamId: number
  title: string
  sourceType: string
  sourceLabel: string
  summary: string | null
  decisions: { content: string; evidence: string }[]
  unresolved: string[]
  status: PlanStatus
  statusLabel: string
  createdBy: number
  createdByName: string | null
  sourceText: string | null
  tasks: RespTask[]
  events: RespEvent[]
  canPublish: boolean
  blockerCount: number
  warningCount: number
  missingAtCreation: number
  canEdit: boolean
  isHead: boolean
}

export interface Candidates {
  members: { user_id: number; name: string; is_head: boolean }[]
  reviewers: { user_id: number; name: string }[]
  me: { user_id: number; is_head: boolean }
}

export interface MineCounts {
  pendingAccept?: number
  inProgress?: number
  blocked?: number
  overdue?: number
  dueSoon?: number
  pendingReview?: number
  assignedNotAccepted?: number
  isHead?: boolean
  teamOverdue?: number
  teamPendingAccept?: number
  weekCompletionRate?: number | null
}

export interface BriefTask {
  id: number
  title: string
  responsibleName: string | null
  reviewerName: string | null
  dueDate: string | null
  status: TaskStatus
  statusLabel: string
  hoursWaiting?: number | null
  hours?: number
  reason?: string
  waitingOnName?: string | null
  objection?: string | null
}

export interface Summary {
  byStatus: Record<string, number>
  draftTasks: number
  overdueCount: number
  overdue: BriefTask[]
  waitingAccept: BriefTask[]
  blocked: BriefTask[]
  blockedOver2Days: number
  pendingReview: BriefTask[]
  load: { userId: number; name: string; open: number; highPriority: number; overloaded: boolean }[]
  week: { from: string; to: string; due: number; done: number; doneOnTime: number; rate: number | null }
  metrics: Record<string, number | null>
  narrative: string
}

export interface TaskInput {
  title: string
  responsible_user_id: number | null
  collaborator_user_ids: number[]
  reviewer_user_id: number | null
  due_date: string | null
  deliverable: string | null
  acceptance_criteria: string | null
  priority: Priority
  evidence: string | null
  depends_on_seq: number[]
}

const base = '/enterprise/responsibility'

export const getCandidates = async (teamId: number) => (await request.get<Candidates>(`${base}/candidates`, { params: { team_id: teamId } })).data
export const getMine = async (teamId: number) => (await request.get<MineCounts>(`${base}/mine`, { params: { team_id: teamId }, skipErrorToast: true })).data
export const listTasks = async (teamId: number, view: TaskView, status?: string) =>
  (await request.get<RespTask[]>(`${base}/tasks`, { params: { team_id: teamId, view, status }, skipErrorToast: true })).data
export const getTask = async (teamId: number, id: number) => (await request.get<RespTaskDetail>(`${base}/tasks/${id}`, { params: { team_id: teamId } })).data
export const listPlans = async (teamId: number) => (await request.get<PlanSummary[]>(`${base}/plans`, { params: { team_id: teamId }, skipErrorToast: true })).data
export const getPlan = async (teamId: number, id: number) => (await request.get<RespPlan>(`${base}/plans/${id}`, { params: { team_id: teamId } })).data
export const getSummary = async (teamId: number) => (await request.get<Summary>(`${base}/summary`, { params: { team_id: teamId } })).data

export const editPlan = async (teamId: number, id: number, body: { title?: string; summary?: string; unresolved: string[] }) =>
  (await request.post<RespPlan>(`${base}/plans/${id}/edit`, { team_id: teamId, ...body })).data
export const editTask = async (teamId: number, id: number, task: TaskInput) =>
  (await request.post<RespPlan>(`${base}/tasks/${id}/edit`, { team_id: teamId, task })).data
export const addTask = async (teamId: number, planId: number, task: TaskInput) =>
  (await request.post<RespPlan>(`${base}/plans/${planId}/tasks`, { team_id: teamId, task })).data
export const removeTask = async (teamId: number, id: number) => (await request.post<RespPlan>(`${base}/tasks/${id}/remove`, { team_id: teamId })).data
export const publishPlan = async (teamId: number, id: number, note?: string) =>
  (await request.post<RespPlan>(`${base}/plans/${id}/publish`, { team_id: teamId, note })).data
export const cancelPlan = async (teamId: number, id: number, reason: string) =>
  (await request.post<RespPlan>(`${base}/plans/${id}/cancel`, { team_id: teamId, reason })).data
export const actOnTask = async (teamId: number, id: number, action: string, body: Record<string, unknown>) =>
  (await request.post<RespTaskDetail>(`${base}/tasks/${id}/${action}`, { team_id: teamId, ...body })).data
