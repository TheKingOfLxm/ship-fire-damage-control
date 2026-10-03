/**
 * LSTM 火灾预测服务客户端
 * ─────────────────────────────────────────────────────────────
 * 注意地址来源：旧代码读 `VITE_LSTM_API_URL`，而 config/.env.* 里写的是
 * `VITE_LSTM_BASE_URL`，两者对不上 → 永远回退到硬编码的地址。
 * 现在两种变量名都接受，端口与 Flask 实际监听（5001）对齐。
 */

import axios from 'axios'

// 端口与 lstm-prediction-server/server.py 的 app.run(port=5001) 保持一致
const LSTM_API_BASE =
  import.meta.env.VITE_LSTM_API_URL ||
  import.meta.env.VITE_LSTM_BASE_URL ||
  'http://localhost:5001'

/** ISO 字符串 / 数字统一转毫秒。后端 /history 给的是 ISO 字符串，
 *  直接拿字符串做算术会得到 NaN，整条重采样序列跟着变 NaN。 */
const toMs = v => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const n = Number(v)
    if (Number.isFinite(n)) return n
    const t = Date.parse(v)
    return Number.isFinite(t) ? t : null
  }
  return null
}

/** ppm → mol/mol。服务端模型通道用摩尔分数（CO 峰值约 5e-4），
 *  而后端历史里的 CO/CO₂ 是 ppm（数百到数万）。阈值取 0.05：
 *  大于它的一律按 ppm 换算，小于它的已经是摩尔分数。 */
const toMol = v => (Number.isFinite(v) && v > 0.05 ? v / 1e6 : v)

/**
 * LSTM预测服务类
 */
class LSTMPredictionService {
  constructor() {
    this.baseURL = LSTM_API_BASE
    this.isAvailable = false
    this.lastHealthCheck = null
    this.lastHealthAt = 0
    this.config = null
    this.availableCompartments = null
    this.degradedCompartments = {}
    this.lastError = null
  }

  /**
   * 检查服务健康状态（带缓存，避免每次预测前都打一次）
   * @param {boolean} [force] 忽略缓存强制探测
   */
  async checkHealth(force = false) {
    // 30s 内复用上次结果
    if (!force && this.lastHealthAt && Date.now() - this.lastHealthAt < 30000) {
      return this.lastHealthCheck
    }
    try {
      const response = await axios.get(`${this.baseURL}/api/lstm/health`, {
        timeout: 5000
      })
      this.isAvailable = response.data?.status === 'ok'
      this.availableCompartments = response.data?.available_compartments || null
      // 还没换到 v6 配方的舱室：实测（tools/regress_online.py）它们自回归
      // 会塌缩成常数 —— 曲线看着平滑收敛、不报警，值班员会当成结论。
      // UI 必须显式降级提示，不能只当曲线展示。
      this.degradedCompartments = response.data?.degraded_compartments || {}
      this.lastHealthCheck = response.data
      this.lastHealthAt = Date.now()
      this.lastError = null
      return response.data
    } catch (error) {
      this.isAvailable = false
      this.lastHealthAt = Date.now()
      this.lastHealthCheck = null
      this.lastError = error.message
      return null
    }
  }

  /**
   * 获取模型配置
   */
  async getConfig() {
    if (this.config) return this.config

    try {
      const response = await axios.get(`${this.baseURL}/api/lstm/config`, {
        timeout: 5000
      })
      this.config = response.data.data
      return this.config
    } catch (error) {
      console.error('获取LSTM配置失败:', error)
      return null
    }
  }

  /**
   * 获取支持预测的舱室列表
   */
  async getAvailableCompartments() {
    try {
      const response = await axios.get(`${this.baseURL}/api/lstm/compartments`, {
        timeout: 5000
      })
      return response.data.data || []
    } catch (error) {
      console.error('获取舱室列表失败:', error)
      return []
    }
  }

  /**
   * 解析某舱室模型真实的步长与窗口长度。
   *
   * 优先用 /api/lstm/config 的 modelShapes（那是 checkpoint 里记的值），
   * 退化时才用全局字段兜底。取不到就按 0.1s / 150 步保守处理，
   * 服务端 predict() 还会按模型自己的 seq_len 截断，不会因此报错。
   */
  shapeOf(compartmentName) {
    const cfg = this.config || {}
    const shapes = cfg.modelShapes || {}
    let hit = shapes[compartmentName]
    if (!hit) {
      for (const [name, s] of Object.entries(shapes)) {
        if (name.includes(compartmentName) || compartmentName.includes(name)) { hit = s; break }
      }
    }
    const stepSeconds = Number.isFinite(hit?.stepSeconds) && hit.stepSeconds > 0
      ? hit.stepSeconds
      : (Number.isFinite(hit?.inputSeconds) && hit?.seqLen
        ? hit.inputSeconds / hit.seqLen
        : (Number(cfg.timeStepSeconds) > 0 ? cfg.timeStepSeconds : 0.1))
    return {
      stepSeconds,
      seqLen: Number(hit?.seqLen) > 0 ? hit.seqLen
        : (Number(cfg.sequenceLength) > 0 ? cfg.sequenceLength : 150),
      predLen: Number(hit?.predLen) > 0 ? hit.predLen : null,
      // 单次前向的可靠时长。超过它之后是累积外推，实测精度逐段劣化：
      // 灶炉间 0~30s 2.48℃、30~60s 14.0℃、120~300s 30.8℃。
      reliableHorizonSeconds: Number(hit?.reliableHorizonSeconds) > 0
        ? hit.reliableHorizonSeconds : null,
      degradedChannels: hit?.degenerateChannels || []
    }
  }

  /**
   * 长时程态势投影（供损管做 5~30 分钟决策窗口）。
   *
   * 为什么不是纯 LSTM：自回归外推会收敛到极限环（实测 33 秒后轨迹开始
   * 周期重复），硬推 30 分钟只会得到一条看着权威的假曲线。
   * 服务端因此分段：LSTM 段用真实模型输出，之后改用舱室火灾增长律，
   * 逐点用 basis 字段标出来源。
   *
   * @param {number} [suppression] 反事实灭火水平 0..1：v7 干预模型的舱室
   *   传 1 即"如果全力扑，火会怎么走"的对照投影。旧模型忽略该参数。
   */
  async project(compartmentName, recentReadings, horizonSeconds = 900, suppression = 0) {
    if (!Array.isArray(recentReadings) || recentReadings.length < 2) return null
    await this.checkHealth()
    if (!this.isAvailable) return null
    try {
      const sh = this.shapeOf(compartmentName)
      const res = await axios.post(`${this.baseURL}/api/lstm/projection`, {
        compartmentName,
        historyData: this._resampleTo(recentReadings, sh.seqLen, sh.stepSeconds),
        horizonSeconds,
        suppression: Math.max(0, Math.min(1, Number(suppression) || 0))
      }, { timeout: 120000 })
      if (res.data?.code === 200) return res.data.data
      return null
    } catch (error) {
      this.lastError = error?.response?.data?.message || error.message
      return null
    }
  }

  /** 按时间戳线性插值重采样到 N 个等间隔点。
   *
   *  单位约定：送服务的 CO/CO₂ 必须是摩尔分数 —— 后端历史里的 ppm
   *  直接送过去会把窗口第三通道抬到 40% 量级（服务端按 mol/mol 解析），
   *  输入分布整个错位，投影质量 silently 劣化。这里统一换算。 */
  _resampleTo(recentReadings, seqLen, step) {
    const field = (d, k, fb) => {
      const v = Number(d?.[k])
      return Number.isFinite(v) ? v : fb
    }
    const pts = [...recentReadings]
      .filter(d => Number.isFinite(Number(d?.temperature)))
      .map(d => ({ ...d, _t: toMs(d.timestamp) }))
      .sort((a, b) => (a._t ?? 0) - (b._t ?? 0))
    if (!pts.length) return []
    pts.forEach((d, i) => { if (d._t == null) d._t = i * 1000 })
    if (pts.length < 2) pts.forEach(d => { d._t += 1000 })

    const tEnd = pts[pts.length - 1]._t
    const out = []
    for (let i = 0; i < seqLen; i++) {
      const target = tEnd - (seqLen - 1 - i) * step * 1000
      let lo = 0
      while (lo < pts.length - 2 && pts[lo + 1]._t <= target) lo++
      const a = pts[lo]
      const b = pts[Math.min(lo + 1, pts.length - 1)]
      const tb = b._t > a._t ? b._t : a._t + 1000
      const u = Math.max(0, Math.min(1, (target - a._t) / (tb - a._t)))
      const mix = k => field(a, k, 0) + (field(b, k, 0) - field(a, k, 0)) * u
      out.push({
        temperature: mix('temperature'),
        co: toMol(mix('co')),
        co2: toMol(field(a, 'co2', 400))
      })
    }
    return out
  }

  /**
   * LSTM 不可用时的降级推演
   *
   * 直接用当前读数与变化率做一阶外推。没有模型可信，但比"什么都不显示"
   * 好得多；关键是**如实标注这是推演而非模型预测**。
   *
   * @param {string} compartmentName
   * @param {Array} recentReadings
   * @param {number} forecastSeconds
   * @param {object} fuel 舱室燃料参数，用于给出合理的峰值上限
   */
  extrapolate(compartmentName, recentReadings, forecastSeconds = 15, fuel = null) {
    const n = recentReadings.length
    if (!n) return null
    const points = recentReadings.map(d => ({ ...d, _t: toMs(d.timestamp) }))
    // 缺时间戳的按等间隔补上，保证 dt 不会是 NaN
    points.forEach((d, i) => { if (d._t == null) d._t = i * 1000 })

    const last = points[n - 1]
    // 取最近若干点估算变化率
    const win = points.slice(Math.max(0, n - 6))
    const rate = k => {
      const a = win[0]?.[k] ?? 0
      const b = last?.[k] ?? 0
      const dt = Math.max(1, (last._t - win[0]._t) / 1000)
      return (b - a) / dt
    }

    const rT = rate('temperature')
    const rS = rate('smoke')
    const rO = rate('oxygen')
    const rC = rate('co')
    const cap = fuel?.tMax ?? 800
    const STEP = 0.1

    const predictions = []
    for (let i = 0; i < Math.ceil(forecastSeconds / STEP); i++) {
      const t = i * STEP
      predictions.push({
        time: Number(t.toFixed(2)),
        temperature: Math.min(cap, last.temperature + rT * t),
        smoke: Math.max(0, Math.min(100, last.smoke + rS * t)),
        oxygen: Math.max(8, last.oxygen + rO * t),
        co: Math.max(0, last.co + rC * t)
      })
    }
    const lastP = predictions[predictions.length - 1] || last
    const temps = predictions.map(p => p.temperature)
    return {
      predictions,
      summary: {
        maxTemperature: Math.max(...temps, last.temperature),
        minTemperature: Math.min(...temps, last.temperature),
        avgTemperature: temps.reduce((a, b) => a + b, 0) / (temps.length || 1),
        maxCO: lastP.co,
        trend: rT > 0.5 ? 'rising' : rT < -0.5 ? 'falling' : 'stable',
        riskLevel: lastP.temperature > 400 ? 'high' : lastP.temperature > 200 ? 'medium' : 'low',
        predictionDuration: forecastSeconds
      },
      source: 'extrapolation',
      sourceLabel: '变化率外推（预测服务不可用）',
      horizonSeconds: forecastSeconds
    }
  }
}

// 导出单例
export const lstmService = new LSTMPredictionService()

// 默认导出
export default lstmService
