import type { UAV } from '../types'
import { SUBGROUP_META } from '../types'

interface Props { fleet: UAV[]; plan: Record<string, any> | null }

const STATUS_LABEL: Record<string, string> = {
  available: '待命', assigned: '已受领', flying: '飞行', working: '作业中',
  returning: '返航', servicing: '补给中', charging: '充电中', replanning: '重规划', fault: '故障',
}
const AVATAR: Record<string, string> = { reconnaissance: '🔭', suppression: '🚒', support: '🛟' }
const MODULE_LABEL: Record<string, (u: UAV) => string> = {
  water_20l: u => `水 ${u.agent_remaining}L`,
  co2_6kg: u => `CO₂ ${u.agent_remaining}kg`,
  sup_10kg: u => `补给箱 ${u.agent_remaining}`,
}

export default function FleetPanel({ fleet, plan }: Props) {
  const groups: Array<[string, UAV[]]> = [
    ['reconnaissance', fleet.filter(u => u.subgroup === 'reconnaissance')],
    ['suppression', fleet.filter(u => u.subgroup === 'suppression')],
    ['support', fleet.filter(u => u.subgroup === 'support')],
  ]
  const active = new Set<string>(
    plan ? [...(plan.candidate?.suppression_uavs ?? []), ...(plan.candidate?.recon_uavs ?? []),
      ...(plan.candidate?.support_assignments ?? []).map((a: any) => a.uav_id)] : [],
  )

  return (
    <div className="fleet-panel">
      <div className="panel-title">无人机资源池 · 2+4+2
        <span className="count">{fleet.length} 架在线</span>
      </div>
      {groups.map(([sub, uavs]) => (
        <div key={sub} className="fleet-group">
          <div className="group-label" style={{ color: SUBGROUP_META[sub].color }}>
            {SUBGROUP_META[sub].label} × {uavs.length}
          </div>
          <div className="fleet-cards">
            {uavs.map(u => {
              const isActive = active.has(u.uav_id)
              const socColor = u.soc < 25 ? 'var(--danger)' : u.soc < 50 ? 'var(--warn)' : 'var(--ok)'
              return (
                <div key={u.uav_id} className={`uav-card ${isActive ? 'active' : ''}`}>
                  <div className="uav-head">
                    <span className="uav-avatar" style={{ borderColor: SUBGROUP_META[sub].color }}>
                      {AVATAR[sub]}<b>{u.uav_id}</b>
                    </span>
                    <span className={`uav-status st-${u.status}`}>
                      <i className="st-dot" />{STATUS_LABEL[u.status] ?? u.status}
                    </span>
                  </div>
                  <div className="soc-bar"><i style={{ width: `${u.soc}%`, background: socColor }} /><span>{u.soc.toFixed(0)}%</span></div>
                  <div className="uav-meta">
                    <span className="module">{MODULE_LABEL[u.payload_module]?.(u) ?? '空载'}</span>
                    <span>架次 <b>{u.sorties ?? 0}</b></span>
                    <span>换电 <b>{u.swaps ?? 0}</b></span>
                    <span>补水 <b>{u.refills ?? 0}</b></span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
      {fleet.length === 0 && <div className="empty">🛫 开始任务后, 2+4+2 机队将在此显示实时状态</div>}
    </div>
  )
}
