import { useState } from 'react'
import type { ApprovalRequest } from '../types'

interface Props {
  request: ApprovalRequest
  busy: boolean
  onDecide: (decision: 'approve' | 'reject' | 'adjust', feedback?: string, peopleStatus?: string | null) => void
}

export default function ApprovalCard({ request, busy, onDecide }: Props) {
  const [feedback, setFeedback] = useState('')
  const [people, setPeople] = useState('')
  const p = request.plan_summary
  const gap = p.resource_gap ?? {}

  return (
    <div className="approval-card">
      <div className="panel-title approval-title">📝 方案待审批 <span className="plan-id">{p.plan_id}</span></div>
      <div className="approval-grid">
        <div className="ap-col">
          <h4>最优方案</h4>
          <ul className="kv">
            <li><span>灭火机</span><b>{p.suppression_uavs.join(' + ') || '—'}</b></li>
            <li><span>药剂模块</span><b>{p.module === 'water_20l' ? 'W20 水剂 20L' : 'C6 CO₂ 6kg'}</b></li>
            <li><span>单架次有效能力</span><b>{p.per_sortie_flp} FLP</b></li>
            <li><span>预计完成</span><b className="hl">{p.time_interval}</b></li>
            <li><span>可行性</span><b>{p.feasibility_label}</b></li>
            <li><span>评分 J(越小越优)</span><b>{p.score}</b></li>
            <li><span>支援分支</span><b>{{ people: '有人:通信+指引', logistics: '无人:物流', verify: '待复核' }[p.support_branch] ?? p.support_branch}</b></li>
            {p.water_source_note && <li><span>水剂补给</span><b>{p.water_source_note}</b></li>}
            {gap.message && <li className="gap"><span>资源缺口</span><b>{gap.message}</b></li>}
          </ul>
        </div>
        <div className="ap-col">
          <h4>关键数字来源(规则引擎)</h4>
          <ul className="kv source">
            {request.key_numbers.map(k => (
              <li key={k.name}><span>{k.name}</span><b>{k.value}<small> ← {k.source}</small></b></li>
            ))}
          </ul>
          {request.alternative && (
            <div className="alt">备选 {request.alternative.plan_id}:{request.alternative.suppression_uavs.join('+')} · J={request.alternative.score} · {request.alternative.time_interval}</div>
          )}
        </div>
      </div>
      {request.people_note && (
        <div className="people-note">⚠ {request.people_note}</div>
      )}
      <div className="approval-actions">
        {request.people_note && (
          <select value={people} onChange={e => setPeople(e.target.value)}>
            <option value="">人员状态:保持现状</option>
            <option value="confirmed">确认有人</option>
            <option value="absent">确认无人</option>
          </select>
        )}
        <input placeholder="调整意见,如:最多出动 2 架" value={feedback} onChange={e => setFeedback(e.target.value)} />
        <button className="primary" disabled={busy} onClick={() => onDecide('approve', feedback, people || null)}>批准执行</button>
        <button disabled={busy || (!feedback.trim() && !people)} onClick={() => onDecide('adjust', feedback, people || null)}>调整</button>
        <button className="danger" disabled={busy} onClick={() => onDecide('reject')}>拒绝</button>
      </div>
      <div className="approval-note">生成方案 ≠ 执行:批准后才会锁定无人机资源并进入 5 分钟轮次仿真。</div>
    </div>
  )
}
