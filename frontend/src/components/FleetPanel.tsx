import type { UAV } from '../types'
import { SUBGROUP_META } from '../types'

interface Props { fleet: UAV[]; plan: Record<string, any> | null }

const STATUS_LABEL: Record<string, string> = {
  available: '待命', assigned: '已受领', flying: '飞行', working: '作业中',
  returning: '返航', servicing: '补给中', charging: '充电中', replanning: '重规划', fault: '故障',
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
      <div className="panel-title">无人机资源池 · 2+4+2</div>
      {groups.map(([sub, uavs]) => (
        <div key={sub} className="fleet-group">
          <div className="group-label" style={{ color: SUBGROUP_META[sub].color }}>{SUBGROUP_META[sub].label} × {uavs.length}</div>
          <div className="fleet-cards">
            {uavs.map(u => {
              const isActive = active.has(u.uav_id)
              return (
                <div key={u.uav_id} className={`uav-card ${isActive ? 'active' : ''}`}>
                  <div className="uav-head">
                    <b style={{ color: SUBGROUP_META[sub].color }}>{u.uav_id}</b>
                    <span className={`uav-status st-${u.status}`}>{STATUS_LABEL[u.status] ?? u.status}</span>
                  </div>
                  <div className="soc-bar"><i style={{ width: `${u.soc}%`, background: u.soc < 25 ? '#ef4444' : u.soc < 50 ? '#f59e0b' : '#22c55e' }} /><span>{u.soc.toFixed(0)}%</span></div>
                  <div className="uav-meta">
                    <span>{u.payload_module === 'water_20l' ? `水 ${u.agent_remaining}L` : u.payload_module === 'co2_6kg' ? `CO₂ ${u.agent_remaining}kg` : u.payload_module === 'sup_10kg' ? `补给箱 ${u.agent_remaining}` : '空载'}</span>
                    <span>架次 {u.sorties ?? 0}</span>
                    <span>换电 {u.swaps ?? 0}</span>
                    <span>补水 {u.refills ?? 0}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
