import * as THREE from 'three'

/**
 * Flipbook 真实火焰系统
 * ─────────────────────────────────────────────────────────────
 * 基于真实火焰拍摄的 Sprite Sheet（6x6 帧）播放火焰动画。
 *
 * 场景为米制（1 单位 = 1 米），因此尺寸与光源强度均按物理量给出：
 *  - width/height 为火焰面片的实际尺寸（米）
 *  - 点光源使用 candela 强度，配合 three.js 物理光照默认值
 *  - 3 片交叉 Billboard 近似体积感，AdditiveBlending 叠加
 */
export class FlipbookFireSystem extends THREE.Group {
  /**
   * @param {THREE.Vector3} position 世界坐标锚点（火焰底部所在）
   * @param {object} [options]
   * @param {number} [options.width=6]     火焰宽度（米）
   * @param {number} [options.height=8]    火焰高度（米）
   * @param {string} [options.spriteSheet] 精灵图路径
   * @param {number} [options.fps=24]
   * @param {number} [options.lightIntensity=900] 点光源强度 (cd)
   */
  constructor(position, options = {}) {
    super()
    this.position.copy(position)
    this.anchor = position.clone()

    this.width = options.width ?? 6
    this.height = options.height ?? 8
    this.spriteSheetPath = options.spriteSheet || '/fire-sprite.jpg'
    this.columns = options.columns || 6
    this.rows = options.rows || 6
    this.fps = options.fps || 24
    this.lightIntensity = options.lightIntensity ?? 12

    this.totalFrames = this.columns * this.rows
    this.intensity = 1.0
    this._visible = false
    this.clock = new THREE.Clock()
    this.currentFrame = 0
    this.frameAccumulator = 0

    this.billboards = []
    this.texture = null
    this.pointLight = null
    this._textureLoader = new THREE.TextureLoader()
    this._ready = this._init()
  }

  async _init() {
    this.texture = await this._loadTexture()
    this._createBillboards()
    this._createSmoke()
    this._createPointLight()
    this.setVisibility(this._visible)
    return this
  }

  _loadTexture() {
    return new Promise(resolve => {
      this._textureLoader.load(
        this.spriteSheetPath,
        texture => {
          const img = texture.image
          if (img && (img.width > 1024 || img.height > 1024)) {
            const maxDim = 1024
            const scale = maxDim / Math.max(img.width, img.height)
            const canvas = document.createElement('canvas')
            canvas.width = Math.round(img.width * scale)
            canvas.height = Math.round(img.height * scale)
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
            texture.image = canvas
          }
          texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping
          texture.magFilter = THREE.LinearFilter
          texture.minFilter = THREE.LinearFilter
          texture.colorSpace = THREE.SRGBColorSpace
          texture.generateMipmaps = false
          texture.needsUpdate = true
          resolve(texture)
        },
        undefined,
        () => {
          console.error('[FlipbookFireSystem] 精灵图加载失败:', this.spriteSheetPath)
          resolve(null)
        }
      )
    })
  }

  _createBillboards() {
    if (!this.texture) return
    /*
     * 舱内火灾不是一簇居中的篝火：火是沿着可燃物表面**贴地铺开**的，
     * 随发展逐渐向上烧，并在舱顶形成烟层。
     * 因此这里用 3 个不同宽高比、不同相位的面片互相穿插，
     * 横向宽、纵向矮，整体读起来是一片火场而不是一根火苗。
     */
    const configs = [
      { angle: 0, wScale: 1.0, hScale: 0.82, opacity: 1.0, y: 0.46, x: 0.0 },
      { angle: Math.PI / 2, wScale: 0.88, hScale: 0.70, opacity: 0.95, y: 0.38, x: 0.18 },
      { angle: Math.PI / 4, wScale: 0.74, hScale: 0.95, opacity: 0.8, y: 0.62, x: -0.14 }
    ]

    configs.forEach((cfg, index) => {
      const geometry = new THREE.PlaneGeometry(this.width, this.height)
      const material = new THREE.MeshBasicMaterial({
        map: this.texture.clone(),
        transparent: true,
        opacity: cfg.opacity,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false
      })
      material.map.needsUpdate = true
      material.map.repeat.set(1 / this.columns, 1 / this.rows)
      material.map.offset.set(0, 1 - 1 / this.rows)

      const mesh = new THREE.Mesh(geometry, material)
      mesh.rotation.y = cfg.angle
      mesh.position.set(this.width * cfg.x, this.height * cfg.y, 0)
      mesh.renderOrder = 10
      mesh.userData = {
        baseW: cfg.wScale, baseH: cfg.hScale, baseOpacity: cfg.opacity,
        phase: index * 2.1
      }
      this.billboards.push(mesh)
      this.add(mesh)
    })
  }

  /** 烟层：深色烟片在舱顶积聚，随火灾强度变浓 */
  _createSmoke() {
    this.smoke = []
    const COUNT = 5
    for (let i = 0; i < COUNT; i++) {
      const geo = new THREE.PlaneGeometry(this.width * 1.5, this.height * 1.1)
      const mtl = new THREE.MeshBasicMaterial({
        color: 0x2a2b30,
        transparent: true,
        opacity: 0,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false
      })
      const mesh = new THREE.Mesh(geo, mtl)
      mesh.renderOrder = 9
      mesh.userData = { phase: i * 1.3, baseY: this.height * (0.95 + i * 0.12) }
      this.smoke.push(mesh)
      this.add(mesh)
    }
  }

  _createPointLight() {
    this.pointLight = new THREE.PointLight(0xff7a26, this.lightIntensity, this.height * 4.5, 2)
    this.pointLight.position.set(0, this.height * 0.4, 0)
    this.add(this.pointLight)
  }

  update(delta, elapsed) {
    if (!this._visible || !this.texture) return
    const t = elapsed ?? this.clock.getElapsedTime()

    this.frameAccumulator += delta * this.fps
    if (this.frameAccumulator >= 1) {
      const step = Math.floor(this.frameAccumulator)
      this.currentFrame = (this.currentFrame + step) % this.totalFrames
      this.frameAccumulator %= 1
      const col = this.currentFrame % this.columns
      const row = Math.floor(this.currentFrame / this.columns)
      const uOffset = col / this.columns
      const vOffset = 1 - (row + 1) / this.rows
      for (const m of this.billboards) {
        m.material.map.offset.set(uOffset, vOffset)
        m.material.map.needsUpdate = true
      }
    }

    const k = this.intensity
    for (const mesh of this.billboards) {
      const { baseW, baseH, baseOpacity, phase } = mesh.userData
      const flick = 1 + Math.sin(t * 9 + phase) * 0.07 + Math.sin(t * 23 + phase) * 0.03
      // 强度低时火苗矮而窄，强度高时横向铺开、向上窜
      mesh.scale.set(baseW * flick, baseH * flick * (0.55 + k * 0.75), 1)
      mesh.material.opacity = baseOpacity * k * (0.82 + Math.sin(t * 14 + phase * 2) * 0.18)
    }

    // 烟：强度越高越浓、越往上堆
    for (const mesh of this.smoke) {
      const { phase, baseY } = mesh.userData
      const rise = baseY * (0.6 + k * 0.6) + Math.sin(t * 0.7 + phase) * this.height * 0.06
      mesh.position.y = rise
      mesh.rotation.y = phase + t * 0.12
      const s = (0.75 + k * 0.7) * (1 + Math.sin(t * 0.9 + phase) * 0.08)
      mesh.scale.set(s, s, 1)
      mesh.material.opacity = Math.max(0, (k - 0.18)) * 0.34
    }

    if (this.pointLight) {
      this.pointLight.intensity =
        this.lightIntensity * k * (0.82 + Math.sin(t * 11) * 0.18)
    }
  }

  setIntensity(v) {
    this.intensity = THREE.MathUtils.clamp(v, 0, 1)
  }

  /**
   * 注意：不要在子类里定义 `visible` 访问器。
   * Object3D.visible 是数据属性，覆写成只有 getter 的访问器会让
   * 父类的赋值静默失败（strict mode 下直接抛错），整个系统无法显隐。
   * 这里用 _visible 记录“显式设置”的意图，渲染开关仍走继承来的 visible。
   */
  setVisibility(visible) {
    this._visible = !!visible
    this.visible = this._visible
    if (this._visible) this.clock.getDelta()
  }

  /** 移动火焰锚点（进入舱室视图时用） */
  moveTo(pos) {
    this.anchor.copy(pos)
    this.position.copy(pos)
  }

  resetPosition() {
    this.position.copy(this.anchor)
  }

  /** 按舱室配置设定火焰基准尺寸与起火点 */
  applyProfile({ width, height, origin }) {
    if (width) this.width = width
    if (height) this.height = height
    /*
     * three.js 的物理光照单位下，PointLight 的 intensity 是坎德拉，
     * 表面照度 ≈ I / d²。数值一旦给大（几百 cd），
     * 2m 外的舱壁照度就上百，整个舱室会被冲成一片橙白，
     * 火焰本身反而显得更小。这里按尺寸给一个很克制的量级。
     */
    this.lightIntensity = Math.max(6, this.width * this.height * 1.2)
    for (const mesh of this.billboards) {
      mesh.geometry.dispose()
      mesh.geometry = new THREE.PlaneGeometry(this.width, this.height)
      mesh.position.x = this.width * (mesh.userData.baseW > 0.9 ? 0 : mesh.userData.baseW > 0.8 ? 0.18 : -0.14)
      mesh.position.y = this.height * (mesh.userData.baseH > 0.9 ? 0.62 : mesh.userData.baseH > 0.75 ? 0.46 : 0.38)
    }
    for (const mesh of this.smoke || []) {
      mesh.geometry.dispose()
      mesh.geometry = new THREE.PlaneGeometry(this.width * 1.5, this.height * 1.1)
    }
    if (this.pointLight) {
      this.pointLight.distance = this.height * 4.5
      this.pointLight.position.set(0, this.height * 0.4, 0)
    }
    if (origin) this.moveTo(origin)
  }

  dispose() {
    for (const mesh of this.billboards) {
      mesh.geometry.dispose()
      if (mesh.material.map) mesh.material.map.dispose()
      mesh.material.dispose()
    }
    for (const mesh of this.smoke || []) {
      mesh.geometry.dispose()
      mesh.material.dispose()
    }
    this.smoke = []
    this.billboards.length = 0
    if (this.texture) this.texture.dispose()
    if (this.pointLight) this.pointLight.dispose()
    this.clear()
  }
}
