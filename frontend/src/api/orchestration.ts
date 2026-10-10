import request from '../utils/request'

export type StepKind = 'leave' | 'expense' | 'ticket' | 'procurement' | 'crm' | 'hr_case'

export interface PlanStep {
  id: number
  seq: number
  kind: StepKind | null
  kind_label: string
  department_code: string | null
  department_label: string
  clause: string
  reason: string
  state: 'pending' | 'handoff' | 'skipped' | 'linked'
  /** linked 时是关联工作成果的实时状态（processing / ready / applied / failed / retry …） */
  status: string
  automation_work_id: string | null
  error_message: string | null
  business_result: { id?: number } | null
  handoff_agent: { id: number; name: string; team_name?: string } | null
}

export interface Plan {
  id: number
  team_id: number
  source_text: string
  status: string
  created_at: string
  steps: PlanStep[]
  progress: { done: number; total: number; handoff: number }
}

const BASE = '/enterprise/orchestration'

export async function createPlan(teamId: number, text: string): Promise<Plan> {
  const { data } = await request.post(`${BASE}/plans`, { team_id: teamId, text })
  return data as Plan
}

export async function listPlans(teamId: number): Promise<Plan[]> {
  const { data } = await request.get(`${BASE}/plans`, { params: { team_id: teamId } })
  return data as Plan[]
}

export async function getPlan(id: number): Promise<Plan> {
  const { data } = await request.get(`${BASE}/plans/${id}`)
  return data as Plan
}

export async function updateStep(planId: number, stepId: number, payload: { kind?: StepKind; skip?: boolean; clause?: string }): Promise<Plan> {
  const { data } = await request.patch(`${BASE}/plans/${planId}/steps/${stepId}`, payload)
  return data as Plan
}

export async function startStep(planId: number, stepId: number, modelName: string, customerId?: number): Promise<Plan> {
  const { data } = await request.post(`${BASE}/plans/${planId}/steps/${stepId}/start`, { model_name: modelName, customer_id: customerId })
  return data as Plan
}
