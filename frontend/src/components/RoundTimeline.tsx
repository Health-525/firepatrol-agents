import type { RoundRecord } from '../types'

interface Props { rounds: RoundRecord[] }

export default function RoundTimeline({ rounds }: Props) {
  const last = rounds[rounds.length - 1]
  const maxFlp = Math.max(...rounds.map(r => r.before_flp), 1)
  const W = 720, H = 150, PAD = 30
  const points = rounds.map((r, i) => ({
    x: PAD + (rounds.length === 1 ? 0 : (i / (rounds.length - 1)) * (W - 2 * PAD)),
    y: H - PAD - (r.after_flp / maxFlp) * (H - 2 * PAD),
    r,
  }))
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')

  return (
    <div className="timeline">
      <div className="panel-title">轮次时间轴 · 5 min/轮
        {last && (
          <span className="fire-stat">
            第 {last.round_index} 轮 · B {last.before_flp}→<b>{last.after_flp}</b> FLP · 本轮压制 {last.suppression_flp}
            {last.events.length > 0 && <em className="round-events"> ⚑ {last.events.join(';')}</em>}
          </span>
        )}
      </div>
      {rounds.length === 0
        ? <div className="empty" style={{ padding: 18 }}>方案获批后,这里将逐轮显示火情负荷 B 的演化曲线与关键事件。</div>
        : (
          <svg viewBox={`0 0 ${W} ${H}`} className="tl-svg">
            {[0, 0.5, 1].map(f => (
              <g key={f}>
                <line x1={PAD} x2={W - PAD} y1={H - PAD - f * (H - 2 * PAD)} y2={H - PAD - f * (H - 2 * PAD)} stroke="#1e3a34" />
                <text x={4} y={H - PAD - f * (H - 2 * PAD) + 4} fill="#64748b" fontSize="10">{(maxFlp * f).toFixed(0)}</text>
              </g>
            ))}
            <path d={path} fill="none" stroke="#f87171" strokeWidth="2" />
            {points.map(p => (
              <g key={p.r.round_index}>
                <circle cx={p.x} cy={p.y} r={p.r.events.some(e => e.includes('换电') || e.includes('风')) ? 5 : 3}
                  fill={p.r.events.some(e => e.includes('换电') || e.includes('风')) ? '#f97316' : '#f87171'} />
                {(p.r.round_index - 1) % 2 === 0 && (
                  <text x={p.x} y={H - 8} fill="#64748b" fontSize="9" textAnchor="middle">R{p.r.round_index}</text>
                )}
              </g>
            ))}
          </svg>
        )}
    </div>
  )
}
