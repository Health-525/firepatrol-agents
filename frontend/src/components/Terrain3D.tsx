import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { Scene, Snapshot } from '../types'
import { SUBGROUP_META } from '../types'

export interface TerrainModel {
  nx: number; ny: number; cell_m: number
  scene_w: number; scene_h: number
  min_elev: number; max_elev: number
  exaggeration: number
  source: string
  elevations: number[][]
}

interface Props { scene: Scene | null; snapshot: Snapshot | null; terrain: TerrainModel | null }

const EX = 2.2 // 垂直夸张(与后端一致)

function elev(terrain: TerrainModel, x: number, y: number): number {
  const gx = Math.min(terrain.nx - 1, Math.max(0, Math.round(x / terrain.scene_w * (terrain.nx - 1))))
  const gy = Math.min(terrain.ny - 1, Math.max(0, Math.round(y / terrain.scene_h * (terrain.ny - 1))))
  return terrain.elevations[gy][gx]
}

// 场景坐标(米) -> three 世界(x 右 / y 上 / z 屏幕向内)
function toWorld(terrain: TerrainModel, x: number, y: number): [number, number] {
  return [x - terrain.scene_w / 2, y - terrain.scene_h / 2]
}

const COL_VALLEY = new THREE.Color('#1e3a2b')
const COL_MID = new THREE.Color('#33503a')
const COL_HIGH = new THREE.Color('#5c5a41')
const COL_PEAK = new THREE.Color('#7d7466')

function terrainColor(t: number, slope: number): THREE.Color {
  const c = t < 0.45 ? COL_VALLEY.clone().lerp(COL_MID, t / 0.45)
    : t < 0.8 ? COL_MID.clone().lerp(COL_HIGH, (t - 0.45) / 0.35)
    : COL_HIGH.clone().lerp(COL_PEAK, (t - 0.8) / 0.2)
  return c.lerp(new THREE.Color('#0e1713'), Math.min(1, slope) * 0.35)
}

export default function Terrain3D({ scene, snapshot, terrain }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef<Props>({ scene, snapshot, terrain })
  stateRef.current = { scene, snapshot, terrain }
  const coreRef = useRef<{
    renderer: THREE.WebGLRenderer
    camera: THREE.PerspectiveCamera
    controls: OrbitControls
    world: THREE.Group
    dynamic: THREE.Group
  } | null>(null)

  // ---------- 初始化(一次) ----------
  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setClearColor('#08120e')
    mount.appendChild(renderer.domElement)
    const camera = new THREE.PerspectiveCamera(48, 1, 1, 8000)
    camera.position.set(780, 760, 980)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.45
    controls.target.set(0, 180, 0)
    controls.maxDistance = 3400
    controls.minDistance = 160

    const threeScene = new THREE.Scene()
    threeScene.fog = new THREE.Fog('#08120e', 2300, 4400)
    threeScene.add(new THREE.AmbientLight('#cfe8dc', 0.55))
    const sun = new THREE.DirectionalLight('#ffd9a3', 1.15)
    sun.position.set(-900, 1200, 600)
    threeScene.add(sun)
    const fill = new THREE.DirectionalLight('#5f8fca', 0.35)
    fill.position.set(800, 500, -900)
    threeScene.add(fill)

    const world = new THREE.Group()
    const dynamic = new THREE.Group()
    threeScene.add(world, dynamic)
    coreRef.current = { renderer, camera, controls, world, dynamic }

    let raf = 0
    const render = (ts: number) => {
      const core = coreRef.current
      if (core) {
        for (const child of core.dynamic.children) {
          const meta = child.userData as Record<string, any>
          if (meta.fire) {
            const pulse = 0.75 + 0.25 * Math.sin(ts / 320 + meta.seed)
            const mat = (child as THREE.Mesh).material as THREE.MeshStandardMaterial
            mat.emissiveIntensity = 1.15 * pulse * meta.strength
          }
          if (meta.drone && meta.target) {
            meta.cur.x += (meta.target.x - meta.cur.x) * 0.06
            meta.cur.y += (meta.target.y - meta.cur.y) * 0.06
            meta.cur.z += (meta.target.z - meta.cur.z) * 0.06
            child.position.set(meta.cur.x, meta.cur.y, meta.cur.z)
            child.rotation.y += 0.04
            const line = meta.line as THREE.Line
            line.geometry.setFromPoints([
              new THREE.Vector3(meta.cur.x, meta.cur.y, meta.cur.z),
              new THREE.Vector3(meta.cur.x, meta.groundY, meta.cur.z),
            ])
            ;(line.material as THREE.LineDashedMaterial) && (line as any).computeLineDistances?.()
          }
        }
        controls.update()
        renderer.render(threeScene, camera)
      }
      raf = requestAnimationFrame(render)
    }
    raf = requestAnimationFrame(render)

    const resize = () => {
      const rect = mount.getBoundingClientRect()
      if (rect.width < 8 || rect.height < 8) return
      renderer.setSize(rect.width, rect.height, false)
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
      camera.aspect = rect.width / rect.height
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()
    return () => {
      cancelAnimationFrame(raf); observer.disconnect(); controls.dispose()
      renderer.dispose(); mount.removeChild(renderer.domElement)
      coreRef.current = null
    }
  }, [])

  // ---------- 地形网格 + 静态标记(terrain/scene 变化时重建) ----------
  useEffect(() => {
    const core = coreRef.current
    if (!core || !terrain || !scene) return
    core.world.clear()
    const { nx, ny, scene_w, scene_h } = terrain
    const geo = new THREE.PlaneGeometry(scene_w, scene_h, nx - 1, ny - 1)
    geo.rotateX(-Math.PI / 2)
    const pos = geo.attributes.position as THREE.BufferAttribute
    const colors: number[] = []
    const span = terrain.max_elev - terrain.min_elev || 1
    for (let i = 0; i < pos.count; i++) {
      const gx = i % nx, gy = Math.floor(i / nx)
      const row = ny - 1 - gy // 数据第 0 行 = 场景 y=0(南) -> plane 局部 z+ 为北
      const h = terrain.elevations[row]?.[gx] ?? terrain.min_elev
      pos.setY(i, h * EX)
      const hRight = terrain.elevations[row]?.[gx + 1] ?? h
      const slope = Math.abs(h - hRight) / 25
      const c = terrainColor((h - terrain.min_elev) / span, slope)
      colors.push(c.r, c.g, c.b)
    }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
    geo.computeVertexNormals()
    core.world.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, metalness: 0.05 })))

    // 基地 / 前向补给点
    const base = new THREE.Mesh(new THREE.CylinderGeometry(14, 20, 28, 6),
      new THREE.MeshStandardMaterial({ color: '#f0a848', emissive: '#8a5a1e', emissiveIntensity: 0.5 }))
    const [bx, bz] = toWorld(terrain, scene.base.x, scene.base.y)
    base.position.set(bx, elev(terrain, scene.base.x, scene.base.y) * EX + 14, bz)
    core.world.add(base)
    const fsp = new THREE.Mesh(new THREE.CylinderGeometry(9, 13, 20, 6),
      new THREE.MeshStandardMaterial({ color: '#d9b44a', emissive: '#6b5220', emissiveIntensity: 0.4 }))
    const [fx, fz] = toWorld(terrain, scene.forward_supply_point.x, scene.forward_supply_point.y)
    fsp.position.set(fx, elev(terrain, scene.forward_supply_point.x, scene.forward_supply_point.y) * EX + 10, fz)
    core.world.add(fsp)

    // 水源
    for (const ws of scene.water_sources ?? []) {
      const disc = new THREE.Mesh(new THREE.CircleGeometry(26, 24),
        new THREE.MeshStandardMaterial({ color: '#57a8c9', emissive: '#2b6f8f', emissiveIntensity: 0.55, transparent: true, opacity: 0.9 }))
      disc.rotation.x = -Math.PI / 2
      const [wx, wz] = toWorld(terrain, ws.x, ws.y)
      disc.position.set(wx, elev(terrain, ws.x, ws.y) * EX + 2, wz)
      core.world.add(disc)
    }

    // 道路(贴地折线)
    for (const road of scene.roads ?? []) {
      const pts: THREE.Vector3[] = []
      for (let i = 0; i < road.points.length - 1; i++) {
        const [x1, y1] = road.points[i]
        const [x2, y2] = road.points[i + 1]
        for (let s = 0; s <= 14; s++) {
          const x = x1 + (x2 - x1) * s / 14, y = y1 + (y2 - y1) * s / 14
          const [wx, wz] = toWorld(terrain, x, y)
          pts.push(new THREE.Vector3(wx, elev(terrain, x, y) * EX + 4, wz))
        }
      }
      core.world.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: '#9fb8ac', transparent: true, opacity: 0.55 })))
    }
  }, [terrain, scene])

  // ---------- 动态层: 火场重建 + 无人机增量更新(平滑插值) ----------
  useEffect(() => {
    const core = coreRef.current
    if (!core || !terrain) return
    // 火场格子: 每次火情变化重建
    for (const child of [...core.dynamic.children]) {
      if ((child.userData as Record<string, any>)?.fire) core.dynamic.remove(child)
    }
    const fire = snapshot?.fire
    if (fire) {
      const maxFlp = Math.max(...fire.cells.map(c => c.flp), 1)
      fire.cells.filter(c => c.flp > 0.01).forEach((cell, i) => {
        const strength = Math.min(1, cell.flp / maxFlp)
        const box = new THREE.Mesh(new THREE.BoxGeometry(94, 14 + 26 * strength, 94),
          new THREE.MeshStandardMaterial({ color: '#b91c1c', emissive: '#f6723a', emissiveIntensity: 1, transparent: true, opacity: 0.88 }))
        const [wx, wz] = toWorld(terrain, cell.x, cell.y)
        const ground = elev(terrain, cell.x, cell.y) * EX
        box.position.set(wx, ground + (14 + 26 * strength) / 2, wz)
        box.userData = { fire: true, seed: i * 1.7, strength }
        core.dynamic.add(box)
      })
    }
    // 无人机: 存在则更新目标, 不存在则创建; 消失则移除
    const seen = new Set<string>()
    for (const uav of snapshot?.fleet ?? []) {
      seen.add(uav.uav_id)
      const name = `drone-${uav.uav_id}`
      let body = core.dynamic.getObjectByName(name) as THREE.Mesh | undefined
      const meta = SUBGROUP_META[uav.subgroup] ?? { color: '#94a3b8', label: uav.subgroup, short: '?' }
      const [wx, wz] = toWorld(terrain, uav.position.x, uav.position.y)
      const groundY = elev(terrain, uav.position.x, uav.position.y) * EX
      const alt = groundY + 60 * EX + (uav.position.z || 0) * 0.6
      if (!body) {
        body = new THREE.Mesh(new THREE.OctahedronGeometry(16),
          new THREE.MeshStandardMaterial({ color: meta.color, emissive: meta.color, emissiveIntensity: 0.55 }))
        body.name = name
        const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(wx, alt, wz), new THREE.Vector3(wx, groundY, wz)])
        const line = new THREE.Line(lineGeo, new THREE.LineDashedMaterial({ color: meta.color, transparent: true, opacity: 0.35, dashSize: 12, gapSize: 10 }))
        line.computeLineDistances()
        core.dynamic.add(line)
        body.userData = { drone: true, cur: { x: wx, y: alt, z: wz }, groundY, line }
        body.position.set(wx, alt, wz)
        core.dynamic.add(body)
      }
      const data = body.userData as Record<string, any>
      data.target = { x: wx, y: alt, z: wz }
      data.groundY = groundY
    }
    for (const child of [...core.dynamic.children]) {
      const data = child.userData as Record<string, any>
      if (data?.drone && !seen.has(child.name.replace('drone-', ''))) {
        if (data.line) core.dynamic.remove(data.line)
        core.dynamic.remove(child)
      }
    }
  }, [snapshot?.fire, snapshot?.fleet, terrain])

  return <div className="terrain3d" ref={mountRef} />
}
