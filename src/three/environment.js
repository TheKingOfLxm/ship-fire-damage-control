/**
 * 场景环境构建 —— 物理天空 + 程序化海面 + 灯光 + 雾
 *
 * 完全程序化，不依赖任何外部贴图资源。
 * 环境球经 PMREM 卷积后作为 scene.environment，为船体提供正确的 PBR 反射。
 */
import * as THREE from 'three'
import { Sky } from 'three/examples/jsm/objects/Sky.js'

/**
 * 海面着色器
 *
 * 之前的版本只有 4 组正弦波、法线也来自同一组低频高度场，远看就是一张
 * 起伏的贴图。这里分三层来做：
 *   1. 顶点位移 —— 7 个不同方向/波长的波叠加，负责大形
 *   2. 片元细节法线 —— 4 组高频波只扰动法线不做位移，负责"水"的质感
 *   3. 泡沫 —— 浪尖白沫 + 船体周围被扰动的水线白浪
 * 再配合菲涅耳天空反射、深度色渐变与阳光碎光。
 */

const SEA_VERTEX = /* glsl */ `
  uniform float uTime;
  varying vec3 vWorldPos;
  varying vec3 vNormal;
  varying float vCrest;

  // 7 个方向分量：长涌浪为主，短波为辅
  const vec2 D0 = vec2( 0.0210,  0.0170);
  const vec2 D1 = vec2(-0.0130,  0.0310);
  const vec2 D2 = vec2( 0.0430, -0.0110);
  const vec2 D3 = vec2( 0.0080,  0.0520);
  const vec2 D4 = vec2(-0.0620, -0.0340);
  const vec2 D5 = vec2( 0.0870,  0.0640);
  const vec2 D6 = vec2(-0.1150,  0.0930);

  float waveHeight(vec2 p) {
    float h = 0.0;
    h += sin(dot(p, D0) + uTime * 0.62) * 0.52;
    h += sin(dot(p, D1) + uTime * 0.87) * 0.32;
    h += sin(dot(p, D2) + uTime * 1.23) * 0.19;
    h += sin(dot(p, D3) + uTime * 1.71) * 0.11;
    h += sin(dot(p, D4) + uTime * 2.35) * 0.065;
    h += sin(dot(p, D5) + uTime * 3.10) * 0.036;
    h += sin(dot(p, D6) + uTime * 4.20) * 0.020;
    return h;
  }

  void main() {
    vec3 world = (modelMatrix * vec4(position, 1.0)).xyz;
    float eps = 1.6;
    float h  = waveHeight(world.xz);
    float hx = waveHeight(world.xz + vec2(eps, 0.0));
    float hz = waveHeight(world.xz + vec2(0.0, eps));
    world.y += h;
    vNormal = normalize(vec3(-(hx - h) / eps, 1.0, -(hz - h) / eps));
    vCrest = h;
    vWorldPos = world;
    gl_Position = projectionMatrix * viewMatrix * vec4(world, 1.0);
  }
`

const SEA_FRAGMENT = /* glsl */ `
  uniform vec3  uDeep;
  uniform vec3  uShallow;
  uniform vec3  uSkyTint;
  uniform vec3  uSunDir;
  uniform vec3  uSunColor;
  uniform vec3  uFogColor;
  uniform float uFogNear;
  uniform float uFogFar;
  uniform float uTime;
  uniform float uShipHalfLen;
  uniform float uShipHalfBeam;
  varying vec3 vWorldPos;
  varying vec3 vNormal;
  varying float vCrest;

  // 船体在水面的投影：沿 X 的线段 + 椭圆半径，用于生成水线白浪
  float hullDist(vec2 p) {
    vec2 a = vec2(-uShipHalfLen, 0.0);
    vec2 b = vec2( uShipHalfLen, 0.0);
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h) - uShipHalfBeam;
  }

  // 高频细节法线：只扰动法线，不做几何位移。
  // 振幅必须克制 —— 拉高会立刻变成瓦楞板/搓衣板而不是水。
  vec3 detailNormal(vec2 p, float t) {
    vec2 g = vec2(0.0);
    g += vec2( 0.42,  0.15) * cos(dot(p, vec2(0.31,  0.17)) + t * 2.1) * 0.135;
    g += vec2(-0.21,  0.46) * cos(dot(p, vec2(0.53,  0.29)) + t * 2.7) * 0.095;
    g += vec2( 0.14, -0.33) * cos(dot(p, vec2(1.13,  0.87)) + t * 3.9) * 0.055;
    g += vec2(-0.48, -0.22) * cos(dot(p, vec2(2.31,  1.77)) + t * 5.3) * 0.032;
    g += vec2( 0.30,  0.52) * cos(dot(p, vec2(4.70,  3.90)) + t * 7.1) * 0.016;
    return normalize(vec3(g.x, 1.0, g.y));
  }

  void main() {
    vec3 V = normalize(cameraPosition - vWorldPos);
    float dist = length(vWorldPos.xz - cameraPosition.xz);

    // 远处更早地衰减细节：高频法线在远景会剧烈闪烁
    float detailFade = 1.0 - smoothstep(50.0, 420.0, dist);
    vec3 dn = detailNormal(vWorldPos.xz, uTime);
    vec3 N = normalize(vNormal + vec3(dn.x, 0.0, dn.z) * 0.9 * detailFade);

    float fres = pow(1.0 - clamp(dot(N, V), 0.0, 1.0), 4.0);

    // 深浅色：近处偏深水，远处趋于海色
    float depthMix = smoothstep(40.0, 800.0, dist);
    vec3 base = mix(uDeep, uShallow, depthMix);
    // 浪尖被水下散射照亮，偏青绿
    float sss = smoothstep(0.25, 0.9, vCrest) * max(0.0, dot(N, uSunDir)) * 0.18;
    base += vec3(0.05, 0.16, 0.14) * sss;

    vec3 col = mix(base, uSkyTint, clamp(fres * 1.2, 0.0, 0.94));

    // 阳光碎光：随细节法线闪烁
    vec3 H = normalize(normalize(uSunDir) + V);
    float spec = pow(max(dot(N, H), 0.0), mix(420.0, 60.0, detailFade));
    col += uSunColor * spec * 1.1 * detailFade;

    // 泡沫：只在最高浪尖 + 船体近旁，且克制
    float crestFoam = smoothstep(0.62, 0.82, vCrest) * 0.30;
    float wake = 1.0 - smoothstep(0.0, 9.0, abs(hullDist(vWorldPos.xz)));
    float wakeFoam = wake * (0.30 + 0.16 * sin(vWorldPos.x * 0.6 + uTime * 2.4)
                              + 0.12 * sin(vWorldPos.z * 0.9 - uTime * 3.1));
    float foam = clamp(crestFoam + max(0.0, wakeFoam), 0.0, 0.5) * detailFade;
    col = mix(col, vec3(0.80, 0.86, 0.90), foam);

    float fogF = smoothstep(uFogNear, uFogFar, dist);
    col = mix(col, uFogColor, fogF);

    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`

export const ENV_PRESET = {
  sunElevation: 34,
  sunAzimuth: 42,
  turbidity: 4.2,
  rayleigh: 1.1,
  mieCoefficient: 0.006,
  mieDirectionalG: 0.82,
  // 雾要够远，否则海天在地平线处糊成一片，看不出海面
  fogNear: 420,
  fogFar: 2600,
  deep: 0x061622,
  shallow: 0x123f5c,
  horizon: 0x8aa6bf,
  /** 船体水线投影，用于生成船周白浪 */
  shipHalfLen: 58,
  shipHalfBeam: 6.9
}

/**
 * 构建天空 + 海面 + 灯光
 * @returns {{ sky, sea, sun, sunDir, dispose: Function, params }}
 */
export function createEnvironment(scene, renderer, preset = ENV_PRESET) {
  const params = { ...preset }

  /* ---------------- 天空 ---------------- */
  const sky = new Sky()
  sky.scale.setScalar(12000)
  const u = sky.material.uniforms
  u.turbidity.value = params.turbidity
  u.rayleigh.value = params.rayleigh
  u.mieCoefficient.value = params.mieCoefficient
  u.mieDirectionalG.value = params.mieDirectionalG

  const phi = THREE.MathUtils.degToRad(90 - params.sunElevation)
  const theta = THREE.MathUtils.degToRad(params.sunAzimuth)
  const sunDir = new THREE.Vector3().setFromSphericalCoords(1, phi, theta)

  u.sunPosition.value.copy(sunDir)
  scene.add(sky)

  /* ---------------- 海面 ---------------- */
  // 顶点数按"近处密、远处可以糙"分配：整片 1800m 均匀 220 段，
  // 保证中景波形不出现折线感
  const seaGeo = new THREE.PlaneGeometry(1800, 1800, 220, 220)
  seaGeo.rotateX(-Math.PI / 2)
  const seaMat = new THREE.ShaderMaterial({
    vertexShader: SEA_VERTEX,
    fragmentShader: SEA_FRAGMENT,
    uniforms: {
      uTime: { value: 0 },
      uDeep: { value: new THREE.Color(params.deep) },
      uShallow: { value: new THREE.Color(params.shallow) },
      uSkyTint: { value: new THREE.Color(0xa8c4dc) },
      uSunDir: { value: sunDir.clone() },
      uSunColor: { value: new THREE.Color(0xfff2dc) },
      uFogColor: { value: new THREE.Color(params.horizon) },
      uFogNear: { value: params.fogNear },
      uFogFar: { value: params.fogFar },
      uShipHalfLen: { value: params.shipHalfLen },
      uShipHalfBeam: { value: params.shipHalfBeam }
    }
  })
  const sea = new THREE.Mesh(seaGeo, seaMat)
  sea.position.y = -0.15
  sea.renderOrder = -1
  scene.add(sea)

  /* ---------------- 灯光 ---------------- */
  // 太阳：投射阴影
  const sun = new THREE.DirectionalLight(0xfff4e0, 3.1)
  sun.position.copy(sunDir).multiplyScalar(220)
  sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048)
  sun.shadow.camera.near = 40
  sun.shadow.camera.far = 420
  const S = 90
  sun.shadow.camera.left = -S
  sun.shadow.camera.right = S
  sun.shadow.camera.top = S
  sun.shadow.camera.bottom = -S
  sun.shadow.bias = -0.0006
  sun.shadow.normalBias = 0.045
  scene.add(sun)
  scene.add(sun.target)

  // 天空/海面反射补光
  const hemi = new THREE.HemisphereLight(0xa8c6e0, 0x16232e, 0.9)
  scene.add(hemi)

  // 逆向轮廓补光，避免暗面死黑
  const rim = new THREE.DirectionalLight(0x7d9ec4, 0.55)
  rim.position.set(-sunDir.x * 160, 60, -sunDir.z * 160)
  scene.add(rim)

  /* ---------------- 环境球 (IBL) ---------------- */
  const pmrem = new THREE.PMREMGenerator(renderer)
  pmrem.compileEquirectangularShader()
  // 单独用一个只含天空的场景做卷积，避免把海面/船体也卷进去
  const envScene = new THREE.Scene()
  const skyClone = new Sky()
  skyClone.scale.setScalar(12000)
  const cu = skyClone.material.uniforms
  cu.turbidity.value = params.turbidity
  cu.rayleigh.value = params.rayleigh
  cu.mieCoefficient.value = params.mieCoefficient
  cu.mieDirectionalG.value = params.mieDirectionalG
  cu.sunPosition.value.copy(sunDir)
  envScene.add(skyClone)
  const envRT = pmrem.fromScene(envScene, 0.04)
  scene.environment = envRT.texture
  // 天空 IBL 亮度很高，不压低会让低反照率的海军灰全部泛白
  scene.environmentIntensity = 0.5
  pmrem.dispose()
  skyClone.geometry.dispose()
  skyClone.material.dispose()

  /* ---------------- 雾 ---------------- */
  scene.fog = new THREE.Fog(new THREE.Color(params.horizon), params.fogNear, params.fogFar)
  scene.background = null // 由 Sky 网格充当背景

  /* ---------------- 舱内模式 ---------------- */
  // 舱室是开顶的，会被太阳和环境球完整照亮。用室外的光照强度渲染舱内，
  // 无论材质反照率压得多低都会糊成一片白。舱内改用低强度定向光 +
  // 微弱环境光，靠明暗对比而不是亮度来读结构。
  const OUTDOOR = {
    sun: sun.intensity, hemi: hemi.intensity, rim: rim.intensity,
    env: scene.environmentIntensity, exposure: 0.92
  }
  const INTERIOR = {
    sun: 0.95, hemi: 0.48, rim: 0.28, env: 0.16, exposure: 1.25
  }

  let interior = false

  return {
    sky,
    sea,
    sun,
    sunDir,
    params,
    /** 外部雾对象：舱室视图下会临时摘除，入舱时还原 */
    fog: scene.fog,

    /** 切换户外/舱内光照预设 */
    setInteriorMode(on) {
      if (on === interior) return
      interior = on
      const p = on ? INTERIOR : OUTDOOR
      sun.intensity = p.sun
      hemi.intensity = p.hemi
      rim.intensity = p.rim
      scene.environmentIntensity = p.env
      if (renderer) renderer.toneMappingExposure = p.exposure
    },
    /** 每帧调用以推进波浪动画 */
    update(elapsed) {
      seaMat.uniforms.uTime.value = elapsed
    },
    dispose() {
      scene.remove(sky, sea, sun, sun.target, hemi, rim)
      seaGeo.dispose()
      seaMat.dispose()
      sky.geometry.dispose()
      sky.material.dispose()
      envRT.dispose()
      scene.environment = null
    }
  }
}
