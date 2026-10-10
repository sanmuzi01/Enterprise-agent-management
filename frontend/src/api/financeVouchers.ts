import request from '../utils/request'

export type VoucherStatus = 'DRAFT' | 'POSTED' | 'VOID'
export type RiskLevel = 'NONE' | 'INFO' | 'WARN' | 'BLOCK'

export interface VoucherRisk {
  code: string
  level: 'INFO' | 'WARN' | 'BLOCK'
  message: string
}

export interface VoucherEntry {
  id: number
  lineNo: number
  subjectCode: string
  subjectName: string
  direction: 'D' | 'C'
  amount: number
  summary: string
  basis: string
  confidence: 'HIGH' | 'MEDIUM' | 'LOW' | 'MANUAL'
  manualOverride: boolean
}

/** 列表行：跟 Java VoucherSummaryDto 一一对应，外加 Python 层补的申请人/部门名称。 */
export interface VoucherSummary {
  id: number
  expenseClaimId: number
  teamId: number
  applicantUserId: number
  status: VoucherStatus
  voucherDate: string
  period: string
  summary: string
  totalAmount: number
  voucherNo: string | null
  riskLevel: RiskLevel
  riskLabel: string
  applicantName: string | null
  teamName: string | null
  createdAt: string
}

export interface VoucherDetail extends VoucherSummary {
  expenseClass: 'SALES' | 'ADMIN'
  risks: VoucherRisk[]
  entries: VoucherEntry[]
  claimLines: { category: string; amount: number; description: string | null; invoiceNo: string | null }[]
  claim: { approverUserId: number | null; decisionNote: string | null; decidedAt: string | null; departmentCode: string | null; createdAt: string }
  confirmedBy: number | null
  confirmNote: string | null
  warningsAcknowledged: boolean
  postedAt: string | null
  voidReason: string | null
  voidedAt: string | null
}

export interface UnbookedClaim {
  id: number
  teamId: number
  teamName: string | null
  applicantUserId: number
  applicantName: string | null
  totalAmount: number
  decidedAt: string | null
}

export interface AccountSubject {
  code: string
  name: string
  category: string
  direction: string
}

export interface VoucherMonthlySummary {
  period: string
  byStatus: Record<VoucherStatus, { count: number; amount: number }>
  bySubject: { code: string; name: string; direction: 'D' | 'C'; amount: number; entries: number }[]
  byTeam: { teamId: number; teamName: string | null; count: number; amount: number }[]
  debitTotal: number
  creditTotal: number
  balanced: boolean
  draftRisk: Record<RiskLevel, number>
  unbookedClaims: number
  unbookedAmount: number
  efficiency: { postedCount: number; adoptedAsIs: number; adoptedRate: number | null; avgHoursToPost: number | null }
  narrative: string
}

const BASE = '/enterprise/finance/vouchers'

export async function listVouchers(teamId: number, status?: VoucherStatus, period?: string): Promise<VoucherSummary[]> {
  const { data } = await request.get(BASE, { params: { team_id: teamId, status, period } })
  return data as VoucherSummary[]
}

export async function getVoucher(teamId: number, id: number): Promise<VoucherDetail> {
  const { data } = await request.get(`${BASE}/${id}`, { params: { team_id: teamId } })
  return data as VoucherDetail
}

export async function listUnbooked(teamId: number): Promise<UnbookedClaim[]> {
  const { data } = await request.get(`${BASE}/unbooked`, { params: { team_id: teamId } })
  return data as UnbookedClaim[]
}

export async function listSubjects(teamId: number): Promise<AccountSubject[]> {
  const { data } = await request.get(`${BASE}/subjects`, { params: { team_id: teamId } })
  return data as AccountSubject[]
}

export async function getMonthlySummary(teamId: number, period?: string): Promise<VoucherMonthlySummary> {
  const { data } = await request.get(`${BASE}/summary`, { params: { team_id: teamId, period } })
  return data as VoucherMonthlySummary
}

export async function generateFromClaim(teamId: number, claimId: number): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/from-claim/${claimId}`, { team_id: teamId })
  return data as VoucherDetail
}

export async function updateEntrySubject(
  teamId: number, voucherId: number, entryId: number, subjectCode: string, reason: string,
): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/entries/${entryId}/subject`, {
    team_id: teamId, subject_code: subjectCode, reason,
  })
  return data as VoucherDetail
}

export async function updateVoucherDate(teamId: number, voucherId: number, voucherDate: string): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/date`, { team_id: teamId, voucher_date: voucherDate })
  return data as VoucherDetail
}

export async function recheckVoucher(teamId: number, voucherId: number): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/recheck`, { team_id: teamId })
  return data as VoucherDetail
}

export async function regenerateVoucher(teamId: number, voucherId: number): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/regenerate`, { team_id: teamId })
  return data as VoucherDetail
}

export async function confirmVoucher(
  teamId: number, voucherId: number, payload: { note?: string; acknowledge_warnings: boolean },
): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/confirm`, { team_id: teamId, ...payload })
  return data as VoucherDetail
}

export async function voidVoucher(teamId: number, voucherId: number, reason: string): Promise<VoucherDetail> {
  const { data } = await request.post(`${BASE}/${voucherId}/void`, { team_id: teamId, reason })
  return data as VoucherDetail
}
