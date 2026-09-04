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
  const [scenario, setScenario] = useState('random')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [voiceOn, setVoiceOn] = useState(true)
  const spokenRef = useRef<number>(-1)
  const [llm, setLlm] = useState<{ connected: boolean; model: string | null; degraded?: boolean; last_error?: string | null }>({ connected: false, model: null })
  const [terrain, setTerrain] = useState<TerrainModel | null>(null)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    fetch('/api/agents').then(r => r.json()).then(d => setAgents(d.agents)).catch(() => {})
    fetch('/api/scene').then(r => r.json()).then(setScene).catch(() => {})
    fetch('/api/terrain').then(r => r.json()).then(setTerrain).catch(() => {})
    llmStatus().then(setLlm).catch(() => {})
    const llmTimer = window.setInterval(() => llmStatus().then(setLlm).catch(() => {}), 10000)
    fetch('/api/scenarios').then(r => r.json())
      .then(d => setScenarios(d.scenarios.map((s: any) => ({ id: s.id, label: s.label })))).catch(() => {})
    return () => window.clearInterval(llmTimer)
  }, [])

  useEffect(() => {
    // 语音发号令: 疏散广播(浏览器 TTS, zh-CN)
    if (!voiceOn || !snapshot) return
    const broadcasts = snapshot.messages.filter(m => m.msg_type === 'EVAC_BROADCAST')
    const last = broadcasts[broadcasts.length - 1]
    if (last && last.seq !== spokenRef.current) {
      spokenRef.current = last.seq
      try {
        const utterance = new SpeechSynthesisUtterance(last.content.replace(/🔊/g, '').replace(/S1 号机/g, 'S1号机'))
        utterance.lang = 'zh-CN'
        utterance.rate = 1.05
        utterance.pitch = 1.0
        window.speechSynthesis.cancel()
        window.speechSynthesis.speak(utterance)
      } catch { /* 浏览器不支持 TTS 时静默 */ }
    }
  }, [snapshot, voiceOn])

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
  const eReady = fleet.filter(u => u.subgroup === 'suppression' && !['fault', 'charging'].includes(u.status)).length
  const eTotal = fleet.filter(u => u.subgroup === 'suppression').length || 4

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">🔥</span>
          <div>
            <h1>火巡智策 · 多智能体调度工作台</h1>
            <p>多智能体调度{scene ? ` · ${scene.name}` : ''}</p>
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
          <button className={`voice-btn ${voiceOn ? 'on' : ''}`} onClick={() => setVoiceOn(v => !v)}
                  title={voiceOn ? '语音广播已开启(点击静音)' : '语音广播已静音(点击开启)'}>
            {voiceOn ? '🔊 广播' : '🔇 静音'}
          </button>
          <span className={`llm-badge ${llm.connected ? (llm.degraded ? 'degraded' : 'on') : 'off'}`}
                title={llm.degraded ? `连续失败: ${llm.last_error ?? ''}` : (llm.model ?? '未配置 FIREOPS_LLM_API_KEY')}>
            {llm.degraded ? '⚠ GLM 降级·确定性模式' : llm.connected ? `⚡ ${llm.model}` : '📴 离线规则'}
          </span>
        </div>
      </header>

      <div className="kpi-bar">
        <div className="kpi k-ember"><b className={fire ? '' : 'idle'}>{fire ? fire.total_flp.toFixed(1) : '0.0'}</b><span>火情负荷 FLP</span></div>
        <div className="kpi"><b className={snapshot?.rounds?.length ? '' : 'idle'}>{snapshot?.rounds?.length ?? 0}</b><span>执行轮次</span></div>
        <div className={`kpi ${fire ? (fire.wind_band >= 2 ? 'k-danger' : 'k-ok') : ''}`}>
          <b className={fire ? '' : 'idle'}>{fire ? `${fire.wind_speed} ${fire.wind_band_label}` : '待命'}</b><span>风速 / 档位</span></div>
        <div className="kpi"><b className={fleet.length ? '' : 'idle'}>{fleet.length ? <>{eReady}<small>/{eTotal}</small></> : <>{eTotal}<small>/{eTotal}</small></>}</b><span>灭火机可用</span></div>
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
          <FleetPanel fleet={fleet} plan={snapshot?.plan ?? null}
            ground={!!snapshot && (['executing', 'replanning', 'recovering', 'completed'].includes(phase) ||
              (phase === 'awaiting_approval' && (snapshot.rounds?.length ?? 0) > 0))} />
          <ChatPanel taskId={taskId} enabled={llm.connected} />
          <AgentPanel messages={snapshot?.messages ?? []} agents={agents} />
        </section>
      </main>
    </div>
  )
}
