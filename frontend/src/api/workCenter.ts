import request from '../utils/request'

export type Priority = 'low' | 'normal' | 'high'
export type WorkItemStatus = 'open' | 'done' | 'dismissed'
export interface WorkItem {
  id: number; team_id: number | null; source_type: 'reminder' | 'automation' | 'manual'; rule: string | null
  title: string; detail: string | null; link: string | null; priority: Priority; status: WorkItemStatus
  resolved_by: 'user' | 'rule' | null; due_at: string | null; completed_at: string | null; created_at: string
}
export interface WorkItemCounts { open: number; overdue: number; due_today: number; high: number }
export interface AppNotification {
  id: number; category: string; category_label: string; title: string; body: string | null
  link: string | null; read: boolean; created_at: string
}
export interface NotificationPreference {
  muted_categories: string[]; quiet_start: string | null; quiet_end: string | null; push_external: boolean
  categories: { value: string; label: string }[]
}
export interface ReminderRuleStatus {
  rule: string; label: string; description: string; interval_minutes: number
  last_status: 'ok' | 'failed' | null; last_error: string | null; last_finished_at: string | null
  next_run_at: string | null; last_created: number; last_resolved: number; consecutive_failures: number
}

export async function listWorkItems(status: WorkItemStatus | 'all' = 'open') {
  return (await request.get<{ items: WorkItem[]; counts: WorkItemCounts }>('/work-items', { params: { status } })).data
}
export async function getWorkItemCounts() { return (await request.get<WorkItemCounts>('/work-items/counts')).data }
export async function createWorkItem(data: { title: string; due_at?: string | null; priority?: Priority; detail?: string }) {
  return (await request.post<WorkItem>('/work-items', data)).data
}
export async function setWorkItemStatus(id: number, status: WorkItemStatus) {
  return (await request.patch<WorkItem>(`/work-items/${id}`, { status })).data
}
export async function listNotifications(unreadOnly = false) {
  return (await request.get<{ items: AppNotification[]; unread: number }>('/notifications', { params: { unread_only: unreadOnly } })).data
}
export async function getUnreadCount() { return (await request.get<{ unread: number }>('/notifications/unread-count')).data.unread }
export async function markNotificationsRead(ids?: number[]) {
  return (await request.post<{ unread: number }>('/notifications/read', { ids: ids ?? null })).data
}
export async function getNotificationPreference() { return (await request.get<NotificationPreference>('/notification-preferences')).data }
export async function updateNotificationPreference(data: Omit<NotificationPreference, 'categories'>) {
  return (await request.put<NotificationPreference>('/notification-preferences', data)).data
}
export async function getReminderStatus() { return (await request.get<ReminderRuleStatus[]>('/admin/reminders')).data }
export async function runReminder(rule: string) {
  return (await request.post<{ rule: string; status: string; created?: number; resolved?: number; error?: string }>(`/admin/reminders/${rule}/run`)).data
}
