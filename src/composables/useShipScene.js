/**
 * 舰船三维场景编排
 * ─────────────────────────────────────────────────────────────
 * 职责：渲染器 / 相机 / 控制器 / 环境 / 船模与舱室模型的加载与切换 /
 *       射线拾取 / 舱室标签投影 / 火焰锚点 / 资源释放。
 *
 * 所有几何与交互都基于 src/config/shipLayout.js 中定义的真实米制坐标，
 * 不再依赖任何硬编码的缩放系数与猜测坐标。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { createEnvironment } from '@/three/environment'
import { loadGLTF, disposeObject } from '@/three/loaders'
import { COMPARTMENTS, SHIP, getCompartmentById } from '@/config/shipLayout'
import { FlipbookFireSystem } from '@/components/FlipbookFireSystem'

const SHIP_URL = '/models/ship.glb'
/** 由 tools/optimize_fire_sprite.py 从 4K 源图转码而来：8.92MB -> 279KB */
const SPRITE_URL = '/fire-sprite.jpg'

/**
 * 整船视角相机预设
 * 115m 长的船配 42° FOV，要让整舰入画需要约 165m 观察距离；
 * 取艉右四分之一、仰角约 20°，兼顾飞行甲板、上层建筑与艏部舰炮。
 */
const SHIP_VIEW = {
  position: new THREE.Vector3(-120, 58, 105),
  target: new THREE.Vector3(0, 5, 0)
}

const easeInOutCubic = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export function useShipScene(options = {}) {
  // 注意命名：不要解构成 onSelectCompartment，它会与下面的同名局部函数遮蔽，
  // 导致 canvas 点击自我递归、永远传不到父组件
  const { onSelectCompartment: emitSelect, onHoverCompartment } = options

  /* ---------------- 响应式状态 ---------------- */
  const ready = ref(false)
  const loading = ref(true)
  const progress = ref(0)
  const loadError = ref(null)
  const hoveredId = ref(null)
  const activeCompartment = ref(null)
  const labels = ref([])

  /* ---------------- three 对象（不进响应式） ---------------- */
  const scene = shallowRef(null)
  const camera = shallowRef(null)
  const renderer = shallowRef(null)
  const controls = shallowRef(null)

  let env = null
  let shipRoot = null
  let compartmentRoot = null
  let pickTargets = []
  let rafId = null
  let resizeObserver = null
  let disposed = false
  const clock = new THREE.Clock()
  const fires = new Map()
  /** compartmentId -> boolean，进入舱室时据此决定火焰是否可见 */
  const fireActive = new Map()
  let fireIntensity = 0.35

  /* ---------------- 相机补间 ---------------- */
  const tween = {
    active: false,
    t: 0,
    dur: 0.9,
    fromPos: new THREE.Vector3(),
    toPos: new THREE.Vector3(),
    fromTarget: new THREE.Vector3(),
    toTarget: new THREE.Vector3()
  }

  function flyCamera(position, target, duration = 0.9) {
    if (!camera.value || !controls.value) return
    tween.fromPos.copy(camera.value.position)
    tween.toPos.copy(position)
    tween.fromTarget.copy(controls.value.target)
    tween.toTarget.copy(target)
    tween.t = 0
    tween.dur = duration
    tween.active = true
  }

  /* ---------------- 初始化 ---------------- */
  function initRenderer() {
    const canvas = options.canvasRef?.value
    if (!canvas) throw new Error('canvas 未就绪')

    const r = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      powerPreference: 'high-performance',
      stencil: false
    })
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    r.outputColorSpace = THREE.SRGBColorSpace
    r.toneMapping = THREE.ACESFilmicToneMapping
    r.toneMappingExposure = 0.92
    r.shadowMap.enabled = true
    r.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.value = r

    const s = new THREE.Scene()
    scene.value = s

    const container = options.containerRef?.value
    const w = container?.clientWidth || window.innerWidth
    const h = container?.clientHeight || window.innerHeight

    const cam = new THREE.PerspectiveCamera(42, w / h, 0.5, 20000)
    cam.position.copy(SHIP_VIEW.position)
    cam.lookAt(SHIP_VIEW.target)
    camera.value = cam

    const ctl = new OrbitControls(cam, r.domElement)
    ctl.enableDamping = true
    ctl.dampingFactor = 0.06
    ctl.target.copy(SHIP_VIEW.target)
    ctl.minDistance = 8
    ctl.maxDistance = 320
    ctl.maxPolarAngle = Math.PI * 0.495   // 不允许钻到海面以下
    ctl.screenSpacePanning = false
    controls.value = ctl
  }

  function initEvents() {
    const dom = renderer.value.domElement
    dom.addEventListener('pointermove', onPointerMove)
    dom.addEventListener('pointerleave', onPointerLeave)
    dom.addEventListener('pointerdown', onPointerDown)
    dom.addEventListener('pointerup', onPointerUp)
    dom.addEventListener('pointercancel', onPointerUp)
    dom.addEventListener('click', onCanvasClick)

    const container = options.containerRef?.value
    if (container && 'ResizeObserver' in window) {
      resizeObserver = new ResizeObserver(resize)
      resizeObserver.observe(container)
    } else {
      window.addEventListener('resize', resize)
    }
  }

  function resize() {
    const container = options.containerRef?.value
    if (!container || !renderer.value || !camera.value) return
    const w = container.clientWidth
    const h = container.clientHeight
    if (!w || !h) return
    camera.value.aspect = w / h
    camera.value.updateProjectionMatrix()
    renderer.value.setSize(w, h, false)
  }

  /* ---------------- 模型加载 ---------------- */
  async function loadShip() {
    const gltf = await loadGLTF(SHIP_URL, p => {
      progress.value = Math.min(0.97, p * 0.97)
    })

    shipRoot = gltf.scene
    shipRoot.name = 'ShipRoot'

    shipRoot.traverse(child => {
      if (child.isMesh) {
        child.castShadow = true
        child.receiveShadow = true
        const mats = Array.isArray(child.material) ? child.material : [child.material]
        for (const m of mats) {
          if (!m) continue
          m.envMapIntensity = 1.0
          // 拾取体保持完全透明，不参与阴影与渲染
          if (m.name === 'CompartmentPick') {
            child.castShadow = false
            child.receiveShadow = false
            child.visible = true
          }
        }
      }
      if (/^COMPARTMENT_\d+$/.test(child.name)) {
        child.userData.compartmentId = Number(child.name.split('_')[1])
        pickTargets.push(child)
      }
    })

    // 船体浮动基线：让吃水线略微下沉，水线附近更自然
    shipRoot.position.y = -0.35
    scene.value.add(shipRoot)

    progress.value = 1
  }

  /* ---------------- 火焰 ---------------- */
  async function ensureFire(compartmentId) {
    if (fires.has(compartmentId)) return fires.get(compartmentId)
    const c = getCompartmentById(compartmentId)
    if (!c) return null
    const origin = new THREE.Vector3(
      c.fireOrigin?.x ?? c.anchor.x,
      c.fireOrigin?.y ?? c.anchor.y,
      c.fireOrigin?.z ?? c.anchor.z
    )
    const fire = new FlipbookFireSystem(origin, {
      spriteSheet: SPRITE_URL,
      width: c.fireSize?.w ?? 6,
      height: c.fireSize?.h ?? 4
    })
    fire.applyProfile({
      width: c.fireSize?.w ?? 6,
      height: c.fireSize?.h ?? 4,
      origin
    })
    fire.setVisibility(false)
    scene.value.add(fire)
    fires.set(compartmentId, fire)
    return fire
  }

  function setFireState(compartmentId, active, intensity = null) {
    fireActive.set(compartmentId, !!active)
    if (intensity != null) fireIntensity = intensity
    ensureFire(compartmentId).then(fire => {
      if (!fire) return
      fire.setVisibility(!!active)
      fire.setIntensity(active ? Math.max(0.25, fireIntensity) : 0)
    })
  }

  function setFireIntensity(intensity) {
    fireIntensity = THREE.MathUtils.clamp(intensity, 0, 1)
    fires.forEach(fire => {
      if (fire.visible) fire.setIntensity(Math.max(0.12, fireIntensity))
    })
  }

  /* ---------------- 射线拾取 ---------------- */
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()

  function pickAt(event) {
    const el = renderer.value?.domElement
    if (!el || !pickTargets.length || activeCompartment.value) return null
    const rect = el.getBoundingClientRect()
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, camera.value)
    const hits = raycaster.intersectObjects(pickTargets, false)
    return hits.length ? hits[0].object.userData.compartmentId : null
  }

  let lastHover = null
  function onPointerMove(event) {
    const id = pickAt(event)
    if (id !== lastHover) {
      lastHover = id
      hoveredId.value = id
      renderer.value.domElement.style.cursor = id ? 'pointer' : 'grab'
      onHoverCompartment?.(id)
    }
  }

  function onPointerLeave() {
    lastHover = null
    hoveredId.value = null
    onHoverCompartment?.(null)
  }

  /*
   * 拖动 vs 单击判定
   *
   * 之前 pointerdown 监听是在 onMounted 里注册的，早于 initRenderer()，
   * 那时 renderer.value 还是 null，监听器压根没挂上，downPos 恒为 null，
   * 于是"拖动 OrbitControls 后松手"也会被判成单击而误入舱室。
   *
   * 现在监听随 initEvents() 一起注册（在渲染器就绪之后），
   * 并且用两个条件判定，任意一个成立就认为是拖动：
   *   1. 指针位移超过阈值
   *   2. 相机在这段时间里被移动过（OrbitControls 确实旋转了）
   * 只看位移不够：某些设备上拖动后指针可能回弹到起点。
   */
  const DRAG_PX = 6
  let pointerDown = null

  function onPointerDown(event) {
    if (event.button !== 0) return
    pointerDown = {
      x: event.clientX,
      y: event.clientY,
      t: performance.now(),
      cam: camera.value ? camera.value.position.clone() : null
    }
  }

  function wasDrag(event) {
    if (!pointerDown) return false
    const moved = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y)
    if (moved > DRAG_PX) return true
    if (pointerDown.cam && camera.value) {
      if (camera.value.position.distanceTo(pointerDown.cam) > 0.01) return true
    }
    return false
  }

  function onPointerUp() {
    // 延迟清空：click 在 pointerup 之后触发
    setTimeout(() => { pointerDown = null }, 0)
  }

  function onSelectCompartment(id) {
    emitSelect?.(id, true)   // 第二个参数标记「来自三维视图」
  }

  function onCanvasClick(event) {
    // 拖动旋转视角后松手不能当成点击，否则会误入舱室
    if (wasDrag(event)) return
    const id = pickAt(event)
    if (id) onSelectCompartment(id)
  }

  /* ---------------- 舱室标签投影 ---------------- */
  let labelFrame = 0
  function updateLabels() {
    if (activeCompartment.value) {
      if (labels.value.length) labels.value = []
      return
    }
    if (++labelFrame % 3 !== 0) return // 限流到 ~20fps

    const cam = camera.value
    const el = renderer.value?.domElement
    if (!cam || !el) return
    const w = el.clientWidth
    const h = el.clientHeight
    const out = []
    const v = new THREE.Vector3()

    for (const c of COMPARTMENTS) {
      v.set(c.anchor.x, c.anchor.y + 4.2, c.anchor.z)
      v.y += Math.sin(clock.elapsedTime * 0.5) * 0.2
      v.project(cam)
      const behind = v.z > 1
      const x = (v.x * 0.5 + 0.5) * w
      const y = (-v.y * 0.5 + 0.5) * h
      const inView = !behind && x > -60 && x < w + 60 && y > -40 && y < h + 40
      out.push({
        id: c.id,
        name: c.name,
        code: c.code,
        x: Math.round(x),
        y: Math.round(y),
        visible: inView,
        depth: behind ? 1 : THREE.MathUtils.clamp(v.z, 0, 1)
      })
    }
    labels.value = out
  }

  /* ---------------- 环境显隐（舱室视图下必须切换） ----------------
   *
   * 注意：Sky 是 add 到 scene 里的网格对象，不是 scene.background。
   * 只改 scene.background 撤不掉它，天空会继续铺满整个视口 ——
   * 结果就是舱室视图背景一片亮白，深色半透明控件全被冲掉。
   * 必须同时隐藏 sky 与 sea，并把 background 显式设为暗色。
   */
  function setSeaVisible(v) {
    if (!env) return
    const inside = !v
    env.sea.visible = v
    env.sky.visible = v
    env.setInteriorMode(inside)
    if (v) {
      scene.value.background = null
      scene.value.fog = env.fog
    } else {
      scene.value.fog = null
      scene.value.background = new THREE.Color(0x080f18)
    }
  }

  /* ---------------- 视图切换 ---------------- */
  async function enterCompartment(id) {
    const c = getCompartmentById(id)
    if (!c || !scene.value) return
    activeCompartment.value = id
    hoveredId.value = null

    if (compartmentRoot) {
      scene.value.remove(compartmentRoot)
      disposeObject(compartmentRoot)
      compartmentRoot = null
    }

    if (shipRoot) shipRoot.visible = false

    /*
     * 舱室在船体水线以下，外部海面会把它整个淹没并透过舱壁看见海水。
     * 进入舱室视图必须同时撤掉海面与雾，否则内部是"泡在水里"的观感。
     */
    setSeaVisible(false)

    if (c.model) {
      try {
        const gltf = await loadGLTF(c.model)
        compartmentRoot = gltf.scene
        compartmentRoot.name = `Compartment_${id}`
        compartmentRoot.traverse(m => {
          if (m.isMesh) {
            m.castShadow = true
            m.receiveShadow = true
          }
        })
        scene.value.add(compartmentRoot)
      } catch (e) {
        console.error('[useShipScene] 舱室模型加载失败:', c.model, e)
      }
    }

    /*
     * 取景按「模型实际包围盒」而不是「声明的舱室包围盒」。
     * 舱室模型由 FDS 导出后往往只占包围盒的一小块，按声明范围取景
     * 会让模型在画面里缩成一小团。这里取两者中更贴合的一个。
     */
    const declaredCenter = new THREE.Vector3(
      (c.bounds.min.x + c.bounds.max.x) / 2,
      (c.bounds.min.y + c.bounds.max.y) / 2,
      (c.bounds.min.z + c.bounds.max.z) / 2
    )
    const declaredSize = new THREE.Vector3(
      c.bounds.max.x - c.bounds.min.x,
      c.bounds.max.y - c.bounds.min.y,
      c.bounds.max.z - c.bounds.min.z
    )

    let target = declaredCenter
    let fitSize = declaredSize
    if (compartmentRoot) {
      const box = new THREE.Box3().setFromObject(compartmentRoot)
      if (box.isEmpty()) {
        disposeObject(compartmentRoot)
        scene.value.remove(compartmentRoot)
        compartmentRoot = null
      } else {
        box.getCenter(target = new THREE.Vector3())
        box.getSize(fitSize = new THREE.Vector3())
      }
    }

    // config 中的 offset 只提供观察方向，距离按模型尺寸自动解算
    const dir = new THREE.Vector3(
      c.camera.offset.x,
      c.camera.offset.y,
      c.camera.offset.z
    ).normalize()
    const radius = Math.max(fitSize.x, fitSize.y, fitSize.z, 1) * 0.5
    const vFov = THREE.MathUtils.degToRad(camera.value.fov)
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.value.aspect)
    const dist = radius / Math.tan(Math.min(vFov, hFov) / 2) * 1.15
    const pos = target.clone().add(dir.multiplyScalar(dist))
    flyCamera(pos, target, 1.0)

    // 火焰跟随到舱室内的起火点；仅在该舱室确实处于火灾状态时才显示
    const fire = await ensureFire(id)
    if (fire) {
      const active = fireActive.get(id) === true
      fire.setVisibility(active)
      const o = c.fireOrigin ?? c.anchor
      fire.moveTo(new THREE.Vector3(o.x, o.y, o.z))
      fire.setIntensity(active ? Math.max(0.3, fireIntensity) : 0)
    }
  }

  function exitCompartment() {
    if (compartmentRoot) {
      scene.value?.remove(compartmentRoot)
      disposeObject(compartmentRoot)
      compartmentRoot = null
    }
    if (shipRoot) shipRoot.visible = true
    setSeaVisible(true)
    fires.forEach(fire => {
      fire.setVisibility(false)
      fire.resetPosition()
    })
    activeCompartment.value = null
    flyCamera(SHIP_VIEW.position, SHIP_VIEW.target, 0.95)
  }

  /** 整船视图下把镜头对准某个舱室（不进入舱室内部） */
  function focusCompartment(id) {
    if (activeCompartment.value) return
    const c = getCompartmentById(id)
    if (!c) return
    const target = new THREE.Vector3(c.anchor.x, c.anchor.y + 2, c.anchor.z)
    const offset = c.camera.offset.clone().multiplyScalar(0.9)
    flyCamera(target.clone().add(offset), target, 0.8)
  }

  function resetView() {
    if (activeCompartment.value) exitCompartment()
    else flyCamera(SHIP_VIEW.position, SHIP_VIEW.target, 0.8)
  }

  /* ---------------- 动画循环 ---------------- */
  function animate() {
    if (disposed) return
    rafId = requestAnimationFrame(animate)
    const delta = Math.min(clock.getDelta(), 0.1)
    const elapsed = clock.elapsedTime

    if (tween.active && controls.value && camera.value) {
      tween.t = Math.min(1, tween.t + delta / tween.dur)
      const e = easeInOutCubic(tween.t)
      camera.value.position.lerpVectors(tween.fromPos, tween.toPos, e)
      controls.value.target.lerpVectors(tween.fromTarget, tween.toTarget, e)
      if (tween.t >= 1) tween.active = false
    }

    controls.value?.update()

    // 涌浪：纵摇 + 横摇 + 垂荡
    if (shipRoot && shipRoot.visible) {
      shipRoot.position.y = -0.35 + Math.sin(elapsed * 0.62) * 0.16 + Math.sin(elapsed * 0.31) * 0.08
      shipRoot.rotation.z = Math.sin(elapsed * 0.44) * 0.0075
      shipRoot.rotation.x = Math.sin(elapsed * 0.53 + 1.1) * 0.004
    }

    env?.update(elapsed)
    fires.forEach(fire => fire.update(delta, elapsed))

    updateLabels()
    renderer.value?.render(scene.value, camera.value)
  }

  /* ---------------- 生命周期 ---------------- */
  async function start() {
    try {
      initRenderer()
      env = createEnvironment(scene.value, renderer.value)
      initEvents()
      resize()
      animate()
      await loadShip()
      await Promise.all(COMPARTMENTS.map(c => ensureFire(c.id)))
      ready.value = true
      loading.value = false
    } catch (e) {
      console.error('[useShipScene] 初始化失败:', e)
      loadError.value = e?.message || String(e)
      loading.value = false
    }
  }

  function dispose() {
    disposed = true
    if (rafId) cancelAnimationFrame(rafId)
    rafId = null

    const dom = renderer.value?.domElement
    dom?.removeEventListener('pointermove', onPointerMove)
    dom?.removeEventListener('pointerleave', onPointerLeave)
    dom?.removeEventListener('pointerdown', onPointerDown)
    dom?.removeEventListener('pointerup', onPointerUp)
    dom?.removeEventListener('pointercancel', onPointerUp)
    dom?.removeEventListener('click', onCanvasClick)
    window.removeEventListener('resize', resize)
    resizeObserver?.disconnect()

    fires.forEach(f => f.dispose())
    fires.clear()

    if (compartmentRoot) disposeObject(compartmentRoot)
    if (shipRoot) disposeObject(shipRoot)
    env?.dispose()
    controls.value?.dispose()
    renderer.value?.dispose()
    renderer.value?.forceContextLoss?.()

    scene.value?.clear()
    pickTargets = []
  }

  onMounted(() => {
    // 注意：不要在这里注册任何依赖 renderer 的监听，
    // 渲染器是在 start() -> initRenderer() 里创建的。
    start()
  })
  onBeforeUnmount(dispose)

  return {
    // 状态
    ready, loading, progress, loadError,
    hoveredId, activeCompartment, labels,
    // 引用
    scene, camera, renderer, controls,
    // 行为
    start, dispose, resize,
    enterCompartment, exitCompartment, focusCompartment, resetView,
    setFireState, setFireIntensity,
    shipMeta: SHIP
  }
}
