import { useEffect, useRef } from 'react'
import type { AgentMessage, AgentProfile } from '../types'

interface Props { messages: AgentMessage[]; agents: AgentProfile[] }

const TYPE_META: Record<string, { label: string; color: string }> = {
  TASK_ASSIGN: { label: '派单', color: '#8b5cf6' },
  FINDING: { label: '研判', color: '#3b82f6' },
  PLAN_PROPOSAL: { label: '方案', color: '#ef4444' },
  SIM_RESULT: { label: '仿真', color: '#f59e0b' },
  APPROVAL_REQ: { label: '待审批', color: '#ec4899' },
  APPROVAL_DECISION: { label: '决策', color: '#ec4899' },
  ROUND: { label: '轮次', color: '#14b8a6' },
  REPLAN_TRIGGER: { label: '重规划', color: '#f97316' },
  REPORT_READY: { label: '报告', color: '#22c55e' },
  HUMAN_ASK: { label: '指挥员提问', color: '#38bdf8' },
  AGENT_REPLY: { label: '智能参谋', color: '#a78bfa' },
  INFO: { label: '通知', color: '#64748b' },
  ERROR: { label: '异常', color: '#ef4444' },
}

export default function AgentPanel({ messages, agents }: Props) {
  const listRef = useRef<HTMLDivElement>(null)
  const byId = new Map(agents.map(a => [a.agent_id, a]))
  byId.set('human', { agent_id: 'human', name: '指挥员', role: '', subgroup: 'human', color: '#f8fafc', emoji: '🧑‍✈️' } as AgentProfile)
  byId.set('blackboard', { agent_id: 'blackboard', name: '黑板', role: '', subgroup: 'system', color: '#64748b', emoji: '🗄️' } as AgentProfile)

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  return (
    <div className="agent-panel">
      <div className="panel-title">Agent 协作流 <span className="count">{messages.length} 条</span></div>
      <div className="agent-messages" ref={listRef}>
        {messages.length === 0 && <div className="empty">开始任务后,六个智能体的协作消息将在此实时滚动…</div>}
        {messages.map(m => {
          const from = byId.get(m.frm) ?? { name: m.frm, color: '#64748b', emoji: '•' } as AgentProfile
          const typeMeta = TYPE_META[m.msg_type] ?? { label: m.msg_type, color: '#64748b' }
          return (
            <div key={m.seq} className={`msg ${m.data && (m.data as any).llm ? 'msg-llm' : ''}`}>
              <div className="msg-head">
                <span className="msg-avatar" style={{ background: from.color + '26', color: from.color, borderColor: from.color }}>
                  {from.emoji} {from.name}
                </span>
                <span className="msg-arrow">→ {m.to === 'human' ? '指挥员' : m.to === 'blackboard' ? '黑板' : (byId.get(m.to)?.name ?? m.to)}</span>
                <span className="msg-type" style={{ color: typeMeta.color, borderColor: typeMeta.color }}>{typeMeta.label}</span>
                {m.data && (m.data as any).llm && <span className="llm-chip">GLM</span>}
                <span className="msg-seq">#{m.seq}</span>
              </div>
              <div className="msg-body">{m.content}</div>
              {m.data && Array.isArray((m.data as any).tools) && (m.data as any).tools.length > 0 && (
                <div className="tool-trace">
                  {(m.data as any).tools.map((t: string, i: number) => (
                    <span key={i} className="tool-chip">🔧 {t}</span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
