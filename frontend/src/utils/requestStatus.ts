/** 部门工作台各业务模块共用的申请单状态展示——DRAFT/SUBMITTED/APPROVED/REJECTED
 * 这套语义请假和采购完全一样，抽出来避免两个模块各复制一份、以后改一个忘了改另一个。 */
export type RequestStatus = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'

export const STATUS_LABELS: Record<RequestStatus, string> = {
  DRAFT: '草稿',
  SUBMITTED: '待审批',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
}

const STATUS_BADGE_CLASSES: Record<RequestStatus, string> = {
  DRAFT: 'bg-slate-100 text-slate-500',
  SUBMITTED: 'bg-amber-50 text-amber-700',
  APPROVED: 'bg-emerald-50 text-emerald-700',
  REJECTED: 'bg-red-50 text-red-700',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as RequestStatus] || status
}

export function statusBadgeClass(status: string): string {
  return STATUS_BADGE_CLASSES[status as RequestStatus] || 'bg-slate-100 text-slate-500'
}
