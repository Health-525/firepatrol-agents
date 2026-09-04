import { useEffect, useRef, useState } from 'react'
import type { Scene, Snapshot, UAV } from '../types'
import { SUBGROUP_META } from '../types'
import Terrain3D, { type TerrainModel } from './Terrain3D'

interface Props { scene: Scene | null; snapshot: Snapshot | null; terrain: TerrainModel | null }

const W = 2000, H = 1400 // 世界坐标(米)

// 无人机动画位置(uav_id -> 当前渲染坐标)
const animPos: Record<string, { x: number; y: number }> = {}
let animTaskId = ''

// ---------- 场景装饰缓存(森林/湖泊/溪流, 按场景生成一次) ----------
interface Decor {
  trees: Array<{ x: number; y: number; s: number; c: string }>
  lakes: Array<{ pts: number[][]; name: string; x: number; y: number }>
  stream: number[][]
}
let decorCache: { key: string; decor: Decor } | null = null

function mulberry(seed: string) {
  let a = 0
  for (let i = 0; i < seed.length; i++) a = (a * 31 + seed.charCodeAt(i)) >>> 0
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function buildDecor(scene: Scene, terrain: TerrainModel | null): Decor {
  const rng = mulberry(scene.scene_id || 'scene')
  const elevAt = (x: number, y: number) => {
    if (!terrain) return 0
    const gx = Math.min(terrain.nx - 1, Math.max(0, Math.round(x / terrain.scene_w * (terrain.nx - 1))))
    const gy = Math.min(terrain.ny - 1, Math.max(0, Math.round(y / terrain.scene_h * (terrain.ny - 1))))
    return terrain.elevations[gy][gx]
  }
  const span = terrain ? terrain.max_elev - terrain.min_elev : 100
  // 森林: 中低海拔林带(避开山顶裸岩与水/基地区)
  const trees: Decor['trees'] = []
  const waterPts = (scene.water_sources ?? []).map(w => [w.x, w.y])
  const nearWater = (x: number, y: number) => waterPts.some(([wx, wy]) => Math.hypot(x - wx, y - wy) < 110)
  for (let i = 0; i < 340; i++) {
    const x = 60 + rng() * (W - 120)
    const y = 60 + rng() * (H - 120)
    if (nearWater(x, y)) continue
    if (Math.hypot(x - scene.base.x, y - scene.base.y) < 130) continue
    if (Math.hypot(x - scene.forward_supply_point.x, y - scene.forward_supply_point.y) < 90) continue
    const t = (elevAt(x, y) - (terrain?.min_elev ?? 0)) / (span || 1)
    if (t > 0.82) continue // 高脊裸岩
    const green = 46 + Math.floor(rng() * 26)
    trees.push({ x, y, s: 7 + rng() * 5, c: `hsl(${140 + rng() * 18}, 34%, ${green / 2.1}%)` })
  }
  // 湖泊: 径向噪声有机形状
  const lakes = (scene.water_sources ?? []).filter(w => !w.name.includes('溪')).map(w => {
    const pts: number[][] = []
    const r = 62
    for (let k = 0; k < 12; k++) {
      const ang = (k / 12) * Math.PI * 2
      const rr = r * (0.62 + rng() * 0.55)
      pts.push([w.x + Math.cos(ang) * rr * 1.25, w.y + Math.sin(ang) * rr])
    }
    return { pts, name: w.name, x: w.x, y: w.y }
  })
  // 溪流: 蜿蜒折线(自水源向图缘)
  const streamSrc = (scene.water_sources ?? []).find(w => w.name.includes('溪'))
  const stream: number[][] = []
  if (streamSrc) {
    let sx = streamSrc.x, sy = streamSrc.y
    for (let k = 0; k < 14 && sx < W; k++) {
      stream.push([sx, sy])
      sx += 42 + rng() * 26
      sy += (rng() - 0.5) * 66
    }
  }
  return { trees, lakes, stream }
}

// ---------- 图元 ----------
function pine(ctx: CanvasRenderingContext2D, x: number, y: number, s: number, color: string) {
  ctx.fillStyle = color
  ctx.beginPath(); ctx.moveTo(x, y - s * 0.55); ctx.lineTo(x - s * 0.8, y + s * 0.55); ctx.lineTo(x + s * 0.8, y + s * 0.55); ctx.closePath(); ctx.fill()
  ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x - s * 0.62, y + s * 0.05); ctx.lineTo(x + s * 0.62, y + s * 0.05); ctx.closePath(); ctx.fill()
}

function person(ctx: CanvasRenderingContext2D, x: number, y: number, c: string, s = 1) {
  ctx.strokeStyle = c
  ctx.fillStyle = c
  ctx.lineWidth = 1.6 * s
  ctx.beginPath(); ctx.arc(x, y - 6.5 * s, 2.2 * s, 0, Math.PI * 2); ctx.fill()          // 头
  ctx.beginPath(); ctx.moveTo(x, y - 4 * s); ctx.lineTo(x, y + 2 * s); ctx.stroke()       // 躯干
  ctx.beginPath(); ctx.moveTo(x - 3.4 * s, y - 1 * s); ctx.lineTo(x + 3.4 * s, y - 1 * s); ctx.stroke() // 手
  ctx.beginPath(); ctx.moveTo(x, y + 2 * s); ctx.lineTo(x - 2.6 * s, y + 7 * s); ctx.stroke()            // 腿
  ctx.beginPath(); ctx.moveTo(x, y + 2 * s); ctx.lineTo(x + 2.6 * s, y + 7 * s); ctx.stroke()
}

function tent(ctx: CanvasRenderingContext2D, x: number, y: number, s: number) {
  ctx.fillStyle = '#c9a86a'
  ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x - s, y + s * 0.7); ctx.lineTo(x + s, y + s * 0.7); ctx.closePath(); ctx.fill()
  ctx.strokeStyle = '#8a6f42'; ctx.lineWidth = 1.2
  ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x, y + s * 0.7); ctx.stroke()
}

export default function SitMap({ scene, snapshot, terrain }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [mode, setMode] = useState<'2d' | '3d'>('2d')
  const stateRef = useRef<{ scene: Scene | null; snapshot: Snapshot | null; terrain: TerrainModel | null }>({ scene, snapshot, terrain })
  stateRef.current = { scene, snapshot, terrain }

  useEffect(() => {
    let raf = 0
    const draw = (ts: number) => {
      const canvas = canvasRef.current
      const { scene: sc, snapshot: snap, terrain: ter } = stateRef.current
      if (canvas && mode === '2d') {
        const dpr = window.devicePixelRatio || 1
        const rect = canvas.parentElement!.getBoundingClientRect()
        if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
          canvas.width = rect.width * dpr
          canvas.height = rect.height * dpr
        }
        const ctx = canvas.getContext('2d')!
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        render(ctx, rect.width, rect.height, sc, snap, ter, ts)
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [mode])

  const fire = snapshot?.fire
  return (
    <div className="sitmap">
      <div className="panel-title">林区态势{mode === '3d' ? ' · 三维模型' : ' · 战术地图'}
        <span className="view-toggle">
          <button className={mode === '2d' ? 'on' : ''} onClick={() => setMode('2d')}>🗺 平面</button>
          <button className={mode === '3d' ? 'on' : ''} onClick={() => setMode('3d')}>🏔 模型</button>
        </span>
        {fire && (
          <span className="fire-stat">
            B<sub>总</sub> = <b>{fire.total_flp}</b> FLP · 风 {fire.wind_speed} m/s · 人员 {fire.people_status}
          </span>
        )}
      </div>
      {mode === '3d' ? (
        <div className="canvas-wrap terrain-wrap">
          <Terrain3D scene={scene} snapshot={snapshot} terrain={terrain} />
          {terrain && <span className="terrain-src">紫金山实测高程 · 拖拽旋转 / 滚轮缩放</span>}
        </div>
      ) : (
        <div className="canvas-wrap"><canvas ref={canvasRef} /></div>
      )}
    </div>
  )
}

function render(ctx: CanvasRenderingContext2D, vw: number, vh: number, sc: Scene | null, snap: Snapshot | null, ter: TerrainModel | null, ts: number) {
  ctx.clearRect(0, 0, vw, vh)
  const scale = Math.min(vw / W, vh / H)
  const ox = (vw - W * scale) / 2, oy = (vh - H * scale) / 2
  const px = (x: number) => ox + x * scale
  const py = (y: number) => oy + y * scale

  // 底色 + 网格
  ctx.fillStyle = '#0c1712'
  ctx.fillRect(0, 0, vw, vh)
  ctx.strokeStyle = 'rgba(74, 138, 110, 0.10)'
  ctx.lineWidth = 1
  for (let x = 0; x <= W; x += 100) { ctx.beginPath(); ctx.moveTo(px(x), py(0)); ctx.lineTo(px(x), py(H)); ctx.stroke() }
  for (let y = 0; y <= H; y += 100) { ctx.beginPath(); ctx.moveTo(px(0), py(y)); ctx.lineTo(px(W), py(y)); ctx.stroke() }
  ctx.fillStyle = 'rgba(110, 140, 128, 0.4)'
  ctx.font = '9px Consolas, monospace'
  for (let x = 0; x < W; x += 200) ctx.fillText(String(x), px(x) + 3, py(0) + 12)
  for (let y = 0; y < H; y += 200) ctx.fillText(String(y), px(0) + 4, py(y) + 11)

  if (!sc) return

  // ---- 地势分层设色(真实高程, 低透明度打底) ----
  if (ter) {
    const cols = 20, rows = 14, span = ter.max_elev - ter.min_elev || 1
    for (let c = 0; c < cols; c++) {
      for (let r = 0; r < rows; r++) {
        const gx = Math.min(ter.nx - 1, Math.round((c * 100 + 50) / ter.scene_w * (ter.nx - 1)))
        const gy = Math.min(ter.ny - 1, Math.round((r * 100 + 50) / ter.scene_h * (ter.ny - 1)))
        const t = (ter.elevations[gy][gx] - ter.min_elev) / span
        const shade = 0.35 + t * 0.4
        ctx.fillStyle = t < 0.5 ? `rgba(30, 62, 44, ${(shade * 0.30).toFixed(3)})` : `rgba(74, 70, 50, ${(shade * 0.26).toFixed(3)})`
        ctx.fillRect(px(c * 100), py(r * 100), 100 * scale + 0.5, 100 * scale + 0.5)
      }
    }
  }

  // ---- 等高线(地形脊线示意) ----
  ctx.strokeStyle = 'rgba(94, 131, 113, 0.12)'
  ctx.lineWidth = 1.2
  for (let i = 0; i < 7; i++) {
    ctx.beginPath()
    const baseY = 140 + i * 170
    ctx.moveTo(px(60 + i * 90), py(baseY))
    ctx.bezierCurveTo(px(500 + i * 40), py(baseY - 130), px(1100 - i * 30), py(baseY + 120), px(1900 - i * 60), py(baseY - 40))
    ctx.stroke()
  }

  // ---- 装饰(森林/湖泊/溪流) ----
  if (!decorCache || decorCache.key !== sc.scene_id) {
    decorCache = { key: sc.scene_id || 'scene', decor: buildDecor(sc, ter) }
  }
  const decor = decorCache.decor
  const fireSet = new Set(((snap?.fire?.cells) ?? []).filter(c => c.flp > 0.01).map(c => `${c.cx},${c.cy}`))
  for (const t of decor.trees) {
    const cell = `${Math.floor(t.x / 100)},${Math.floor(t.y / 100)}`
    if (fireSet.has(cell)) {
      pine(ctx, px(t.x), py(t.y), t.s, '#3d372f') // 过火焦木
    } else {
      pine(ctx, px(t.x), py(t.y), t.s * scale * 2.6, t.c)
    }
  }
  // 溪流
  if (decor.stream.length > 1) {
    ctx.strokeStyle = '#3f7f9e'; ctx.lineWidth = Math.max(2.5, 5 * scale)
    ctx.beginPath()
    decor.stream.forEach(([x, y], i) => (i ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y))))
    ctx.stroke()
    ctx.strokeStyle = '#7cc4de'; ctx.lineWidth = Math.max(1, 2 * scale)
    ctx.stroke()
  }
  // 湖泊(有机形状 + 岸线 + 波纹)
  for (const lake of decor.lakes) {
    const grad = ctx.createRadialGradient(px(lake.x), py(lake.y), 4, px(lake.x), py(lake.y), 90 * scale)
    grad.addColorStop(0, '#2f6f8f')
    grad.addColorStop(1, '#1c4358')
    ctx.fillStyle = grad
    ctx.beginPath()
    lake.pts.forEach(([x, y], i) => (i ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y))))
    ctx.closePath(); ctx.fill()
    ctx.strokeStyle = '#7cc4de'; ctx.lineWidth = 1.4; ctx.stroke()
    ctx.strokeStyle = 'rgba(124, 196, 222, 0.4)'; ctx.lineWidth = 1
    for (let k = 0; k < 3; k++) {
      ctx.beginPath()
      ctx.arc(px(lake.x - 10 + k * 12), py(lake.y + k * 8 - 6), 12 * scale + k * 3, Math.PI * 0.15, Math.PI * 0.85)
      ctx.stroke()
    }
    ctx.fillStyle = '#8fd0e8'; ctx.font = '10px sans-serif'
    ctx.fillText(lake.name, px(lake.x) - 18, py(lake.y) - 70 * scale - 4)
  }

  // ---- 道路(土路虚线) / 禁飞格 ----
  ctx.setLineDash([])
  ctx.strokeStyle = 'rgba(201, 168, 106, 0.5)'
  ctx.lineWidth = Math.max(2, 4 * scale)
  for (const road of sc.roads ?? []) {
    ctx.beginPath()
    road.points.forEach(([x, y], i) => (i ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y))))
    ctx.stroke()
  }
  ctx.fillStyle = 'rgba(148, 163, 184, 0.16)'
  for (const cell of sc.restricted_cells ?? []) ctx.fillRect(px(cell.cx * 100), py(cell.cy * 100), 100 * scale, 100 * scale)

  // ---- 基地(指挥部图标) / FSP ----
  const drawStation = (x: number, y: number, label: string, color: string) => {
    ctx.fillStyle = color
    ctx.fillRect(px(x) - 9, py(y) - 7, 18, 12)
    ctx.beginPath(); ctx.moveTo(px(x) - 9, py(y) - 7); ctx.lineTo(px(x), py(y) - 15); ctx.lineTo(px(x) + 9, py(y) - 7); ctx.closePath(); ctx.fill()
    ctx.strokeStyle = '#0006'; ctx.lineWidth = 1; ctx.strokeRect(px(x) - 9, py(y) - 7, 18, 12)
    ctx.strokeStyle = color; ctx.lineWidth = 1.6
    ctx.beginPath(); ctx.moveTo(px(x) + 9, py(y) - 14); ctx.lineTo(px(x) + 20, py(y) - 14); ctx.stroke() // 旗杆旗
    ctx.fillStyle = color; ctx.font = 'bold 10px sans-serif'
    ctx.fillText(label, px(x) + 8, py(y) + 14)
  }
  drawStation(sc.base.x, sc.base.y, '基地', '#f0a848')
  drawStation(sc.forward_supply_point.x, sc.forward_supply_point.y, 'FSP-1', '#d9b44a')

  // ---- 出口 ----
  for (const exit of sc.exits ?? []) {
    ctx.fillStyle = '#5eead4'; ctx.font = 'bold 11px sans-serif'
    ctx.fillText(`🏁 ${exit.name}`, px(exit.x) + 6, py(exit.y) - 6)
  }

  // ---- 火情网格(径向热力 + 呼吸) ----
  const fire = snap?.fire
  const fireCentroid = { x: 0, y: 0, n: 0 }
  if (fire) {
    const maxFlp = Math.max(...fire.cells.map(c => c.flp), 1)
    for (const cell of fire.cells) {
      if (cell.flp <= 0.01) continue
      const pulse = 0.6 + 0.32 * Math.sin(ts / 350 + cell.cx + cell.cy)
      const alpha = Math.min(0.95, (cell.flp / maxFlp) * pulse + 0.42)
      const size = 100 * scale
      const cx0 = px(cell.x), cy0 = py(cell.y)
      const gradient = ctx.createRadialGradient(cx0, cy0, size * 0.08, cx0, cy0, size * 0.9)
      gradient.addColorStop(0, `rgba(255, 224, 160, ${Math.min(1, alpha + 0.3).toFixed(3)})`)
      gradient.addColorStop(0.45, `rgba(239, 68, 68, ${alpha.toFixed(3)})`)
      gradient.addColorStop(1, 'rgba(153, 27, 27, 0.06)')
      ctx.fillStyle = gradient
      ctx.fillRect(cx0 - size / 2, cy0 - size / 2, size, size)
      ctx.strokeStyle = `rgba(255, 120, 60, ${(Math.min(1, alpha + 0.25)).toFixed(3)})`
      ctx.lineWidth = 2
      ctx.strokeRect(cx0 - size / 2, cy0 - size / 2, size, size)
      fireCentroid.x += cell.x; fireCentroid.y += cell.y; fireCentroid.n++
    }
    // 火场中心十字标
    if (fireCentroid.n) {
      const fx = px(fireCentroid.x / fireCentroid.n), fy = py(fireCentroid.y / fireCentroid.n)
      ctx.strokeStyle = '#ffd9a0'; ctx.lineWidth = 1.8
      ctx.beginPath(); ctx.moveTo(fx - 14, fy); ctx.lineTo(fx + 14, fy); ctx.moveTo(fx, fy - 14); ctx.lineTo(fx, fy + 14); ctx.stroke()
      ctx.strokeStyle = '#ffd9a0aa'
      ctx.beginPath(); ctx.arc(fx, fy, 9, 0, Math.PI * 2); ctx.stroke()
      ctx.fillStyle = '#ffd9a0'; ctx.font = 'bold 11px sans-serif'
      ctx.fillText('火场中心', fx + 12, fy - 10)
    }
  }

  // ---- 疏散层: 路线 + 人群小人 ----
  const evac = (snap as any)?.support_plan?.evacuation
  if (evac && evac.path && evac.path.length > 1) {
    // 辉光底线 + 亮线
    const routeColor = evac.evacuated ? '#6fbf97' : '#5eead4'
    ctx.setLineDash([])
    ctx.strokeStyle = 'rgba(94, 234, 212, 0.18)'; ctx.lineWidth = 9
    ctx.beginPath()
    evac.path.forEach((pt: any, i: number) => (i ? ctx.lineTo(px(pt.x), py(pt.y)) : ctx.moveTo(px(pt.x), py(pt.y))))
    ctx.stroke()
    ctx.setLineDash([13, 8])
    ctx.strokeStyle = routeColor; ctx.lineWidth = 3.5
    ctx.stroke()
    ctx.setLineDash([])
    // 方向箭头
    ctx.fillStyle = routeColor
    for (let i = 2; i < evac.path.length; i += 3) {
      const a = evac.path[i - 1], b = evac.path[i]
      const ang = Math.atan2(py(b.y) - py(a.y), px(b.x) - px(a.x))
      const mx = (px(a.x) + px(b.x)) / 2, my = (py(a.y) + py(b.y)) / 2
      ctx.beginPath()
      ctx.moveTo(mx + Math.cos(ang) * 8, my + Math.sin(ang) * 8)
      ctx.lineTo(mx + Math.cos(ang + 2.5) * 7, my + Math.sin(ang + 2.5) * 7)
      ctx.lineTo(mx + Math.cos(ang - 2.5) * 7, my + Math.sin(ang - 2.5) * 7)
      ctx.closePath(); ctx.fill()
    }
  }
  // 人员: 露营区(常显: 帐篷+小人) / 疏散中(小人沿路线走)
  const zone = (sc.people_zones ?? [])[0]
  if (zone) {
    const peopleCount = evac?.people ?? zone.people
    if (evac?.path && !evac.evacuated) {
      const idx = Math.min(Math.floor(evac.progress_cells || 0), evac.path.length - 1)
      const here = evac.path[idx]
      const wob = Math.sin(ts / 260) * 2.5
      for (let k = 0; k < Math.min(peopleCount, 4); k++) {
        person(ctx, px(here.x) + (k - 1.5) * 13 + wob * (k % 2 ? 1 : -1), py(here.y) + (k % 2) * 8, '#ffcf8a', 1.15)
      }
      ctx.fillStyle = '#ffcf8a'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'center'
      ctx.fillText(`🧍${peopleCount} 人撤离中`, px(here.x), py(here.y) - 24)
      ctx.textAlign = 'left'
    } else if (!evac?.evacuated) {
      const zx = px(zone.cx * 100 + 50), zy = py(zone.cy * 100 + 50)
      tent(ctx, zx - 20, zy + 4, 10)
      tent(ctx, zx + 18, zy + 8, 8)
      for (let k = 0; k < Math.min(zone.people, 4); k++) person(ctx, zx + (k - 1) * 12, zy - 6 + (k % 2) * 5, '#ffcf8a')
      ctx.fillStyle = '#ffcf8a'; ctx.font = '10.5px sans-serif'; ctx.textAlign = 'center'
      ctx.fillText(`${zone.name} ×${zone.people}人`, zx, zy - 26)
      ctx.textAlign = 'left'
    } else {
      const exitPt = evac.path[evac.path.length - 1]
      ctx.fillStyle = '#6fbf97'; ctx.font = 'bold 11px sans-serif'
      ctx.fillText(`✅ ${evac.people}人已撤离`, px(exitPt.x) - 30, py(exitPt.y) - 22)
    }
  }

  // ---- 风场矢量 ----
  const windActive = !!snap?.fire
  const wdx = Math.cos((315 * Math.PI) / 180), wdy = Math.sin((315 * Math.PI) / 180)
  ctx.strokeStyle = 'rgba(103, 232, 249, 0.20)'
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

  // ---- 无人机(四旋翼 + 投影 + SOC 环) ----
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
    ctx.fillStyle = 'rgba(0,0,0,0.35)'
    ctx.beginPath(); ctx.ellipse(x + 6, y + 10, 9, 4, 0, 0, Math.PI * 2); ctx.fill()
    ctx.beginPath(); ctx.arc(x, y, 13, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * (uav.soc / 100))
    ctx.strokeStyle = uav.soc < 25 ? '#e07856' : meta.color; ctx.lineWidth = 2.6; ctx.stroke()
    ctx.beginPath(); ctx.arc(x, y, 13, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(140,160,150,0.22)'; ctx.lineWidth = 1; ctx.stroke()
    ctx.strokeStyle = meta.color; ctx.lineWidth = 1.6
    for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]] as const) {
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + dx * 8, y + dy * 8); ctx.stroke()
      ctx.beginPath(); ctx.arc(x + dx * 8, y + dy * 8, 3.4, 0, Math.PI * 2)
      ctx.strokeStyle = meta.color + 'aa'; ctx.lineWidth = 1.2; ctx.stroke()
    }
    ctx.fillStyle = meta.color
    ctx.beginPath(); ctx.arc(x, y, 5.5, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = '#ffffffc9'
    ctx.beginPath(); ctx.arc(x - 1.5, y - 1.5, 1.8, 0, Math.PI * 2); ctx.fill()
    if (uav.status === 'working') {
      ctx.beginPath(); ctx.arc(x, y, 16 + 3 * Math.sin(ts / 200), 0, Math.PI * 2)
      ctx.strokeStyle = meta.color + '77'; ctx.lineWidth = 1.5; ctx.stroke()
    }
    ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 10px Bahnschrift, sans-serif'
    ctx.fillText(uav.uav_id, x + 15, y - 9)
    ctx.fillStyle = '#94a3b8'; ctx.font = '9px Consolas, monospace'
    ctx.fillText(`${uav.soc.toFixed(0)}%`, x + 15, y + 3)
  }

  // ---- 空态提示 ----
  if (!snap?.fire && !fleet.length) {
    ctx.fillStyle = 'rgba(143, 163, 154, 0.65)'
    ctx.font = '13px "Microsoft YaHei UI", sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('等待任务 · 选择演练场景并点击「开始任务」', px(W / 2), py(H / 2) - 8)
    ctx.fillStyle = 'rgba(143, 163, 154, 0.4)'
    ctx.font = '10.5px Consolas, monospace'
    ctx.fillText('紫金山北麓演示林区 · 2000m × 1400m · 100m² 网格', px(W / 2), py(H / 2) + 14)
    ctx.textAlign = 'left'
  }
}
