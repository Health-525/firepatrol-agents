import { useCallback, useEffect, useRef, useState } from 'react'
import { createMission, getSnapshot, postApproval, subscribe } from './api'
import type { AgentMessage, AgentProfile, Scene, Snapshot } from './types'
import { PHASE_LABEL } from './types'
import SitMap from './components/SitMap'
import AgentPanel from './components/AgentPanel'
import FleetPanel from './components/FleetPanel'
import RoundTimeline from './components/RoundTimeline'
import ApprovalCard from './components/ApprovalCard'
import ReportCard from './components/ReportCard'

interface ScenarioOption { id: string; label: string }

export default function App() {
  const [agents, setAgents] = useState<AgentProfile[]>([])
  const [scene, setScene] = useState<Scene | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioOption[]>([])
  const [scenario, setScenario] = useState('wind_shift')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    fetch('/api/agents').then(r => r.json()).then(d => setAgents(d.agents)).catch(() => {})
    fetch('/api/scene').then(r => r.json()).then(setScene).catch(() => {})
    fetch('/api/scenarios').then(r => r.json())
      .then(d => setScenarios(d.scenarios.map((s: any) => ({ id: s.id, label: s.label })))).catch(() => {})
  }, [])

  // SSE 订阅 + 快照轮询兜底(SSE 每条消息都会刷新全量快照,轮询兜底断流)
  useEffect(() => {
    if (!taskId) return
    const refresh = () => getSnapshot(taskId).then(setSnapshot).catch(() => {})
    refresh()
    const unsubscribe = subscribe(taskId, (_m: AgentMessage) => refresh(), () => refresh())
    pollRef.current = window.setInterval(refresh, 1500)
    return () => { unsubscribe(); if (pollRef.current) window.clearInterval(pollRef.current) }
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
  const phaseLabel = phase === 'idle' ? '未开始' : (PHASE_LABEL[phase] ?? phase)

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">🔥</span>
          <div>
            <h1>火巡智策 · 森林火灾多智能体调度仿真</h1>
            <p>LangGraph 编排 · 6-Agent 协作 · 规则引擎守护安全数字 · 2+4+2 资源池</p>
          </div>
        </div>
        <div className="controls">
          <select value={scenario} onChange={e => setScenario(e.target.value)} disabled={!!taskId && phase !== 'completed' && phase !== 'rejected'}>
            {scenarios.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <button className="primary" onClick={startMission} disabled={busy || ( !!taskId && !['completed', 'rejected', 'error'].includes(phase))}>
            {taskId ? '重新开始' : '开始任务'}
          </button>
          <span className={`phase phase-${phase}`}>{phaseLabel}{snapshot && snapshot.replans > 0 ? ` · 重规划×${snapshot.replans}` : ''}</span>
        </div>
      </header>

      <div className="agents-strip">
        {agents.map(a => (
          <div key={a.agent_id} className="agent-chip" style={{ borderColor: a.color }}>
            <span>{a.emoji}</span>
            <div><b>{a.name}</b><small>{a.role}</small></div>
          </div>
        ))}
      </div>

      <main className="layout">
        <section className="left">
          <SitMap scene={scene} snapshot={snapshot} />
          <RoundTimeline rounds={snapshot?.rounds ?? []} />
        </section>
        <section className="right">
          {snapshot && (phase === 'awaiting_approval') && snapshot.approval_request && (
            <ApprovalCard request={snapshot.approval_request} busy={busy} onDecide={decide} />
          )}
          {snapshot?.report && <ReportCard report={snapshot.report} />}
          <FleetPanel fleet={snapshot?.fleet ?? []} plan={snapshot?.plan ?? null} />
          <AgentPanel messages={snapshot?.messages ?? []} agents={agents} />
        </section>
      </main>
    </div>
  )
}
