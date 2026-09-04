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

// ---------- 地面路线(消防车沿巡护道行驶) ----------
// 消防车配置(编号 / 道路侧向偏移 / 车速)与出动阶段: 审批通过进入执行后前出
const TRUCK_CFG = [
  { id: 'truck-01', label: '消防01', side: 11, speed: 0.42 },
  { id: 'truck-02', label: '消防02', side: -11, speed: 0.30 },
] as const
const truckPhases = ['executing', 'replanning', 'completed']
interface RoutePt { x: number; y: number; z: number; c: number }
interface TruckRoute { pts: RoutePt[]; total: number }
function buildTruckRoute(terrain: TerrainModel, polyline: number[][]): TruckRoute {
  const pts: RoutePt[] = polyline.map(([x, y]) => {
    const [wx, wz] = toWorld(terrain, x, y)
    return { x: wx, y: elev(terrain, x, y) * EX, z: wz, c: 0 }
  })
  let acc = 0
  for (let i = 0; i < pts.length; i++) {
    pts[i].c = acc
    if (i < pts.length - 1) acc += Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].z - pts[i].z)
  }
  return { pts, total: acc }
}
function routeAt(route: TruckRoute, d: number) {
  const dist = Math.max(0, Math.min(d, route.total))
  for (let i = 0; i < route.pts.length - 1; i++) {
    const a = route.pts[i], b = route.pts[i + 1]
    const seg = b.c - a.c
    if (dist <= b.c || i === route.pts.length - 2) {
      const t = seg > 0 ? Math.max(0, (dist - a.c) / seg) : 0
      return {
        x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t,
        dx: (b.x - a.x) / (seg || 1), dz: (b.z - a.z) / (seg || 1),
      }
    }
  }
  const last = route.pts[route.pts.length - 1]
  return { x: last.x, y: last.y, z: last.z, dx: 0, dz: 1 }
}

// ---------- 四旋翼无人机建模(机身 + 桨盘 + 航行灯 + 子群专属挂载) ----------
function buildDrone(color: string, subgroup: string): { group: THREE.Group; rotors: THREE.Object3D[]; spray: THREE.Mesh | null } {
  const group = new THREE.Group()
  const matAir = new THREE.MeshStandardMaterial({ color: '#3a4450', roughness: 0.42, metalness: 0.45 })
  const matDark = new THREE.MeshStandardMaterial({ color: '#171c22', roughness: 0.55, metalness: 0.3 })
  const matAccent = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.5, roughness: 0.35 })
  const matGlass = new THREE.MeshStandardMaterial({ color: '#0d141c', emissive: color, emissiveIntensity: 0.3, roughness: 0.15, metalness: 0.65 })
  // 机身: 主舱 + 座舱穹顶 + 腹板 + 尾鳍
  group.add(new THREE.Mesh(new THREE.BoxGeometry(13, 5, 17), matAir))
  const canopy = new THREE.Mesh(new THREE.SphereGeometry(5.2, 18, 12), matGlass)
  canopy.scale.set(1.05, 0.48, 1.32)
  canopy.position.y = 2.6
  group.add(canopy)
  const belly = new THREE.Mesh(new THREE.BoxGeometry(11, 1.5, 13), matDark)
  belly.position.y = -3.1
  group.add(belly)
  const fin = new THREE.Mesh(new THREE.BoxGeometry(0.9, 3.6, 3.2), matAir)
  fin.position.set(0, 3.2, -8.6)
  group.add(fin)
  // 机首传感舱 + 光电镜头
  const nose = new THREE.Mesh(new THREE.CylinderGeometry(3, 4.1, 4, 14), matAir)
  nose.rotation.x = Math.PI / 2
  nose.position.set(0, 0, 9.6)
  group.add(nose)
  const lens = new THREE.Mesh(new THREE.SphereGeometry(1.5, 12, 10),
    new THREE.MeshStandardMaterial({ color: '#67e8f9', emissive: '#67e8f9', emissiveIntensity: 1.6, roughness: 0.1 }))
  lens.position.set(0, 0, 11.6)
  group.add(lens)
  // 机臂 + 电机舱 + 双叶桨(带桨盘光晕)
  const rotors: THREE.Object3D[] = []
  const matDisc = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.15, side: THREE.DoubleSide, depthWrite: false })
  for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]] as const) {
    const arm = new THREE.Mesh(new THREE.BoxGeometry(1.6, 1.1, 12.5), matAir)
    arm.position.set(dx * 4.6, 0.9, dz * 4.6)
    arm.rotation.y = Math.atan2(dx, dz)
    group.add(arm)
    const pod = new THREE.Mesh(new THREE.CylinderGeometry(1.9, 2.1, 2.6, 12), matDark)
    pod.position.set(dx * 8.8, 1.5, dz * 8.8)
    group.add(pod)
    const ring = new THREE.Mesh(new THREE.CylinderGeometry(2.05, 2.05, 0.5, 12), matAccent)
    ring.position.set(dx * 8.8, 2.9, dz * 8.8)
    group.add(ring)
    const prop = new THREE.Group()
    prop.position.set(dx * 8.8, 3.7, dz * 8.8)
    prop.add(new THREE.Mesh(new THREE.CylinderGeometry(0.7, 0.7, 1, 8), matAccent))
    prop.add(new THREE.Mesh(new THREE.BoxGeometry(12.4, 0.22, 1.15), matDark))
    prop.add(new THREE.Mesh(new THREE.CylinderGeometry(6.3, 6.3, 0.05, 24), matDisc))
    group.add(prop)
    rotors.push(prop)
  }
  // 航行灯: 左红右绿 + 尾部白色频闪
  const navRed = new THREE.Mesh(new THREE.SphereGeometry(0.95, 8, 6),
    new THREE.MeshStandardMaterial({ color: '#ff3b30', emissive: '#ff3b30', emissiveIntensity: 2.2 }))
  navRed.position.set(-8.8, 3, 8.8)
  group.add(navRed)
  const navGreen = new THREE.Mesh(new THREE.SphereGeometry(0.95, 8, 6),
    new THREE.MeshStandardMaterial({ color: '#30ff5a', emissive: '#30ff5a', emissiveIntensity: 2.2 }))
  navGreen.position.set(8.8, 3, 8.8)
  group.add(navGreen)
  const strobe = new THREE.Mesh(new THREE.SphereGeometry(0.8, 8, 6),
    new THREE.MeshStandardMaterial({ color: '#ffffff', emissive: '#ffffff', emissiveIntensity: 2.6 }))
  strobe.position.set(0, 4.4, -9.4)
  group.add(strobe)
  // 起落橇
  for (const sx of [-5.6, 5.6]) {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.9, 15), matDark)
    rail.position.set(sx, -5.6, 0)
    group.add(rail)
    for (const sz of [-4.5, 4.5]) {
      const strut = new THREE.Mesh(new THREE.BoxGeometry(0.7, 2.8, 0.7), matDark)
      strut.position.set(sx, -4, sz)
      group.add(strut)
    }
  }
  // 子群专属挂载(造型区分: 侦察=光电吊舱 / 灭火=水剂箱+喷洒 / 支援=补给货舱)
  let spray: THREE.Mesh | null = null
  if (subgroup === 'reconnaissance') {
    const gimbal = new THREE.Group()
    gimbal.position.set(0, -4.2, 5.2)
    gimbal.add(new THREE.Mesh(new THREE.BoxGeometry(4.6, 1, 1.4), matAir))
    const ball = new THREE.Mesh(new THREE.SphereGeometry(2.5, 14, 10), matDark)
    ball.position.y = -2
    gimbal.add(ball)
    const cam = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1.6, 10),
      new THREE.MeshStandardMaterial({ color: '#67e8f9', emissive: '#67e8f9', emissiveIntensity: 1.2 }))
    cam.rotation.x = Math.PI / 2
    cam.position.set(0, -2, 1.9)
    gimbal.add(cam)
    group.add(gimbal)
  } else if (subgroup === 'suppression') {
    const tank = new THREE.Mesh(new THREE.BoxGeometry(8.6, 3.8, 11),
      new THREE.MeshStandardMaterial({ color: '#d92b2b', roughness: 0.4, metalness: 0.25 }))
    tank.position.set(0, -4.6, -0.5)
    group.add(tank)
    for (const cz of [3, -4]) {
      const strap = new THREE.Mesh(new THREE.BoxGeometry(8.8, 0.8, 1), matDark)
      strap.position.set(0, -4.6, cz)
      group.add(strap)
    }
    const nozzle = new THREE.Mesh(new THREE.ConeGeometry(1.7, 3, 10), matDark)
    nozzle.rotation.x = Math.PI
    nozzle.position.set(0, -7.4, -0.5)
    group.add(nozzle)
    spray = new THREE.Mesh(new THREE.ConeGeometry(5, 16, 12, 1, true),
      new THREE.MeshBasicMaterial({ color: '#9fd8ff', transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }))
    spray.position.set(0, -16, -0.5)
    spray.visible = false
    group.add(spray)
  } else {
    const crate = new THREE.Mesh(new THREE.BoxGeometry(8.6, 3.6, 11),
      new THREE.MeshStandardMaterial({ color: '#4d5d3a', roughness: 0.7 }))
    crate.position.set(0, -4.5, -0.5)
    group.add(crate)
    for (const cz of [2.5, -3.5]) {
      const strap = new THREE.Mesh(new THREE.BoxGeometry(8.8, 0.7, 1), matAccent)
      strap.position.set(0, -4.5, cz)
      group.add(strap)
    }
  }
  return { group, rotors, spray }
}

// ---------- 消防车建模(水罐车: 驾驶室 + 水罐 + 折叠梯 + 水炮 + 警灯 + 车轮) ----------
function buildTruck(label: string): { group: THREE.Group; meta: Record<string, any> } {
  const group = new THREE.Group()
  const matRed = new THREE.MeshStandardMaterial({ color: '#c42025', roughness: 0.35, metalness: 0.25 })
  const matWhite = new THREE.MeshStandardMaterial({ color: '#e9edf0', roughness: 0.5 })
  const matDark = new THREE.MeshStandardMaterial({ color: '#14181d', roughness: 0.6 })
  const matSteel = new THREE.MeshStandardMaterial({ color: '#aab4bd', roughness: 0.28, metalness: 0.85 })
  const matGlass = new THREE.MeshStandardMaterial({ color: '#0e161f', roughness: 0.12, metalness: 0.7 })
  // 底盘 + 保险杠
  const chassis = new THREE.Mesh(new THREE.BoxGeometry(7.6, 1.6, 24), matDark)
  chassis.position.set(0, 2.6, 1)
  group.add(chassis)
  const bumper = new THREE.Mesh(new THREE.BoxGeometry(9.8, 2.4, 1.6), matWhite)
  bumper.position.set(0, 3.2, 13.6)
  group.add(bumper)
  // 驾驶室 + 挡风玻璃 + 大灯
  const cab = new THREE.Mesh(new THREE.BoxGeometry(9.2, 6.6, 8.4), matRed)
  cab.position.set(0, 6.2, 8.8)
  group.add(cab)
  const windshield = new THREE.Mesh(new THREE.BoxGeometry(8.4, 3.2, 0.7), matGlass)
  windshield.position.set(0, 7.6, 13.05)
  group.add(windshield)
  const headMats: THREE.MeshStandardMaterial[] = []
  for (const hx of [-3.2, 3.2]) {
    const mat = new THREE.MeshStandardMaterial({ color: '#fff3cc', emissive: '#ffe9a8', emissiveIntensity: 1.6 })
    const hl = new THREE.Mesh(new THREE.BoxGeometry(1.8, 1.1, 0.6), mat)
    hl.position.set(hx, 4.6, 13.4)
    group.add(hl)
    headMats.push(mat)
  }
  // 水罐体 + 白色环带 + 不锈钢罐顶
  const tank = new THREE.Mesh(new THREE.BoxGeometry(9.6, 7.6, 14.6), matRed)
  tank.position.set(0, 6.8, -4.6)
  group.add(tank)
  const band = new THREE.Mesh(new THREE.BoxGeometry(9.8, 1.5, 14.7), matWhite)
  band.position.set(0, 5.1, -4.6)
  group.add(band)
  const tankTop = new THREE.Mesh(new THREE.BoxGeometry(8.2, 1, 13), matSteel)
  tankTop.position.set(0, 10.9, -4.6)
  group.add(tankTop)
  // 两侧水带卷盘
  for (const rx of [-5.1, 5.1]) {
    const reel = new THREE.Mesh(new THREE.CylinderGeometry(1.7, 1.7, 1.2, 14), matWhite)
    reel.rotation.z = Math.PI / 2
    reel.position.set(rx, 8.2, 2.2)
    group.add(reel)
  }
  // 罐顶折叠梯
  for (const lx of [-1.5, 1.5]) {
    const rail = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.6, 13.5), matSteel)
    rail.position.set(lx, 11.7, -4.8)
    group.add(rail)
  }
  for (let k = 0; k < 6; k++) {
    const rung = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.35, 0.5), matSteel)
    rung.position.set(0, 11.7, -10.5 + k * 2.3)
    group.add(rung)
  }
  // 水炮: 方位云台 + 俯仰炮管(炮口锚点用于挂水柱)
  const turretBase = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.8, 1.4, 12), matDark)
  turretBase.position.set(0, 11.6, 1.8)
  group.add(turretBase)
  const cannonYaw = new THREE.Group()
  cannonYaw.position.set(0, 12.4, 1.8)
  group.add(cannonYaw)
  const barrel = new THREE.Group()
  const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.65, 0.85, 6.4, 10), matSteel)
  pipe.rotation.x = Math.PI / 2
  pipe.position.z = 3.2
  barrel.add(pipe)
  const muzzle = new THREE.Mesh(new THREE.CylinderGeometry(1.05, 0.85, 1, 10), matDark)
  muzzle.rotation.x = Math.PI / 2
  muzzle.position.z = 6.4
  barrel.add(muzzle)
  const barrelTip = new THREE.Object3D()
  barrelTip.position.set(0, 0, 7)
  barrel.add(barrelTip)
  barrel.rotation.x = -0.55
  cannonYaw.add(barrel)
  // 车顶警灯排(红蓝爆闪)
  const lightBase = new THREE.Mesh(new THREE.BoxGeometry(7.2, 0.9, 2.4), matDark)
  lightBase.position.set(0, 9.95, 8.8)
  group.add(lightBase)
  const lampR = new THREE.MeshStandardMaterial({ color: '#3a0508', emissive: '#ff2d2d', emissiveIntensity: 0.15 })
  const lampB = new THREE.MeshStandardMaterial({ color: '#050a2a', emissive: '#2d6bff', emissiveIntensity: 2.6 })
  const lampRm = new THREE.Mesh(new THREE.BoxGeometry(3.2, 1.3, 2), lampR)
  lampRm.position.set(-1.9, 11, 8.8)
  group.add(lampRm)
  const lampBm = new THREE.Mesh(new THREE.BoxGeometry(3.2, 1.3, 2), lampB)
  lampBm.position.set(1.9, 11, 8.8)
  group.add(lampBm)
  // 排烟管
  const stack = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 3, 8), matSteel)
  stack.position.set(-3.2, 10.9, 3.6)
  group.add(stack)
  // 车轮 ×6(双后桥)
  const wheels: THREE.Mesh[] = []
  const wheelGeo = new THREE.CylinderGeometry(2.35, 2.35, 1.8, 16)
  wheelGeo.rotateZ(Math.PI / 2)
  const hubGeo = new THREE.CylinderGeometry(1.05, 1.05, 1.9, 10)
  hubGeo.rotateZ(Math.PI / 2)
  for (const wz of [8.6, -4.4, -9.9]) {
    for (const wx of [-4.7, 4.7]) {
      const wheel = new THREE.Mesh(wheelGeo, matDark)
      wheel.position.set(wx, 2.35, wz)
      wheel.add(new THREE.Mesh(hubGeo, matSteel))
      group.add(wheel)
      wheels.push(wheel)
    }
  }
  const tag = textSprite(label, '#ff8a7a', 0.5)
  tag.position.set(0, 24, 0)
  group.add(tag)
  const meta: Record<string, any> = { truck: true, wheels, lampR, lampB, headMats, cannonYaw, barrel, barrelTip, cannonTarget: 0 }
  return { group, meta }
}

// ---------- 水柱(抛物线水舌 + 落水涟漪) ----------
function buildWaterJet(from: THREE.Vector3, to: THREE.Vector3): THREE.Group {
  const jet = new THREE.Group()
  const mid = from.clone().lerp(to, 0.45)
  mid.y += from.distanceTo(to) * 0.2 + 10
  const curve = new THREE.QuadraticBezierCurve3(from, mid, to)
  jet.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 0.9, 6),
    new THREE.MeshBasicMaterial({ color: '#7cc7ff', transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false })))
  jet.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 2, 6),
    new THREE.MeshBasicMaterial({ color: '#3f9fe0', transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false })))
  const splash = new THREE.Mesh(new THREE.CircleGeometry(7, 24),
    new THREE.MeshBasicMaterial({ color: '#a5dcff', transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }))
  splash.rotation.x = -Math.PI / 2
  splash.position.copy(to)
  jet.add(splash)
  const ripple = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 32),
    new THREE.MeshBasicMaterial({ color: '#a5dcff', transparent: true, opacity: 0.4, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }))
  ripple.rotation.x = -Math.PI / 2
  ripple.position.copy(to).setY(to.y + 2)
  ripple.userData = { ripple: true, seed: Math.random() }
  jet.add(ripple)
  jet.userData = { jet: true }
  return jet
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
  const truckTaskRef = useRef<string>('')

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
            for (const rotor of meta.rotors) rotor.rotation.y += 0.85
          }
          if (meta.drone && meta.target) {
            meta.cur.x += (meta.target.x - meta.cur.x) * 0.06
            meta.cur.y += (meta.target.y - meta.cur.y) * 0.06
            meta.cur.z += (meta.target.z - meta.cur.z) * 0.06
            // 悬停微沉浮, 避免滞空感
            child.position.set(meta.cur.x, meta.cur.y + Math.sin(ts / 850 + (meta.seed || 0) * 9) * 2.2, meta.cur.z)
            if (meta.beacon) meta.beacon.position.set(meta.cur.x, meta.groundY + 4, meta.cur.z)
            if (meta.line) {
              const lg = (meta.line as THREE.Line).geometry.attributes.position as THREE.BufferAttribute
              lg.setXYZ(0, meta.cur.x, child.position.y, meta.cur.z)
              lg.setXYZ(1, meta.cur.x, meta.groundY, meta.cur.z)
              lg.needsUpdate = true
            }
          }
          if (meta.truck) {
            meta.dist = Math.min(meta.dist + meta.speed, meta.route.total)
            const at = routeAt(meta.route as TruckRoute, meta.dist)
            child.position.set(at.x - at.dz * meta.side, at.y, at.z + at.dx * meta.side)
            child.rotation.y = Math.atan2(at.dx, at.dz)
            const moving = meta.dist < meta.route.total - 0.5
            if (moving) for (const wheel of meta.wheels) wheel.rotation.x += 0.28
            const flash = Math.sin(ts / 125)
            meta.lampR.emissiveIntensity = flash > 0 ? 2.6 : 0.15
            meta.lampB.emissiveIntensity = flash > 0 ? 0.15 : 2.6
            for (const hm of meta.headMats) hm.emissiveIntensity = 1.4 + 0.35 * Math.sin(ts / 95)
            // 水炮缓动转向目标方位
            meta.cannonYaw.rotation.y += (meta.cannonTarget - meta.cannonYaw.rotation.y) * 0.05
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
    // 疏散层: 贴地亮线路线 + 人群标记(3 个小球, 沿路线移动)
    const evac = (snapshot as any)?.support_plan?.evacuation
    if (evac && evac.path && evac.path.length > 1) {
      const routePts: THREE.Vector3[] = evac.path.map((pt: any) =>
        new THREE.Vector3(...((): [number, number, number] => {
          const [wx, wz] = toWorld(terrain, pt.x, pt.y)
          return [wx, elev(terrain, pt.x, pt.y) * EX + 8, wz]
        })()))
      let routeLine = core.dynamic.getObjectByName('evac-route') as THREE.Line | undefined
      const routeGeo = new THREE.BufferGeometry().setFromPoints(routePts)
      if (!routeLine) {
        routeLine = new THREE.Line(routeGeo, new THREE.LineDashedMaterial({
          color: '#5eead4', transparent: true, opacity: 0.95, dashSize: 26, gapSize: 14 }))
        routeLine.name = 'evac-route'
        core.dynamic.add(routeLine)
      } else {
        routeLine.geometry.dispose()
        routeLine.geometry = routeGeo
      }
      routeLine.computeLineDistances()
      let crowd = core.dynamic.getObjectByName('evac-crowd') as THREE.Group | undefined
      if (!crowd) {
        crowd = new THREE.Group()
        crowd.name = 'evac-crowd'
        const personMat = new THREE.MeshStandardMaterial({ color: '#ffb066', emissive: '#ff8c3a', emissiveIntensity: 0.7 })
        for (let k = 0; k < 3; k++) {
          const person = new THREE.Mesh(new THREE.CapsuleGeometry(4, 9, 3, 8), personMat)
          person.position.x = (k - 1) * 10
          crowd.add(person)
        }
        core.dynamic.add(crowd)
      }
      crowd.visible = !evac.evacuated
      const idx = Math.min(Math.floor(evac.progress_cells || 0), evac.path.length - 1)
      const pt = evac.path[idx]
      const [cwx, cwz] = toWorld(terrain, pt.x, pt.y)
      crowd.position.set(cwx, elev(terrain, pt.x, pt.y) * EX + 12, cwz)
    } else {
      const oldRoute = core.dynamic.getObjectByName('evac-route')
      if (oldRoute) core.dynamic.remove(oldRoute)
      const oldCrowd = core.dynamic.getObjectByName('evac-crowd')
      if (oldCrowd) core.dynamic.remove(oldCrowd)
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
        const built = buildDrone(meta.color, uav.subgroup)
        group = built.group
        group.name = name
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
        group.userData = { drone: true, cur: { x: wx, y: alt, z: wz }, groundY, rotors: built.rotors, spray: built.spray, beacon, line, seed: Math.random() }
        group.position.set(wx, alt, wz)
        core.dynamic.add(group)
      }
      const data = group.userData as Record<string, any>
      data.target = { x: wx, y: alt, z: wz }
      data.groundY = groundY
      if (data.spray) data.spray.visible = uav.subgroup === 'suppression' && uav.status === 'working'
    }
    for (const child of [...core.dynamic.children]) {
      const data = child.userData as Record<string, any>
      if (data?.drone && !seen.has(child.name.replace('drone-', ''))) {
        if (data.beacon) core.dynamic.remove(data.beacon)
        if (data.line) core.dynamic.remove(data.line)
        core.dynamic.remove(child)
      }
    }

    // ---------- 地面消防车: 审批通过进入执行后, 由基地沿巡护道前出 FSP-1 ----------
    const removeJets = () => {
      for (const child of [...core.dynamic.children]) {
        if ((child.userData as Record<string, any>)?.jet) {
          child.traverse(o => {
            const mat = (o as THREE.Mesh).material as THREE.Material | undefined
            mat?.dispose?.()
          })
          core.dynamic.remove(child)
        }
      }
    }
    const taskId = snapshot?.task_id ?? ''
    if (truckTaskRef.current !== taskId) {
      truckTaskRef.current = taskId
      for (const child of [...core.dynamic.children]) {
        if ((child.userData as Record<string, any>)?.truck) core.dynamic.remove(child)
      }
    }
    // 执行中重规划会回到再审批: 只要已出动过(rounds>0), 消防车保持在前线不回收
    const deployed = (snapshot?.rounds?.length ?? 0) > 0
    const trucksActive = !!snapshot && (truckPhases.includes(snapshot.phase) ||
      (snapshot.phase === 'awaiting_approval' && deployed))
    if (!trucksActive) {
      for (const child of [...core.dynamic.children]) {
        if ((child.userData as Record<string, any>)?.truck) core.dynamic.remove(child)
      }
    }
    const road = scene?.roads?.[0]
    if (trucksActive && road && road.points.length >= 2 && scene) {
      // 路线: 巡护道起点(基地) → FSP 所在折点, 终点回退 34m 避开补给台座
      let fspIdx = road.points.findIndex(p => Math.hypot(p[0] - scene.forward_supply_point.x, p[1] - scene.forward_supply_point.y) < 40)
      if (fspIdx < 1) fspIdx = road.points.length - 1
      const route = buildTruckRoute(terrain, road.points.slice(0, fspIdx + 1))
      const stopRoute: TruckRoute = { pts: route.pts, total: Math.max(0, route.total - 34) }
      for (const cfg of TRUCK_CFG) {
        let truck = core.dynamic.getObjectByName(cfg.id) as THREE.Group | undefined
        if (!truck) {
          const built = buildTruck(cfg.label)
          truck = built.group
          truck.name = cfg.id
          built.meta.route = stopRoute
          built.meta.dist = cfg.id === 'truck-01' ? 0 : -340
          built.meta.speed = cfg.speed
          built.meta.side = cfg.side
          truck.userData = built.meta
          const at = routeAt(stopRoute, 0)
          truck.position.set(at.x - at.dz * cfg.side, at.y, at.z + at.dx * cfg.side)
          truck.rotation.y = Math.atan2(at.dx, at.dz)
          core.dynamic.add(truck)
        }
      }
      // 水炮瞄准与水柱: 消防车到位且火仍在烧时, 向火场中心喷射
      removeJets()
      const burning = (snapshot?.fire?.cells ?? []).filter(c => c.flp > 0.01)
      const firing = snapshot?.phase === 'executing' || snapshot?.phase === 'replanning' ||
        (snapshot?.phase === 'awaiting_approval' && deployed)
      if (burning.length && firing) {
        const fx = burning.reduce((s, c) => s + c.x, 0) / burning.length
        const fy = burning.reduce((s, c) => s + c.y, 0) / burning.length
        const [fwx, fwz] = toWorld(terrain, fx, fy)
        const target = new THREE.Vector3(fwx, elev(terrain, fx, fy) * EX + 6, fwz)
        for (const cfg of TRUCK_CFG) {
          const truck = core.dynamic.getObjectByName(cfg.id) as THREE.Group | undefined
          const meta = truck?.userData as Record<string, any> | undefined
          if (!truck || !meta || meta.dist < meta.route.total - 1) continue
          truck.updateMatrixWorld(true)
          const tip = meta.barrelTip.getWorldPosition(new THREE.Vector3())
          if (tip.distanceTo(target) > 900) continue
          meta.cannonTarget = Math.atan2(target.x - truck.position.x, target.z - truck.position.z) - truck.rotation.y
          meta.barrel.rotation.x = -(0.3 + Math.min(0.5, tip.distanceTo(target) / 1500))
          core.dynamic.add(buildWaterJet(tip, target))
        }
      }
    } else {
      removeJets()
    }
  }, [snapshot?.fire, snapshot?.fleet, snapshot?.phase, snapshot?.task_id, terrain, scene])

  return <div className="terrain3d" ref={mountRef} />
}
