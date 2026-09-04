import { useEffect, useRef, useState } from 'react'
import type { Scene, Snapshot, UAV } from '../types'
import { SUBGROUP_META } from '../types'
import Terrain3D, { type TerrainModel } from './Terrain3D'

interface Props { scene: Scene | null; snapshot: Snapshot | null; terrain: TerrainModel | null }

const W = 2000, H = 1400 // 世界坐标(米)

// 无人机动画位置(uav_id -> 当前渲染坐标); 换任务时清空, 避免从上一个任务的残位飞过来
const animPos: Record<string, { x: number; y: number }> = {}
let animTaskId: string | null = null

export default function SitMap({ scene, snapshot, terrain }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [mode, setMode] = useState<'3d' | '2d'>('3d')
  const stateRef = useRef<{ scene: Scene | null; snapshot: Snapshot | null }>({ scene, snapshot })
  stateRef.current = { scene, snapshot }

  useEffect(() => {
    let raf = 0
    const draw = (ts: number) => {
      const canvas = canvasRef.current
      const { scene: sc, snapshot: snap } = stateRef.current
      if (canvas) {
        const dpr = window.devicePixelRatio || 1
        const rect = canvas.parentElement!.getBoundingClientRect()
        if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
          canvas.width = rect.width * dpr
          canvas.height = rect.height * dpr
        }
        const ctx = canvas.getContext('2d')!
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        render(ctx, rect.width, rect.height, sc, snap, ts)
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  const fire = snapshot?.fire
  return (
    <div className="sitmap">
      <div className="panel-title">林区态势{mode === '3d' ? ' · 三维地形模型' : ' · 100 m² 网格 + FLP 热度'}
        {fire && (
          <span className="fire-stat">
            B<sub>总</sub> = <b>{fire.total_flp}</b> FLP · 风 {fire.wind_speed} m/s({fire.wind_band_label}) · 人员 {fire.people_status}
          </span>
        )}
        <span className="view-toggle">
          <button className={mode === '3d' ? 'on' : ''} onClick={() => setMode('3d')}>🏔 模型</button>
          <button className={mode === '2d' ? 'on' : ''} onClick={() => setMode('2d')}>🗺 平面</button>
        </span>
      </div>
      {mode === '3d' ? (
        <div className="canvas-wrap terrain-wrap">
          <Terrain3D scene={scene} snapshot={snapshot} terrain={terrain} />
          {terrain && <span className="terrain-src">地形: {terrain.source} · 高程 {terrain.min_elev}–{terrain.max_elev} m · 垂直夸张 ×{terrain.exaggeration} · 拖拽旋转/滚轮缩放</span>}
        </div>
      ) : (
        <div className="canvas-wrap"><canvas ref={canvasRef} /></div>
      )}
      <div className="legend">
        <span><i className="lg" style={{ background: SUBGROUP_META.reconnaissance.color }} /> 侦察 R</span>
        <span><i className="lg" style={{ background: SUBGROUP_META.suppression.color }} /> 灭火 E</span>
        <span><i className="lg" style={{ background: SUBGROUP_META.support.color }} /> 支援 S</span>
        <span><i className="lg fire" /> 火情网格</span>
        <span><i className="lg water" /> 水源</span>
        <span><i className="lg fsp" /> 基地/补给点</span>
      </div>
    </div>
  )
}

function render(ctx: CanvasRenderingContext2D, vw: number, vh: number, sc: Scene | null, snap: Snapshot | null, ts: number) {
  ctx.clearRect(0, 0, vw, vh)
  const scale = Math.min(vw / W, vh / H)
  const ox = (vw - W * scale) / 2, oy = (vh - H * scale) / 2
  const px = (x: number) => ox + x * scale
  const py = (y: number) => oy + y * scale

  // 背景
  ctx.fillStyle = '#0b1512'
  ctx.fillRect(0, 0, vw, vh)

  // 网格 + 坐标尺(指挥地图惯例: 顶部列号 / 左侧行号, 每 200m 标注)
  ctx.strokeStyle = 'rgba(74, 138, 110, 0.16)'
  ctx.lineWidth = 1
  for (let x = 0; x <= W; x += 100) { ctx.beginPath(); ctx.moveTo(px(x), py(0)); ctx.lineTo(px(x), py(H)); ctx.stroke() }
  for (let y = 0; y <= H; y += 100) { ctx.beginPath(); ctx.moveTo(px(0), py(y)); ctx.lineTo(px(W), py(y)); ctx.stroke() }

  // 等高线纹理(低透明度贝塞尔弧, 山地指挥图质感)
  ctx.strokeStyle = 'rgba(94, 131, 113, 0.13)'
  ctx.lineWidth = 1.2
  for (let i = 0; i < 7; i++) {
    ctx.beginPath()
    const baseY = 140 + i * 170
    ctx.moveTo(px(60 + i * 90), py(baseY))
    ctx.bezierCurveTo(px(500 + i * 40), py(baseY - 130), px(1100 - i * 30), py(baseY + 120), px(1900 - i * 60), py(baseY - 40))
    ctx.stroke()
  }

  ctx.fillStyle = 'rgba(110, 140, 128, 0.5)'
  ctx.font = '9px Consolas, monospace'
  for (let x = 0; x < W; x += 200) ctx.fillText(String(x), px(x) + 3, py(0) + 12)
  for (let y = 0; y < H; y += 200) ctx.fillText(String(y), px(0) + 4, py(y) + 11)

  // 风场矢量阵列(指向场景盛行风向, 角度取自 scene.wind_direction_deg)
  const windActive = !!snap?.fire
  const windDeg = sc?.wind_direction_deg ?? 315
  const wdx = Math.cos((windDeg * Math.PI) / 180), wdy = Math.sin((windDeg * Math.PI) / 180)
  ctx.strokeStyle = 'rgba(103, 232, 249, 0.22)'
  ctx.lineWidth = 1.2
  for (let gx = 250; gx < W - 100; gx += 330) {
    for (let gy = 250; gy < H - 100; gy += 300) {
      const drift = windActive ? Math.sin(ts / 900 + gx / 300 + gy / 400) * 26 : Math.sin(ts / 1600 + gx / 300) * 14
      const ax = px(gx + drift), ay = py(gy + drift * 0.4)
      ctx.beginPath(); ctx.moveTo(ax - wdx * 14, ay - wdy * 14); ctx.lineTo(ax + wdx * 14, ay + wdy * 14); ctx.stroke()
      ctx.beginPath(); ctx.arc(ax + wdx * 14, ay + wdy * 14, 2.2, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(103, 232, 249, 0.3)'; ctx.fill()
    }
  }

  // 空态提示
  if (!snap?.fire && !snap?.fleet?.length) {
    ctx.fillStyle = 'rgba(143, 163, 154, 0.65)'
    ctx.font = '13px "Microsoft YaHei UI", sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('等待任务 · 选择演练场景并点击「开始任务」', px(W / 2), py(H / 2) - 8)
    ctx.fillStyle = 'rgba(143, 163, 154, 0.4)'
    ctx.font = '10.5px Consolas, monospace'
    ctx.fillText('紫金山北麓演示林区 · 2000m × 1400m · 100m² 网格', px(W / 2), py(H / 2) + 14)
    ctx.textAlign = 'left'
  }

  if (!sc) return

  // 禁飞/风险网格
  ctx.fillStyle = 'rgba(148, 163, 184, 0.18)'
  for (const cell of sc.restricted_cells ?? []) ctx.fillRect(px(cell.cx * 100), py(cell.cy * 100), 100 * scale, 100 * scale)

  // 道路
  ctx.strokeStyle = 'rgba(226, 232, 240, 0.5)'
  ctx.lineWidth = Math.max(2, 3 * scale * 4)
  ctx.setLineDash([])
  for (const road of sc.roads ?? []) {
    ctx.beginPath()
    road.points.forEach(([x, y], i) => (i ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y))))
    ctx.stroke()
  }

  // 水源
  for (const ws of sc.water_sources ?? []) {
    ctx.fillStyle = '#38bdf8'
    ctx.beginPath(); ctx.arc(px(ws.x), py(ws.y), 7, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = '#7dd3fc'; ctx.font = '10px sans-serif'; ctx.fillText(ws.name, px(ws.x) + 10, py(ws.y) + 3)
  }

  // 基地 / 前向补给点
  marker(ctx, px(sc.base.x), py(sc.base.y), '#facc15', '基地')
  marker(ctx, px(sc.forward_supply_point.x), py(sc.forward_supply_point.y), '#fbbf24', 'FSP-1')

  // 火情网格(FLP 径向热力 + 呼吸动画)
  const fire = snap?.fire
  if (fire) {
    const maxFlp = Math.max(...fire.cells.map(c => c.flp), 1)
    for (const cell of fire.cells) {
      if (cell.flp <= 0.01) continue
      const pulse = 0.55 + 0.35 * Math.sin(ts / 350 + cell.cx + cell.cy)
      const alpha = Math.min(0.92, (cell.flp / maxFlp) * pulse + 0.15)
      const size = 100 * scale
      const cx0 = px(cell.x), cy0 = py(cell.y)
      const gradient = ctx.createRadialGradient(cx0, cy0, size * 0.1, cx0, cy0, size * 0.85)
      gradient.addColorStop(0, `rgba(246, 194, 107, ${Math.min(1, alpha + 0.25).toFixed(3)})`)
      gradient.addColorStop(0.45, `rgba(239, 68, 68, ${alpha.toFixed(3)})`)
      gradient.addColorStop(1, 'rgba(153, 27, 27, 0.05)')
      ctx.fillStyle = gradient
      ctx.fillRect(cx0 - size / 2, cy0 - size / 2, size, size)
      ctx.strokeStyle = `rgba(251, 146, 60, ${Math.min(1, alpha + 0.2).toFixed(3)})`
      ctx.strokeRect(cx0 - size / 2, cy0 - size / 2, size, size)
    }
  }

  // 无人机(位置插值动画)
  const fleet: UAV[] = snap?.fleet ?? []
  if (snap && snap.task_id !== animTaskId) {
    animTaskId = snap.task_id
    for (const key of Object.keys(animPos)) delete animPos[key]
  }
  for (const uav of fleet) {
    const target = { x: uav.position.x, y: uav.position.y }
    const cur = animPos[uav.uav_id]
    if (cur) {
      cur.x += (target.x - cur.x) * 0.08
      cur.y += (target.y - cur.y) * 0.08
    } else animPos[uav.uav_id] = { ...target }
    const p = animPos[uav.uav_id]
    const meta = SUBGROUP_META[uav.subgroup] ?? { color: '#94a3b8', label: uav.subgroup, short: '?' }
    const x = px(p.x), y = py(p.y)
    // SOC 环
    ctx.beginPath(); ctx.arc(x, y, 12, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * (uav.soc / 100))
    ctx.strokeStyle = uav.soc < 25 ? '#ef4444' : meta.color; ctx.lineWidth = 3; ctx.stroke()
    // 机体
    ctx.fillStyle = meta.color
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill()
    if (uav.status === 'working') { // 作业光环
      ctx.beginPath(); ctx.arc(x, y, 15 + 3 * Math.sin(ts / 200), 0, Math.PI * 2)
      ctx.strokeStyle = meta.color + '88'; ctx.lineWidth = 1.5; ctx.stroke()
    }
    ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 10px sans-serif'
    ctx.fillText(uav.uav_id, x + 13, y - 8)
    ctx.fillStyle = '#94a3b8'; ctx.font = '9px sans-serif'
    ctx.fillText(`${uav.soc.toFixed(0)}%`, x + 13, y + 4)
  }

  // 风向箭头(右上角)
  const fire0 = snap?.fire
  if (fire0) {
    const cx = vw - 60, cy = 46
    const rad = ((fire0.wind_direction_deg ?? sc?.wind_direction_deg ?? 315) * Math.PI) / 180
    const dx = Math.cos(rad), dy = Math.sin(rad)
    ctx.strokeStyle = '#67e8f9'; ctx.lineWidth = 2
    ctx.beginPath(); ctx.moveTo(cx - dx * 16, cy - dy * 16); ctx.lineTo(cx + dx * 16, cy + dy * 16); ctx.stroke()
    ctx.beginPath(); ctx.arc(cx + dx * 16, cy + dy * 16, 3, 0, Math.PI * 2); ctx.fillStyle = '#67e8f9'; ctx.fill()
    ctx.fillStyle = '#a5f3fc'; ctx.font = '10px sans-serif'
    ctx.fillText(`${fire0.wind_speed} m/s`, cx - 18, cy + 30)
  }
}

function marker(ctx: CanvasRenderingContext2D, x: number, y: number, color: string, label: string) {
  ctx.fillStyle = color
  ctx.fillRect(x - 6, y - 6, 12, 12)
  ctx.strokeStyle = '#0008'; ctx.strokeRect(x - 6, y - 6, 12, 12)
  ctx.fillStyle = color; ctx.font = 'bold 10px sans-serif'
  ctx.fillText(label, x + 9, y + 4)
}
