import type { RoundRecord } from '../types'

interface Props { rounds: RoundRecord[] }

export default function RoundTimeline({ rounds }: Props) {
  const last = rounds[rounds.length - 1]
  const maxFlp = Math.max(...rounds.map(r => r.before_flp), 1)
  const W = 760, H = 158, PAD_L = 34, PAD_R = 14, PAD_T = 12, PAD_B = 22

  const pts = rounds.map((r, i) => ({
    x: PAD_L + (rounds.length === 1 ? 0 : (i / (rounds.length - 1)) * (W - PAD_L - PAD_R)),
    y: H - PAD_B - (r.after_flp / maxFlp) * (H - PAD_T - PAD_B),
    r,
  }))

  // Catmull-Rom -> 贝塞尔平滑曲线
  const line = pts.map((p, i) => {
    if (i === 0) return `M${p.x},${p.y}`
    const p0 = pts[i - 1], p1 = p, p2 = pts[i + 1] ?? p1, p3 = pts[i + 2] ?? p2
    const c1x = p0.x + (p1.x - p0.x) / 2.4, c1y = p0.y + (p1.y - p0.y) / 2.4
    const c2x = p1.x - (p2.x - p0.x) / 6, c2y = p1.y - (p2.y - pts[Math.max(0, i - 1)].y) / 6 - (p3.y - p1.y) / 6
    void p2; void p3
    return `C${c1x},${c1y} ${c2x},${c2y} ${p1.x},${p1.y}`
  }).join(' ')
  const area = pts.length ? `${line} L${pts[pts.length - 1].x},${H - PAD_B} L${pts[0].x},${H - PAD_B} Z` : ''

  return (
    <div className="timeline">
      <div className="panel-title">轮次演化 · FLP 曲线
        {last && (
          <span className="fire-stat">
            第 {last.round_index} 轮 · B <b>{last.before_flp}</b>→<b>{last.after_flp}</b> · 压制 {last.suppression_flp}
          </span>
        )}
        <span className="tl-legend"><i className="lg-line" />FLP <i className="lg-dot-warn" />换电/风变</span>
      </div>
      {rounds.length === 0
        ? <div className="empty tl-empty">方案获批后, 火情负荷 B 将按 5 分钟轮次在此逐轮演化</div>
        : (
          <svg viewBox={`0 0 ${W} ${H}`} className="tl-svg" preserveAspectRatio="none">
            <defs>
              <linearGradient id="tlFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#e07856" stopOpacity="0.34" />
                <stop offset="100%" stopColor="#e07856" stopOpacity="0.02" />
              </linearGradient>
            </defs>
            {[0, 0.5, 1].map(f => (
              <g key={f}>
                <line x1={PAD_L} x2={W - PAD_R} y1={H - PAD_B - f * (H - PAD_T - PAD_B)} y2={H - PAD_B - f * (H - PAD_T - PAD_B)} stroke="#1c2f28" strokeDasharray={f === 0 ? '' : '3 5'} />
                <text x={4} y={H - PAD_B - f * (H - PAD_T - PAD_B) + 3} fill="#5f7268" fontSize="9" fontFamily="Consolas">{(maxFlp * f).toFixed(0)}</text>
              </g>
            ))}
            {area && <path d={area} fill="url(#tlFill)" />}
            <path d={line} fill="none" stroke="#e07856" strokeWidth="2.2" strokeLinecap="round" />
            {pts.map(p => {
              const keyEvent = p.r.events.some(e => e.includes('换电') || e.includes('风'))
              return (
                <g key={p.r.round_index}>
                  {keyEvent && <circle cx={p.x} cy={p.y} r="7" fill="none" stroke="#e8c15a" strokeOpacity="0.4" />}
                  <circle cx={p.x} cy={p.y} r={keyEvent ? 4 : 3} fill={keyEvent ? '#e8c15a' : '#e07856'} />
                  {(p.r.round_index === 1 || p.r.round_index === rounds.length) && (
                    <text x={p.x} y={H - 7} fill="#5f7268" fontSize="9" textAnchor="middle" fontFamily="Consolas">R{p.r.round_index}</text>
                  )}
                </g>
              )
            })}
            {pts.length > 0 && (
              <g>
                <circle cx={pts[pts.length - 1].x} cy={pts[pts.length - 1].y} r="9" fill="none" stroke="#e07856" strokeOpacity="0.35">
                  <animate attributeName="r" values="6;11;6" dur="2s" repeatCount="indefinite" />
                  <animate attributeName="stroke-opacity" values="0.5;0;0.5" dur="2s" repeatCount="indefinite" />
                </circle>
              </g>
            )}
          </svg>
        )}
    </div>
  )
}
