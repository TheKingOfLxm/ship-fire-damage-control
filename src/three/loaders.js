/**
 * GLTF 加载器工厂
 *
 * 船模与舱室模型均由 Blender 以 Draco 压缩导出，解码器自托管于 /draco/，
 * 不依赖任何外部 CDN，离线可用。
 */
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'

const DRACO_PATH = '/draco/'

let _loader = null
let _draco = null

function getDraco() {
  if (!_draco) {
    _draco = new DRACOLoader()
    _draco.setDecoderPath(DRACO_PATH)
    _draco.setDecoderConfig({ type: 'wasm' })
  }
  return _draco
}

/** 单例 GLTFLoader（解码器只初始化一次） */
export function getGLTFLoader() {
  if (!_loader) {
    _loader = new GLTFLoader()
    _loader.setDRACOLoader(getDraco())
  }
  return _loader
}

/**
 * 带进度回调的模型加载
 * @param {string} url
 * @param {(loaded:number,total:number)=>void} [onProgress] 0~1
 * @returns {Promise<{scene: import('three').Object3D, gltf: any}>}
 */
export function loadGLTF(url, onProgress) {
  return new Promise((resolve, reject) => {
    getGLTFLoader().load(
      url,
      gltf => resolve(gltf),
      evt => {
        if (onProgress) {
          const total = evt.total || 0
          onProgress(total > 0 ? Math.min(1, evt.loaded / total) : 0, evt.loaded, total)
        }
      },
      err => reject(err || new Error(`模型加载失败: ${url}`))
    )
  })
}

/** 递归释放一棵对象树的几何体/材质/贴图 */
export function disposeObject(root) {
  if (!root) return
  root.traverse(child => {
    if (child.geometry) child.geometry.dispose()
    const mats = Array.isArray(child.material) ? child.material : child.material ? [child.material] : []
    for (const m of mats) {
      for (const key of Object.keys(m)) {
        const val = m[key]
        if (val && val.isTexture) val.dispose()
      }
      m.dispose()
    }
  })
}

export function disposeLoader() {
  if (_draco) {
    _draco.dispose()
    _draco = null
  }
  _loader = null
}
