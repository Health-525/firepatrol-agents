import type { PhaseStepperProps } from '../types'

const STAGES = [
  { no: '01', label: '接警', icon: '🚨' },
  { no: '02', label: '搜索', icon: '🔭' },
  { no: '03', label: '研判', icon: '🌲' },
  { no: '04', label: '方案', icon: '🚒' },
  { no: '05', label: '审批', icon: '📝' },
  { no: '06', label: '执行', icon: '⚙️' },
  { no: '07', label: '归档', icon: '📄' },
]

const PHASE_STAGE: Record<string, number> = {
  idle: -1, created: 0, searching: 1, analyzing: 2, awaiting_approval: 4,
  executing: 5, replanning: 5, completed: 6, rejected: 6, error: 6,
}

export default function PhaseStepper({ phase, replans }: PhaseStepperProps) {
  const current = PHASE_STAGE[phase] ?? -1
  const done = phase === 'completed'
  const dead = phase === 'rejected' || phase === 'error'
  return (
    <div className={`stepper ${dead ? 'stepper-dead' : ''}`}>
      {STAGES.map((stage, i) => {
        const state = done || i < current ? 'done' : i === current ? 'active' : 'todo'
        return (
          <div key={stage.label} className={`stage stage-${state}`}>
            <span className="stage-no">{stage.no}</span>
            <span className="stage-node">{state === 'done' ? '✓' : stage.icon}</span>
            <span className="stage-label">{stage.label}</span>
            {i < STAGES.length - 1 && <span className="stage-link" />}
          </div>
        )
      })}
      {replans > 0 && (
        <span className="replan-chip" title="执行中触发重规划,已回到方案阶段">↺ 重规划 ×{replans}</span>
      )}
    </div>
  )
}
