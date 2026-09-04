import { useCallback, useEffect, useRef, useState } from 'react'
import { createMission, getSnapshot, llmStatus, postApproval, subscribe } from './api'
import type { AgentMessage, AgentProfile, Scene, Snapshot } from './types'
import SitMap from './components/SitMap'
import AgentPanel from './components/AgentPanel'
import FleetPanel from './components/FleetPanel'
import RoundTimeline from './components/RoundTimeline'
import ApprovalCard from './components/ApprovalCard'
import ReportCard from './components/ReportCard'
import ChatPanel from './components/ChatPanel'
import PhaseStepper from './components/PhaseStepper'
import type { TerrainModel } from './components/Terrain3D'

interface ScenarioOption { id: string; label: string }

export default function App() {
  const [agents, setAgents] = useState<AgentProfile[]>([])
  const [scene, setScene] = useState<Scene | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioOption[]>([])
  const [scenario, setScenario] = useState('wind_shift')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [llm, setLlm] = useState<{ connected: boolean; model: string | null }>({ connected: false, model: null })
  const [defaults, setDefaults] = useState<{ water: number | null; packs: number | null }>({ water: null, packs: null })
  const [terrain, setTerrain] = useState<TerrainModel | null>(null)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    fetch('/api/agents').then(r => r.json()).then(d => setAgents(d.agents)).catch(() => {})
    fetch('/api/scene').then(r => r.json()).then(setScene).catch(() => {})
    fetch('/api/terrain').then(r => r.json()).then(setTerrain).catch(() => {})
    llmStatus().then(setLlm).catch(() => {})
    fetch('/api/inventory').then(r => r.json())
      .then(d => setDefaults({ water: d.water_liters, packs: d.battery_packs })).catch(() => {})
    fetch('/api/scenarios').then(r => r.json())
      .then(d => setScenarios(d.scenarios.map((s: any) => ({ id: s.id, label: s.label })))).catch(() => {})
  }, [])

  useEffect(() => {
    if (!taskId) return
    const refresh = () => getSnapshot(taskId).then(setSnapshot).catch(() => {})
    refresh()
    // SSE 消息可能突发(单节点连发多条), 防抖合并为一次全量拉取
    let timer: number | null = null
    const scheduleRefresh = () => {
      if (timer != null) return
      timer = window.setTimeout(() => { timer = null; refresh() }, 300)
    }
    const unsubscribe = subscribe(taskId, scheduleRefresh, refresh)
    pollRef.current = window.setInterval(refresh, 6000) // SSE 断流时的低频兜底
    return () => {
      unsubscribe()
      if (pollRef.current) window.clearInterval(pollRef.current)
      if (timer != null) window.clearTimeout(timer)
    }
  }, [taskId])

  const startMission = useCallback(async () => {
    setBusy(true)
    try {
      const { task_id } = await createMission(scenario)
      setTaskId(task_id)
    } finally { setBusy(false) }
  }, [scenario])

  const decide = useCallback(async (decision: 'approve' | 'reject' | 'adjust', feedback = '', peopleStatus?: string | null) => {
    if (!taskId) return
    setBusy(true)
    try {
      const { snapshot: snap } = await postApproval(taskId, decision, feedback, peopleStatus)
      setSnapshot(snap)
    } finally { setBusy(false) }
  }, [taskId])

  const phase = snapshot?.phase ?? 'idle'
  const fire = snapshot?.fire
  const fleet = snapshot?.fleet ?? []
  const inv = (snapshot?.inventory ?? {}) as Record<string, any>
  const eReady = fleet.filter(u => u.subgroup === 'suppression' && !['fault', 'charging'].includes(u.status)).length
  const eTotal = fleet.filter(u => u.subgroup === 'suppression').length || 4
  const lastRound = snapshot?.rounds?.[snapshot.rounds.length - 1]

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">🔥</span>
          <div>
            <h1>火巡智策 · 多智能体调度工作台</h1>
            <p>LangGraph × 6-Agent × 规则引擎守护安全数字{scene ? ` · ${scene.name}` : ''}</p>
          </div>
        </div>
        <PhaseStepper phase={phase} replans={snapshot?.replans ?? 0} />
        <div className="controls">
          <select value={scenario} onChange={e => setScenario(e.target.value)} disabled={!!taskId && !['completed', 'rejected', 'error'].includes(phase)}>
            {scenarios.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <button className="primary" onClick={startMission} disabled={busy || (!!taskId && !['completed', 'rejected', 'error'].includes(phase))}>
            {taskId ? '重新开始' : '开始任务'}
          </button>
          <span className={`llm-badge ${llm.connected ? 'on' : 'off'}`} title={llm.model ?? '未配置 FIREOPS_LLM_API_KEY'}>
            {llm.connected ? `⚡ ${llm.model}` : '📴 离线规则'}
          </span>
        </div>
      </header>

      <div className="kpi-bar">
        <div className="kpi k-ember"><b className={fire ? '' : 'idle'}>{fire ? fire.total_flp.toFixed(1) : '0.0'}</b><span>火情负荷 FLP</span></div>
        <div className="kpi"><b className={snapshot?.rounds?.length ? '' : 'idle'}>{snapshot?.rounds?.length ?? 0}</b><span>执行轮次</span></div>
        <div className={`kpi ${fire ? (fire.wind_band >= 2 ? 'k-danger' : 'k-ok') : ''}`}>
          <b className={fire ? '' : 'idle'}>{fire ? `${fire.wind_speed} ${fire.wind_band_label}` : '待命'}</b><span>风速 / 档位</span></div>
        <div className="kpi"><b className={fleet.length ? '' : 'idle'}>{fleet.length ? <>{eReady}<small>/{eTotal}</small></> : <>{eTotal}<small>/{eTotal}</small></>}</b><span>灭火机可用</span></div>
        <div className="kpi"><b className={inv.water_liters != null ? '' : 'idle'}>{inv.water_liters ?? defaults.water ?? '—'} L</b><span>水剂库存</span></div>
        <div className="kpi"><b className={inv.battery_packs != null ? '' : 'idle'}>{inv.battery_packs ?? defaults.packs ?? '—'} 组</b><span>电池组</span></div>
        <div className="kpi"><b className={lastRound ? '' : 'idle'}>{lastRound ? `${lastRound.before_flp}→${lastRound.after_flp}` : '待任务'}</b><span>最新轮 B 变化</span></div>
        <div className="kpi"><b className={snapshot?.messages?.length ? '' : 'idle'}>{snapshot?.messages?.length ?? 0}</b><span>协作消息</span></div>
      </div>

      <div className="agents-strip">
        {agents.map(a => {
          const speaking = snapshot?.messages?.length
            ? snapshot.messages[snapshot.messages.length - 1].frm === a.agent_id : false
          return (
            <div key={a.agent_id} className={`agent-chip ${speaking ? 'speaking' : ''}`} style={{ borderColor: a.color + '88' }}>
              <span className="chip-face" style={{ background: a.color + '1f' }}>{a.emoji}</span>
              <div className="chip-body"><b style={{ color: a.color }}>{a.name}</b><small>{a.role}</small></div>
              {speaking && <span className="speaking-wave"><i /><i /><i /></span>}
            </div>
          )
        })}
      </div>

      <main className="layout">
        <section className="left">
          <SitMap scene={scene} snapshot={snapshot} terrain={terrain} />
          <RoundTimeline rounds={snapshot?.rounds ?? []} />
        </section>
        <section className="right">
          {snapshot && phase === 'awaiting_approval' && snapshot.approval_request && (
            <ApprovalCard request={snapshot.approval_request} busy={busy} onDecide={decide} />
          )}
          {snapshot?.report && <ReportCard report={snapshot.report} />}
          <FleetPanel fleet={fleet} plan={snapshot?.plan ?? null} />
          <ChatPanel taskId={taskId} enabled={llm.connected} />
          <AgentPanel messages={snapshot?.messages ?? []} agents={agents} />
        </section>
      </main>
    </div>
  )
}
