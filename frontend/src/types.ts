// 与后端黑板契约对应的类型定义
export interface Position { x: number; y: number; z?: number }

export interface UAV {
  uav_id: string
  subgroup: 'reconnaissance' | 'suppression' | 'support'
  status: string
  position: Position
  target?: Position | null
  soc: number
  payload_capacity_kg: number
  payload_module: string
  agent_remaining: number
  agent_unit: string
  speed_mps: number
  signal: number
  health: number
  sorties?: number
  swaps?: number
  refills?: number
  assigned_task?: string | null
}

export interface FireCell { cx: number; cy: number; intensity: number; flp: number; x: number; y: number }

export interface Fire {
  fire_id: string
  fire_type: string
  cells: FireCell[]
  total_flp: number
  growth_flp_per_hour: number
  wind_speed: number
  wind_direction_deg?: number
  wind_band: number
  wind_band_label: string
  intensity_level: number
  people_status: 'unknown' | 'confirmed' | 'absent'
  confidence: number
}

export interface AgentMessage {
  seq: number
  t: number
  task_id: string
  msg_type: string
  frm: string
  to: string
  content: string
  data?: Record<string, unknown>
}

export interface RoundRecord {
  round_index: number
  sim_minutes: number
  before_flp: number
  growth_flp: number
  suppression_flp: number
  after_flp: number
  wind_speed: number
  uavs: Array<{ uav_id: string; status: string; position: Position; soc: number; agent_remaining: number; sorties: number; swaps: number; refills: number }>
  events: string[]
}

export interface Snapshot {
  task_id: string
  phase: string
  replans: number
  scenario: string
  fire: Fire | null
  fleet: UAV[]
  inventory: Record<string, unknown>
  candidates: Array<Record<string, unknown>>
  plan: Record<string, any> | null
  rounds: RoundRecord[]
  messages: AgentMessage[]
  approval_request: ApprovalRequest | null
  report: Record<string, any> | null
}

export interface ApprovalRequest {
  plan_summary: {
    plan_id: string
    suppression_uavs: string[]
    module: string
    per_sortie_flp: number
    time_interval: string
    feasibility: string
    feasibility_label: string
    score: number
    resource_gap: Record<string, any>
    support_branch: string
    support: Array<Record<string, any>>
    recon: Array<Record<string, any>>
  }
  alternative: { plan_id: string; score: number; time_interval: string; suppression_uavs: string[] } | null
  key_numbers: Array<{ name: string; value: any; source: string }>
  people_note: string | null
}

export interface AgentProfile {
  agent_id: string
  name: string
  role: string
  subgroup: string
  color: string
  emoji: string
}

export interface Scene {
  scene_id: string
  name: string
  wind_direction_deg?: number
  base: { id: string; name: string; x: number; y: number }
  forward_supply_point: { id: string; name: string; x: number; y: number }
  water_sources: Array<{ id: string; name: string; x: number; y: number }>
  roads: Array<{ id: string; name: string; points: number[][] }>
  restricted_cells: Array<{ cx: number; cy: number }>
  map: { width_m: number; height_m: number; cell_m: number }
}

export interface PhaseStepperProps {
  phase: string
  replans: number
}

export const SUBGROUP_META: Record<string, { label: string; color: string; short: string }> = {
  reconnaissance: { label: '侦察监测 R', color: '#3b82f6', short: 'R' },
  suppression: { label: '灭火处置 E', color: '#ef4444', short: 'E' },
  support: { label: '综合支援 S', color: '#22c55e', short: 'S' },
}

export const PHASE_LABEL: Record<string, string> = {
  created: '已建案', analyzing: '研判中', awaiting_approval: '等待审批', executing: '执行中',
  replanning: '重规划中', completed: '已完成', rejected: '已拒绝', error: '异常',
}
