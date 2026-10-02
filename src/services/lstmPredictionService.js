/**
 * LSTM 火灾预测服务客户端
 * ─────────────────────────────────────────────────────────────
 * 注意地址来源：旧代码读 `VITE_LSTM_API_URL`，而 config/.env.* 里写的是
 * `VITE_LSTM_BASE_URL`，两者对不上 → 永远回退到硬编码的 5000。
 * 而 Flask 服务实际监听 **5001**（启动日志里打印的 5000 是写死的假消息）。
 * 结果就是健康检查永远失败、预测永远走降级。
 * 现在两种变量名都接受，端口与 Flask 实际监听对齐。
 */

import axios from 'axios'

// 端口与 lstm-prediction-server/server.py 的 app.run(port=5001) 保持一致
const LSTM_API_BASE =
  import.meta.env.VITE_LSTM_API_URL ||
  import.meta.env.VITE_LSTM_BASE_URL ||
  'http://localhost:5001'

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
      // 会塌缩成常数 —— 电站间 300 秒末给 379.5℃±0.1，真值 43.8℃±8.0，
      // 预测跨度 0.2℃ 对真值 17.7℃。曲线看着平滑收敛、不报警，值班员
      // 会当成结论。UI 必须显式降级提示，不能只当曲线展示。
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
   * 进行火灾演化预测
   * @param {string} compartmentName - 舱室名称
   * @param {Array} historyData - 历史数据数组 [{temperature, co, co2, smoke, oxygen}, ...]
   * @param {number} predictionSteps - 预测步数（每步约0.1秒）
   * @returns {Promise<Object>} 预测结果
   */
  async predict(compartmentName, historyData, predictionSteps = 150) {
    // 检查服务可用性
    if (!this.isAvailable) {
      await this.checkHealth()
      if (!this.isAvailable) {
        throw new Error('LSTM预测服务不可用，请确保服务已启动')
      }
    }

    try {
      const response = await axios.post(`${this.baseURL}/api/lstm/predict`, {
        compartmentName,
        historyData,
        predictionSteps
      }, {
        timeout: 30000  // 预测可能需要较长时间
      })

      if (response.data.code === 200) {
        return response.data.data
      } else {
        throw new Error(response.data.message || '预测失败')
      }
    } catch (error) {
      if (error.response) {
        throw new Error(error.response.data?.message || '服务器错误')
      }
      throw error
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
   * 该舱室的模型是否还是旧配方（预测会塌缩成常数）。
   * @returns {{degraded: boolean, source: string|null, reliableHorizonSeconds: number|null, reason: string|null}}
   */
  modelQuality(compartmentName) {
    const cfg = this.config || {}
    let degraded = cfg.degradedCompartments?.[compartmentName] || null
    if (!degraded) {
      // 名字对不上时（灶炉间 / 炉灶间）按包含关系再找一次
      for (const [name, info] of Object.entries(cfg.degradedCompartments || {})) {
        if (name.includes(compartmentName) || compartmentName.includes(name)) {
          degraded = info
          break
        }
      }
    }
    const sh = this.shapeOf(compartmentName)
    return {
      degraded: !!degraded,
      source: degraded?.source || (degraded ? '未知' : 'v6'),
      reliableHorizonSeconds: degraded?.reliableHorizonSeconds
        ?? sh.reliableHorizonSeconds,
      reason: degraded?.reason || null
    }
  }

  /**
   * 长时程态势投影（供损管做 5~30 分钟决策窗口）。
   *
   * 为什么不是纯 LSTM：自回归外推会收敛到极限环（实测 33 秒后轨迹开始
   * 周期重复），硬推 30 分钟只会得到一条看着权威的假曲线。
   * 服务端因此分段：LSTM 段用真实模型输出，之后改用舱室火灾增长律，
   * 逐点用 basis 字段标出来源。
   */
  async project(compartmentName, recentReadings, horizonSeconds = 900) {
    if (!Array.isArray(recentReadings) || recentReadings.length < 2) return null
    await this.checkHealth()
    if (!this.isAvailable) return null
    try {
      const sh = this.shapeOf(compartmentName)
      const res = await axios.post(`${this.baseURL}/api/lstm/projection`, {
        compartmentName,
        historyData: this._resampleTo(recentReadings, sh.seqLen, sh.stepSeconds),
        horizonSeconds
      }, { timeout: 120000 })
      if (res.data?.code === 200) return res.data.data
      return null
    } catch (error) {
      this.lastError = error?.response?.data?.message || error.message
      return null
    }
  }

  /**
   * 长时程态势预测。
   *
   * 走 /api/lstm/forecast 而不是 /api/lstm/predict：后者一次前向只给
   * pred_len 步（1.5 秒），对损管没有意义。forecast 会自回归推演到
   * 指定的未来时长，并给出阶段判定、峰值温度与达峰时间。
   */
  async forecastLongHorizon(compartmentName, recentReadings, horizonSeconds = 60) {
    if (!Array.isArray(recentReadings) || recentReadings.length < 2) return null
    await this.checkHealth()
    if (!this.isAvailable) return null
    try {
      const step = this.shapeOf(compartmentName).stepSeconds
      const seqLen = this.shapeOf(compartmentName).seqLen
      const series = this._resampleTo(recentReadings, seqLen, step)
      const res = await axios.post(`${this.baseURL}/api/lstm/forecast`, {
        compartmentName,
        historyData: series,
        horizonSeconds
      }, { timeout: 60000 })
      if (res.data?.code === 200) return res.data.data
      return null
    } catch (error) {
      this.lastError = error?.response?.data?.message || error.message
      return null
    }
  }

  /** 按时间戳线性插值重采样到 N 个等间隔点 */
  _resampleTo(recentReadings, seqLen, step) {
    const ms = v => {
      if (typeof v === 'number') return Number.isFinite(v) ? v : null
      if (typeof v === 'string') {
        const n = Number(v)
        if (Number.isFinite(n)) return n
        const t = Date.parse(v)
        return Number.isFinite(t) ? t : null
      }
      return null
    }
    const field = (d, k, fb) => {
      const v = Number(d?.[k])
      return Number.isFinite(v) ? v : fb
    }
    const pts = [...recentReadings]
      .filter(d => Number.isFinite(Number(d?.temperature)))
      .map(d => ({ ...d, _t: ms(d.timestamp) }))
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
        co: mix('co'),
        co2: Number.isFinite(mix('co2')) ? mix('co2') : 3.9e-4
      })
    }
    return out
  }

  /**
   * 从当前火灾数据生成预测
   *
   * 关键点：模型输入窗口是 **150 步 × 0.1s**（= 15 秒），
   * 而前端的历史采样是 **1s 一次**。直接把 1s 序列喂进去，
   * 时间尺度会差 10 倍，预测出的"未来 15 秒"实际覆盖了几分钟。
   * 这里按时间戳线性插值重采样到 150 个 0.1s 的点再送模型。
   *
   * @param {string} compartmentName - 舱室名称
   * @param {Array<{timestamp?:number,temperature:number,smoke:number,oxygen:number,co:number}>} recentReadings
   * @param {number} forecastSeconds - 预测未来多少秒
   * @returns {Promise<Object>} 统一结构的预测结果
   */
  async forecastFromCurrent(compartmentName, recentReadings, forecastSeconds = 15) {
    if (!Array.isArray(recentReadings) || recentReadings.length === 0) {
      return null
    }

    // 序列不足时向前用最早的一点补齐，保证模型拿满窗口长度
    //
    // ⚠ timestamp 必须先归一化成毫秒数：后端 /history 返回的是 ISO 字符串
    // （"2026-10-01T03:11:34.000Z"），而下面全按数字做算术。
    // 直接拿字符串减数字会得到 NaN，NaN 一路传进 mix() 让整条重采样序列
    // 变成 NaN，序列化成 JSON 又是 null，服务端最终报
    // "unsupported operand type(s) for +: 'NoneType' and 'float'"。
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
    const sorted = [...recentReadings]
      .filter(d => Number.isFinite(d?.temperature))
      .map(d => ({ ...d, _t: toMs(d.timestamp) }))
      .sort((a, b) => (a._t ?? 0) - (b._t ?? 0))

    if (sorted.length === 0) return null

    // 时间步长与窗口长度必须来自模型自己的 checkpoint。
    // 之前这里写死 STEP=0.1、SEQ=150，其中 SEQ 还取错了字段名
    // （config.sequence_length 下划线 vs 服务端返回的 sequenceLength 驼峰），
    // 结果永远回退到 150，对任何非 150 窗口的模型都是错的。
    const shape = this.shapeOf(compartmentName)
    const STEP = shape.stepSeconds
    const SEQ = shape.seqLen
    const base = sorted[0]
    while (sorted.length < SEQ) sorted.unshift({ ...base })

    // 时间戳全部缺失时无法按时间插值，退回按等间隔均匀假设
    const anyTime = sorted.some(d => d._t != null)
    if (anyTime) {
      const t0 = sorted.find(d => d._t != null)._t
      sorted.forEach((d, i) => { if (d._t == null) d._t = t0 + i * 1000 })
    } else {
      sorted.forEach((d, i) => { d._t = i * 1000 })
    }
    const tEnd = sorted[sorted.length - 1]._t

    const field = (d, k, fallback) => {
      const v = Number(d?.[k])
      return Number.isFinite(v) ? v : fallback
    }

    // 线性插值重采样到 0.1s 步长
    const series = []
    // CO2 是模型的第三个输入通道，必须用实测值。历史里确实带 co2
    // （后端演化引擎写入），只有真的缺失时才退回保守量级，
    // 且这条退化路径要能被看出来，不能悄悄编一个数冒充实测。
    const co2Missing = field(sorted[0], 'co2', null) === null &&
      field(sorted[sorted.length - 1], 'co2', null) === null
    for (let i = 0; i < SEQ; i++) {
      const targetT = tEnd - (SEQ - 1 - i) * STEP * 1000
      // 找到 targetT 落在哪两个采样点之间
      let lo = 0
      while (lo < sorted.length - 2 && sorted[lo + 1]._t <= targetT) lo++
      const a = sorted[lo]
      const b = sorted[Math.min(lo + 1, sorted.length - 1)]
      const ta = a._t
      const tb = b._t > ta ? b._t : ta + 1000
      const u = Math.max(0, Math.min(1, (targetT - ta) / (tb - ta)))
      const mix = k => field(a, k, 0) + (field(b, k, 0) - field(a, k, 0)) * u
      series.push({
        temperature: mix('temperature'),
        smoke: mix('smoke'),
        oxygen: mix('oxygen'),
        co: mix('co'),
        co2: co2Missing ? 400 : mix('co2')
      })
    }
    if (co2Missing) {
      this.lastDegradedChannel = 'co2'
    }

    const predictionSteps = Math.ceil(forecastSeconds / STEP)
    const result = await this.predict(compartmentName, series, predictionSteps)
    if (result) {
      return {
        ...result,
        source: 'lstm',
        sourceLabel: 'LSTM 模型预测',
        horizonSeconds: forecastSeconds
      }
    }
    return null
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
    // timestamp 同样是 ISO 字符串，必须先转成毫秒数再算 dt，
    // 否则 (last - win0) 得到 NaN，变化率全变 NaN，预测会画成一条直线。
    const ms = v => {
      if (typeof v === 'number') return Number.isFinite(v) ? v : null
      if (typeof v === 'string') {
        const n2 = Number(v)
        if (Number.isFinite(n2)) return n2
        const t = Date.parse(v)
        return Number.isFinite(t) ? t : null
      }
      return null
    }
    const points = recentReadings.map(d => ({ ...d, _t: ms(d.timestamp) }))
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

  /**
   * 格式化预测结果用于图表显示
   * @param {Object} predictionResult - 预测结果
   * @param {number} interval - 采样间隔（显示每隔N个点）
   */
  formatForChart(predictionResult, interval = 10) {
    if (!predictionResult || !predictionResult.predictions) {
      return null
    }

    const { predictions, summary } = predictionResult
    
    // 按间隔采样
    const sampledData = predictions.filter((_, index) => index % interval === 0)
    
    return {
      labels: sampledData.map(p => `${p.time}s`),
      temperature: {
        data: sampledData.map(p => p.temperature),
        label: '温度预测 (℃)',
        color: '#ff6b6b'
      },
      co: {
        data: sampledData.map(p => p.co),
        label: 'CO浓度预测 (ppm)',
        color: '#4ecdc4'
      },
      smoke: {
        data: sampledData.map(p => p.smoke),
        label: '烟雾预测 (ppm)',
        color: '#95a5a6'
      },
      oxygen: {
        data: sampledData.map(p => p.oxygen),
        label: '氧气预测 (%)',
        color: '#3498db'
      },
      summary
    }
  }

  /**
   * 获取预测的关键指标
   * @param {Object} predictionResult - 预测结果
   */
  getKeyMetrics(predictionResult) {
    if (!predictionResult || !predictionResult.summary) {
      return null
    }

    const { summary, predictions } = predictionResult
    
    // 计算达到危险阈值的时间
    let dangerTime = null
    for (let i = 0; i < predictions.length; i++) {
      if (predictions[i].temperature > 300 || predictions[i].co > 35) {
        dangerTime = predictions[i].time
        break
      }
    }
    
    // 计算温度变化率
    const tempChangeRate = predictions.length > 10 
      ? (predictions[predictions.length - 1].temperature - predictions[0].temperature) / (predictions[predictions.length - 1].time || 1)
      : 0

    return {
      maxTemperature: summary.maxTemperature,
      minTemperature: summary.minTemperature,
      avgTemperature: summary.avgTemperature,
      maxCO: summary.maxCO,
      riskLevel: summary.riskLevel,
      trend: summary.trend,
      trendText: this._getTrendText(summary.trend),
      dangerTime: dangerTime,
      tempChangeRate: Math.round(tempChangeRate * 10) / 10,
      predictionDuration: summary.predictionDuration,
      riskLevelText: this._getRiskLevelText(summary.riskLevel),
      riskColor: this._getRiskColor(summary.riskLevel)
    }
  }

  _getTrendText(trend) {
    const trendMap = {
      'rising': '📈 持续上升',
      'falling': '📉 逐渐下降',
      'stable': '➡️ 基本稳定',
      'unknown': '❓ 趋势未知'
    }
    return trendMap[trend] || trend
  }

  _getRiskLevelText(level) {
    const levelMap = {
      'high': '🔴 高风险',
      'medium': '🟡 中等风险',
      'low': '🟢 低风险'
    }
    return levelMap[level] || level
  }

  _getRiskColor(level) {
    const colorMap = {
      'high': '#e74c3c',
      'medium': '#f39c12',
      'low': '#27ae60'
    }
    return colorMap[level] || '#95a5a6'
  }
}

// 导出单例
export const lstmService = new LSTMPredictionService()

// 默认导出
export default lstmService

