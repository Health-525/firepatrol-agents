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

const EX = 2.2

function elev(terrain: TerrainModel, x: number, y: number): number {
  const gx = Math.min(terrain.nx - 1, Math.max(0, Math.round(x / terrain.scene_w * (terrain.nx - 1))))
  const gy = Math.min(terrain.ny - 1, Math.max(0, Math.round(y / terrain.scene_h * (terrain.ny - 1))))
  return terrain.elevations[gy][gx]
}
function toWorld(terrain: TerrainModel, x: number, y: number): [number, number] {
  return [x - terrain.scene_w / 2, y - terrain.scene_h / 2]
}

// ---------- 程序化纹理 ----------
function radialTexture(inner: string, outer: string, size = 128): THREE.Texture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  const g = ctx.createRadialGradient(size / 2, size / 2, 2, size / 2, size / 2, size / 2)
  g.addColorStop(0, inner)
  g.addColorStop(0.35, inner)
  g.addColorStop(1, outer)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}
function textSprite(text: string, color: string, scale = 1): THREE.Sprite {
  const canvas = document.createElement('canvas')
  canvas.width = 256; canvas.height = 96
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = 'rgba(8,16,12,0.72)'
  ctx.beginPath(); ctx.roundRect(6, 22, 244, 52, 12); ctx.fill()
  ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.stroke()
  ctx.fillStyle = color
  ctx.font = 'bold 38px Bahnschrift, "Microsoft YaHei", sans-serif'
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.fillText(text, 128, 50)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }))
  sprite.scale.set(56 * scale, 21 * scale, 1)
  return sprite
}

const COL_VALLEY = new THREE.Color('#22402f')
const COL_MID = new THREE.Color('#3b5741')
const COL_HIGH = new THREE.Color('#6b6247')
const COL_PEAK = new THREE.Color('#8a8172')
const SUN_DIR = new THREE.Vector3(-0.55, 0.75, 0.35).normalize()

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

  // ---------- 初始化 ----------
  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setClearColor('#0a1410')
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.35
    mount.appendChild(renderer.domElement)
    const camera = new THREE.PerspectiveCamera(46, 1, 1, 6500)
    camera.position.set(760, 640, 1120)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.4
    controls.target.set(0, 200, 0)
    controls.maxDistance = 3400
    controls.minDistance = 160

    const threeScene = new THREE.Scene()
    threeScene.fog = new THREE.Fog('#1c2822', 2600, 5400)
    // 天穹: 黄昏渐变球(内壁, 不依赖 scene.background 机制)
    const skyCanvas = document.createElement('canvas')
    skyCanvas.width = 8; skyCanvas.height = 512
    const sctx = skyCanvas.getContext('2d')!
    const grad = sctx.createLinearGradient(0, 0, 0, 512)
    grad.addColorStop(0, '#0e2018')
    grad.addColorStop(0.42, '#24382c')
    grad.addColorStop(0.66, '#6b4a22')
    grad.addColorStop(0.8, '#3a2c1a')
    grad.addColorStop(1, '#10100c')
    sctx.fillStyle = grad; sctx.fillRect(0, 0, 8, 512)
    const skyTex = new THREE.CanvasTexture(skyCanvas)
    skyTex.colorSpace = THREE.SRGBColorSpace
    const skyDome = new THREE.Mesh(
      new THREE.SphereGeometry(4200, 32, 20),
      new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, fog: false }))
    threeScene.add(skyDome)

    threeScene.add(new THREE.HemisphereLight('#9db8a8', '#1a241e', 0.5))
    const sun = new THREE.DirectionalLight('#ffd9a3', 1.5)
    sun.position.set(-900, 1200, 600)
    threeScene.add(sun)
    const fill = new THREE.DirectionalLight('#5f8fca', 0.3)
    fill.position.set(800, 500, -900)
    threeScene.add(fill)

    const world = new THREE.Group()
    const dynamic = new THREE.Group()
    threeScene.add(world, dynamic)
    coreRef.current = { renderer, camera, controls, world, dynamic }

    const fireTex = radialTexture('rgba(255,214,140,1)', 'rgba(255,90,20,0)')
    const smokeTex = radialTexture('rgba(120,120,120,0.55)', 'rgba(80,80,80,0)')

    let raf = 0
    const render = (ts: number) => {
      const core = coreRef.current
      const ter = stateRef.current.terrain
      try {
        if (core && ter) {
        for (const child of core.dynamic.children) {
          const meta = child.userData as Record<string, any>
          if (meta.fireSprite) {
            const flicker = 0.72 + 0.28 * Math.sin(ts / 130 + meta.seed * 7)
            const s = meta.baseScale * flicker
            child.scale.set(s, s * 1.25, 1)
            const spriteMat = (child as THREE.Sprite).material as THREE.SpriteMaterial
            spriteMat.opacity = meta.baseOpacity * flicker
            child.position.y = meta.baseY + Math.sin(ts / 460 + meta.seed * 3) * 9
          }
          if (meta.smoke) {
            const cycle = ((ts / 5200 + meta.seed) % 1)
            child.position.set(meta.x + Math.sin(cycle * 6.28 + meta.seed) * 26, meta.y0 + cycle * 240, meta.z + Math.cos(cycle * 5 + meta.seed) * 20)
            const s = 90 + cycle * 220
            child.scale.set(s, s, 1)
            const smokeMat = (child as THREE.Sprite).material as THREE.SpriteMaterial
            smokeMat.opacity = 0.34 * (1 - cycle) * (0.6 + 0.4 * Math.sin(cycle * 3.14))
          }
          if (meta.fireLight) {
            ;(child as THREE.PointLight).intensity = 4.2 + 1.6 * Math.sin(ts / 90 + meta.seed) * Math.cos(ts / 230)
          }
          if (meta.rotors && Array.isArray(meta.rotors)) {
            for (const rotor of meta.rotors) rotor.rotation.y += 0.55
          }
          if (meta.drone && meta.target) {
            meta.cur.x += (meta.target.x - meta.cur.x) * 0.06
            meta.cur.y += (meta.target.y - meta.cur.y) * 0.06
            meta.cur.z += (meta.target.z - meta.cur.z) * 0.06
            child.position.set(meta.cur.x, meta.cur.y, meta.cur.z)
            if (meta.beacon) meta.beacon.position.set(meta.cur.x, meta.groundY + 4, meta.cur.z)
          }
          if (meta.ripple) {
            const cycle = ((ts / 2600) + meta.seed) % 1
            const s = 26 + cycle * 46
            child.scale.set(s, s, 1)
            const rippleMat = (child as THREE.Mesh).material as THREE.MeshBasicMaterial
            rippleMat.opacity = 0.5 * (1 - cycle)
          }
        }
        void fireTex; void smokeTex
        controls.update()
        renderer.render(threeScene, camera)
        }
      } catch (error) {
        (window as any).__t3d_error = String(error)
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

  // ---------- 地形(等高线着色器 + 山体阴影烘焙) ----------
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
    const height = (r: number, c: number) => terrain.elevations[Math.max(0, Math.min(ny - 1, r))]?.[Math.max(0, Math.min(nx - 1, c))] ?? terrain.min_elev
    const elevArr: number[] = []
    for (let i = 0; i < pos.count; i++) {
      const gx = i % nx, gy = Math.floor(i / nx)
      const row = ny - 1 - gy
      const h = height(row, gx)
      elevArr.push(h)
      pos.setY(i, h * EX)
    }
    for (let i = 0; i < pos.count; i++) {
      const gx = i % nx, gy = Math.floor(i / nx)
      const row = ny - 1 - gy
      const h = elevArr[i]
      const t = (h - terrain.min_elev) / span
      const c = t < 0.45 ? COL_VALLEY.clone().lerp(COL_MID, t / 0.45)
        : t < 0.8 ? COL_MID.clone().lerp(COL_HIGH, (t - 0.45) / 0.35)
        : COL_HIGH.clone().lerp(COL_PEAK, (t - 0.8) / 0.2)
      // 山体阴影: 邻格梯度与太阳方向的点积
      const dhx = height(row, gx + 1) - height(row, gx - 1)
      const dhy = height(row + 1, gx) - height(row - 1, gx)
      const normal = new THREE.Vector3(-dhx * EX, 40, -dhy * EX).normalize()
      const shade = 0.62 + 0.5 * Math.max(0, normal.dot(SUN_DIR))
      c.multiplyScalar(shade)
      colors.push(c.r, c.g, c.b)
    }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
    geo.computeVertexNormals()
    const ground = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.96, metalness: 0.02 }))
    ground.receiveShadow = true
    core.world.add(ground)

    // 等高线(marching squares): 每 40m 一条, 线性插值提取, 比 shader 注入更稳
    const contourMat = new THREE.LineBasicMaterial({ color: '#9fb8a8', transparent: true, opacity: 0.28 })
    const cellW = scene_w / (nx - 1), cellH = scene_h / (ny - 1)
    const gridX = (c: number) => c * cellW - scene_w / 2
    const gridZ = (r: number) => r * cellH - scene_h / 2
    const hAt = (r: number, c: number) => elevArr[(ny - 1 - r) * nx + c] ?? terrain.min_elev
    const yAt = (r: number, c: number) => hAt(r, c) * EX
    for (let level = Math.ceil(terrain.min_elev / 40) * 40; level <= terrain.max_elev; level += 40) {
      const pts: THREE.Vector3[] = []
      for (let r = 0; r < ny - 1; r++) {
        for (let c = 0; c < nx - 1; c++) {
          const corners: Array<[number, number, number]> = [
            [gridX(c), gridZ(r), yAt(r, c)], [gridX(c + 1), gridZ(r), yAt(r, c + 1)],
            [gridX(c + 1), gridZ(r + 1), yAt(r + 1, c + 1)], [gridX(c), gridZ(r + 1), yAt(r + 1, c)]]
          const vals = [hAt(r, c), hAt(r, c + 1), hAt(r + 1, c + 1), hAt(r + 1, c)]
          for (let e = 0; e < 4; e++) {
            const e2 = (e + 1) % 4
            const [v1, v2] = [vals[e], vals[e2]]
            if ((v1 < level) !== (v2 < level)) {
              const t = (level - v1) / (v2 - v1 || 1)
              pts.push(new THREE.Vector3(
                corners[e][0] + (corners[e2][0] - corners[e][0]) * t,
                (corners[e][2] + (corners[e2][2] - corners[e][2]) * t) + 2.5,
                corners[e][1] + (corners[e2][1] - corners[e][1]) * t))
            }
          }
        }
      }
      if (pts.length > 1) core.world.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), contourMat))
    }

    // 底裙边: 场景外一圈暗色大地, 避免"悬浮孤岛"感
    const skirt = new THREE.Mesh(
      new THREE.CylinderGeometry(Math.max(scene_w, scene_h) * 0.95, Math.max(scene_w, scene_h) * 0.95, 220, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: '#0c1512', side: THREE.BackSide }))
    skirt.position.y = terrain.min_elev * EX - 110
    core.world.add(skirt)

    // 基地: 台座 + 发光环 + 标注
    const makeStation = (x: number, y: number, label: string, color: string, size: number) => {
      const group = new THREE.Group()
      const [wx, wz] = toWorld(terrain, x, y)
      const gy = elev(terrain, x, y) * EX
      const pillar = new THREE.Mesh(new THREE.CylinderGeometry(size * 0.5, size * 0.72, size * 1.7, 8),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.45, roughness: 0.5 }))
      pillar.position.y = gy + size * 0.85
      group.add(pillar)
      const ring = new THREE.Mesh(new THREE.RingGeometry(size * 0.9, size * 1.25, 36),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }))
      ring.rotation.x = -Math.PI / 2
      ring.position.y = gy + 3
      group.add(ring)
      const tag = textSprite(label, color, size / 16)
      tag.position.set(wx, gy + size * 2.4, wz)
      group.add(tag)
      group.position.set(wx, 0, wz)
      return group
    }
    core.world.add(makeStation(scene.base.x, scene.base.y, '基地', '#f0a848', 22))
    core.world.add(makeStation(scene.forward_supply_point.x, scene.forward_supply_point.y, 'FSP-1', '#d9b44a', 15))

    // 水源: 水面 + 涟漪
    for (const ws of scene.water_sources ?? []) {
      const [wx, wz] = toWorld(terrain, ws.x, ws.y)
      const gy = elev(terrain, ws.x, ws.y) * EX
      const disc = new THREE.Mesh(new THREE.CircleGeometry(28, 28),
        new THREE.MeshStandardMaterial({ color: '#3f7f9e', emissive: '#1d4a60', emissiveIntensity: 0.6, transparent: true, opacity: 0.92, roughness: 0.15, metalness: 0.4 }))
      disc.rotation.x = -Math.PI / 2
      disc.position.set(wx, gy + 2.5, wz)
      core.world.add(disc)
      for (let k = 0; k < 2; k++) {
        const ripple = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 40),
          new THREE.MeshBasicMaterial({ color: '#8fd0e8', transparent: true, opacity: 0.4, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }))
        ripple.rotation.x = -Math.PI / 2
        ripple.position.set(wx, gy + 4, wz)
        ripple.userData = { ripple: true, seed: k * 0.5 }
        core.world.add(ripple)
      }
      const tag = textSprite(ws.name, '#8fd0e8', 0.8)
      tag.position.set(wx, gy + 58, wz)
      core.world.add(tag)
    }

    // 道路: 贴地发光虚线
    for (const road of scene.roads ?? []) {
      const pts: THREE.Vector3[] = []
      for (let i = 0; i < road.points.length - 1; i++) {
        const [x1, y1] = road.points[i]
        const [x2, y2] = road.points[i + 1]
        for (let s = 0; s <= 14; s++) {
          const x = x1 + (x2 - x1) * s / 14, y = y1 + (y2 - y1) * s / 14
          const [wx, wz] = toWorld(terrain, x, y)
          pts.push(new THREE.Vector3(wx, elev(terrain, x, y) * EX + 5, wz))
        }
      }
      core.world.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineDashedMaterial({ color: '#c8d8cf', transparent: true, opacity: 0.55, dashSize: 22, gapSize: 16 })))
    }
    // 让所有 Line 计算虚线距离
    core.world.traverse(o => { if ((o as THREE.Line).isLine) (o as THREE.Line).computeLineDistances() })
  }, [terrain, scene])

  // ---------- 动态层: 火焰粒子 + 烟 + 火光 / 四旋翼无人机 ----------
  useEffect(() => {
    const core = coreRef.current
    if (!core || !terrain) return
    for (const child of [...core.dynamic.children]) {
      const data = child.userData as Record<string, any>
      if (data.fireSprite || data.smoke || data.fireLight || data.drone) core.dynamic.remove(child)
    }
    const fire = snapshot?.fire
    if (fire) {
      const fireTex = radialTexture('rgba(255,220,150,1)', 'rgba(255,80,20,0)')
      const smokeTex = radialTexture('rgba(125,125,125,0.5)', 'rgba(80,80,80,0)')
      const burning = fire.cells.filter(c => c.flp > 0.01)
      const maxFlp = Math.max(...burning.map(c => c.flp), 1)
      for (const [idx, cell] of burning.entries()) {
        const [wx, wz] = toWorld(terrain, cell.x, cell.y)
        const gy = elev(terrain, cell.x, cell.y) * EX
        const strength = Math.min(1, cell.flp / maxFlp)
        for (let k = 0; k < 3 + Math.round(strength * 3); k++) {
          const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
            map: fireTex, color: k % 2 ? '#ffb35c' : '#ff7a2e', transparent: true,
            blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.85 }))
          const seed = idx + k * 0.37
          const baseScale = 55 + strength * 60
          sprite.position.set(wx + (seed % 1 - 0.5) * 80, gy + 30 + (k % 3) * 26, wz + ((seed * 7) % 1 - 0.5) * 80)
          sprite.userData = { fireSprite: true, seed, baseScale, baseOpacity: 0.9 - k * 0.12, baseY: sprite.position.y }
          core.dynamic.add(sprite)
        }
        const smoke = new THREE.Sprite(new THREE.SpriteMaterial({ map: smokeTex, transparent: true, opacity: 0.3, depthWrite: false }))
        smoke.userData = { smoke: true, seed: idx * 0.61, x: wx, y0: gy + 60, z: wz }
        smoke.position.set(wx, gy + 60, wz)
        core.dynamic.add(smoke)
        if (idx < 3) { // 火光点光源(限量, 照亮地形)
          const light = new THREE.PointLight('#ff7a2e', 5, 460, 1.6)
          light.position.set(wx, gy + 55, wz)
          light.userData = { fireLight: true, seed: idx }
          core.dynamic.add(light)
        }
      }
    }
    // 四旋翼无人机: 机身+机臂+旋翼(旋转)+光标+编号牌
    const seen = new Set<string>()
    for (const uav of snapshot?.fleet ?? []) {
      seen.add(uav.uav_id)
      const name = `drone-${uav.uav_id}`
      let group = core.dynamic.getObjectByName(name) as THREE.Group | undefined
      const meta = SUBGROUP_META[uav.subgroup] ?? { color: '#94a3b8', label: uav.subgroup, short: '?' }
      const [wx, wz] = toWorld(terrain, uav.position.x, uav.position.y)
      const groundY = elev(terrain, uav.position.x, uav.position.y) * EX
      const alt = groundY + 60 * EX + (uav.position.z || 0) * 0.6
      if (!group) {
        group = new THREE.Group()
        group.name = name
        const mat = new THREE.MeshStandardMaterial({ color: meta.color, emissive: meta.color, emissiveIntensity: 0.35, roughness: 0.4 })
        const body = new THREE.Mesh(new THREE.BoxGeometry(11, 5, 15), mat)
        group.add(body)
        const nose = new THREE.Mesh(new THREE.BoxGeometry(5, 4, 5), new THREE.MeshStandardMaterial({ color: '#e8f2ec', emissive: '#ffffff', emissiveIntensity: 0.25 }))
        nose.position.z = 9
        group.add(nose)
        const rotorMat = new THREE.MeshStandardMaterial({ color: meta.color, transparent: true, opacity: 0.4 })
        const rotors: THREE.Mesh[] = []
        for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]] as const) {
          const arm = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.4, 11), mat)
          arm.position.set(dx * 4.5, 1.5, dz * 4.5)
          arm.rotation.y = Math.atan2(dx, dz)
          group.add(arm)
          const rotor = new THREE.Mesh(new THREE.CylinderGeometry(6.5, 6.5, 0.7, 20), rotorMat)
          rotor.position.set(dx * 8.5, 2.6, dz * 8.5)
          group.add(rotor)
          rotors.push(rotor)
        }
        // 地面光标(垂线落点)
        const beacon = new THREE.Mesh(new THREE.CircleGeometry(10, 22),
          new THREE.MeshBasicMaterial({ color: meta.color, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }))
        beacon.rotation.x = -Math.PI / 2
        core.dynamic.add(beacon)
        const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(wx, alt, wz), new THREE.Vector3(wx, groundY, wz)])
        const line = new THREE.Line(lineGeo, new THREE.LineDashedMaterial({ color: meta.color, transparent: true, opacity: 0.32, dashSize: 12, gapSize: 10 }))
        line.computeLineDistances()
        core.dynamic.add(line)
        const tag = textSprite(uav.uav_id, meta.color, 0.55)
        tag.position.y = 26
        group.add(tag)
        group.userData = { drone: true, cur: { x: wx, y: alt, z: wz }, groundY, rotors, beacon, line }
        group.position.set(wx, alt, wz)
        core.dynamic.add(group)
      }
      const data = group.userData as Record<string, any>
      data.target = { x: wx, y: alt, z: wz }
      data.groundY = groundY
    }
    for (const child of [...core.dynamic.children]) {
      const data = child.userData as Record<string, any>
      if (data?.drone && !seen.has(child.name.replace('drone-', ''))) {
        if (data.beacon) core.dynamic.remove(data.beacon)
        if (data.line) core.dynamic.remove(data.line)
        core.dynamic.remove(child)
      }
    }
  }, [snapshot?.fire, snapshot?.fleet, terrain])

  return <div className="terrain3d" ref={mountRef} />
}
