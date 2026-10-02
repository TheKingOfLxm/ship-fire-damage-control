/**
 * 损管指控台 —— 全局唯一状态容器
 * ═══════════════════════════════════════════════════════════════
 *
 * 二期改造要点：
 *  1. 数据源改为**后端为准**。火灾演化由后端的 LSTM 引擎负责，
 *     前端只做读取与下发；本文件里的本地演化模型仅在后端不可达时兜底，
 *     且会明确把 source 标成 'fallback'，不会伪装成真实数据。
 *  2. 全舰态势用一次 /api/fleet/status 拿齐，取代原来每个舱室各轮询一次。
 *  3. 历史改读后端 fire_data（带每分钟变化速率），取代前端自造的采样。
 *  4. 告警与态势读数合并成一条数据流：后端已按阈值自动升降级，
 *     前端不再自己派生第二套判断。
 *  5. 预测走 LSTM 服务；服务不可用时降级为变化率外推，并如实标注来源。
 */
import { computed, inject, provide, reactive, ref } from 'vue'
import { COMPARTMENTS, FIRE_TYPES, getCompartmentById } from '@/config/shipLayout'
import { alertAPI, aiAPI, fireAPI } from '@/api'
import lstmService from '@/services/lstmPredictionService'
import { streamChat } from '@/services/sseClient'
import Logger from '@/utils/logger'

const KEY = Symbol('ops-console')

/**
 * SSE 流式对话客户端。
 * 后端 /ai/chat/stream 返回 text/event-stream，这里按事件块解析：
 *   stage —— 处理阶段提示
 *   delta —— 增量文本
 *   done  —— 结束
 *   error —— 出错
 * 用 fetch + ReadableStream 而不是 EventSource，因为需要 POST 带请求体。
 */

const FLEET_POLL_MS = 2000
const FLEET_POLL_BACKOFF_MS = 10000
const HISTORY_POLL_MS = 2000
const ALERT_POLL_MS = 5000
const PREDICT_POLL_MS = 10000
const LOCAL_FALLBACK_TICK_MS = 1000
const HISTORY_MAX = 240

/** 各指标的正常/警戒/危险阈值（与 config/compartments.json 保持一致） */
export const THRESHOLDS = {
  temperature: { unit: '℃', max: 800, warn: 200, crit: 400, digits: 0 },
  smoke: { unit: '%', max: 100, warn: 20, crit: 45, digits: 1 },
  oxygen: { unit: '%', max: 25, warn: 19, crit: 16, digits: 1, inverse: true },
  co: { unit: 'ppm', max: 500, warn: 50, crit: 150, digits: 0 }
}

const METRICS = [
  { key: 'temperature', label: '温度' },
  { key: 'smoke', label: '烟雾浓度' },
  { key: 'oxygen', label: '氧气浓度' },
  { key: 'co', label: '一氧化碳' }
]

/** 各舱室燃料特性：仅在后端不可达时用于本地兜底演化 */
export const FIRE_PROFILE = {
  1: { tMax: 780, tau: 46, smoke: 0.55, co: 0.40 },
  2: { tMax: 620, tau: 30, smoke: 0.80, co: 0.22 },
  3: { tMax: 540, tau: 26, smoke: 0.60, co: 0.55 },
  4: { tMax: 560, tau: 32, smoke: 0.95, co: 0.62 },
  5: { tMax: 700, tau: 20, smoke: 0.68, co: 0.78 }
}

const clamp = (v, a, b) => Math.max(a, Math.min(b, v))
const BASELINE = { temperature: 22, smoke: 0, oxygen: 20.9, co: 0, co2: 400 }

function createOpsConsole() {
  /* ══════════════ 状态 ══════════════ */

  /** readings[compartmentId] = { temperature, smoke, oxygen, co, co2, source } */
  const readings = reactive({})
  /** fires[compartmentId] = { active, fireId, severity, suppressed, evacuated, ... } */
  const fires = reactive({})
  for (const c of COMPARTMENTS) {
    readings[c.id] = { ...BASELINE, source: 'baseline' }
    fires[c.id] = { active: false, severity: 0, suppressed: false, evacuated: false }
  }

  const history = ref([])          // 当前选中舱室的历史序列（含变化速率）
  const historyRates = ref(null)   // 最近一次的变化速率
  const alerts = ref([])
  const engineInfo = ref(null)
  const backendOnline = ref(false)
  const lastError = ref(null)
  const updatedAt = ref(0)

  const selectedId = ref(null)
  const selectedName = ref('')
  const selected = ref(null)
  const fireIntensity = ref(0)

  const lstm = reactive({
    available: null,
    running: false,
    // 预测时长（秒）。损管决策窗口以分钟计，30 分钟是常用上限。
    // 纯 LSTM 自回归撑不了这么久：会收敛到极限环，之后改用舱室火灾增长律，
    // 逐点用 basis 标出来源。
    horizon: 900,
    actualHorizon: 0,     // 模型**实际**给出的预测时长（秒）——界面显示要用这个
    predictions: [],
    analysis: null,      // 阶段 / 峰值温度 / 达峰时间 / 外推起点
    source: null,       // 'lstm' | 'extrapolation'
    sourceLabel: '',
    updatedAt: 0,
    error: null
  })

  /**
   * 模型一次前向真正覆盖多少秒。
   *
   * 之前界面直接显示 lstm.horizon（请求的 30 秒），但模型窗口只有
   * pred_len × step（例如 15 × 0.1 = 1.5 秒），于是界面报"未来 30 秒"
   * 而实际只有 1.5 秒的数据 —— 标签差了十几倍。必须按实际点数算。
   */
  function actualHorizon(count) {
    const n = Number(count) || 0
    if (!n) return 0
    const step = lstmService?.shapeOf?.(selectedId.value == null ? '' : getCompartmentById(selectedId.value)?.name)
      ?.stepSeconds || 0.1
    return Math.round(n * step * 10) / 10
  }

  const ai = reactive({
    loading: false, models: [], model: null, error: null,
    results: {}, chat: [], chatLoading: false,
    stage: '',      // 当前阶段的一句话提示
    stages: []     // 阶段列表，用于"分析过程"展示
  })

  /**
   * 阶段推进器。
   * 展示的是后端**真实经过**的处理步骤，不是编造的"深度思考"文案 ——
   * 模型本身没有返回推理链，假装有就是在骗人。
   */
  function resetStages() {
    ai.stages = []
    ai.stage = ''
  }
  function pushStage(text) {
    if (ai.stages[ai.stages.length - 1]?.text === text) return
    ai.stages.forEach(s => { s.done = true })
    ai.stages.push({ text, done: false })
    ai.stage = text
  }
  function finishStages() {
    ai.stages.forEach(s => { s.done = true })
    ai.stage = ''
  }

  /* ══════════════ 后端数据读取 ══════════════ */

  let fleetTimer = null
  let historyTimer = null
  let alertTimer = null
  let predictTimer = null
  let lstmTimer = null
  let fallbackTimer = null
  let inFlight = false

  /**
   * 后端统一返回 { success, data, message }。
   * axios 的 res.data 是整个响应体，真正的载荷在 res.data.data。
   * 少取一层拿到的是 { success, data } 这个包装对象本身 —— 下游一遍历
   * 就抛 TypeError，被 catch 吞掉后误报成"后端不可达"，很难排查。
   */
  const payload = res => res?.data?.data

  async function readFleet() {
    if (inFlight) return
    inFlight = true
    try {
      const d = payload(await fireAPI.getFleetStatus()) || {}
      for (const item of d.compartments || []) {
        readings[item.compartmentId] = {
          temperature: item.temperature,
          smoke: item.smoke,
          oxygen: item.oxygen,
          co: item.co,
          source: item.fireActive ? 'lstm' : 'baseline'
        }
        const prev = fires[item.compartmentId] || {}
        fires[item.compartmentId] = {
          ...prev,
          active: item.fireActive,
          severity: item.severity ?? 0,
          temperatureModelDriven: item.temperatureModelDriven ?? true
        }
      }
      backendOnline.value = true
      lastError.value = null
      updatedAt.value = Date.now()
      stopFallback()
    } catch (e) {
      backendOnline.value = false
      lastError.value = e?.response?.data?.message || e?.message || '后端不可达'
      startFallback()
    } finally {
      inFlight = false
      // 后端掉线时降频轮询，避免刷屏；恢复后自动回到 2s
      scheduleFleet(backendOnline.value ? FLEET_POLL_MS : FLEET_POLL_BACKOFF_MS)
    }
  }

  function scheduleFleet(ms) {
    clearInterval(fleetTimer)
    fleetTimer = setInterval(readFleet, ms)
  }

  async function readEngineInfo() {
    try {
      engineInfo.value = payload(await fireAPI.getEngineInfo()) || null
    } catch {
      engineInfo.value = null
    }
  }

  async function readHistory() {
    const id = selectedId.value
    if (id == null) {
      history.value = []
      historyRates.value = null
      return
    }
    try {
      const series = payload(await fireAPI.getFireHistory(id, { limit: HISTORY_MAX }))?.series || []
      history.value = series
      const last = series[series.length - 1]
      historyRates.value = last?.rates || null
    } catch {
      /* 历史读取失败不阻塞主流程 */
    }
  }

  async function readAlerts() {
    try {
      const res = await alertAPI.getActiveAlerts()
      const list = payload(res)
      alerts.value = (Array.isArray(list) ? list : []).map(a => ({
        id: a.id,
        compartmentId: a.compartmentId,
        level: a.level,
        type: a.type,
        title: a.title,
        message: a.message,
        status: a.status,
        acknowledged: a.status === 'acknowledged' || !!a.acknowledgedAt,
        time: a.createdAt || Date.now()
      }))
    } catch {
      /* 告警读取失败时保持上一次的列表，不清空 */
    }
  }

  /* ══════════════ 预测 ══════════════ */

  async function runPrediction() {
    const id = selectedId.value
    if (id == null || lstm.running) return
    const spec = getCompartmentById(id)
    const series = history.value
    if (series.length < 10) {
      lstm.error = `历史样本不足（${series.length}/10）`
      return
    }
    lstm.running = true
    try {
      const ok = await lstmService.checkHealth()
      lstm.available = ok
      if (ok) {
        // 必须先取配置：预测时的重采样步长与窗口长度来自模型 checkpoint，
        // 没有配置就只能用兜底值，窗口一旦对不上预测就是错的。
        await lstmService.getConfig()
        // 走长时程投影：LSTM 段到极限环为止，之后用舱室火灾增长律外推，
        // 逐点标 basis。纯 LSTM 自回归 30 分钟会收敛成假曲线。
        const res = await lstmService.project(spec.name, series, lstm.horizon)
        if (res && res.points?.length) {
          lstm.predictions = res.points
          lstm.analysis = res.analysis || null
          lstm.source = 'lstm'
          lstm.sourceLabel = res.analysis?.basis || 'LSTM + 增长律投影'
          lstm.updatedAt = Date.now()
          lstm.error = null
          lstm.running = false
          lstm.actualHorizon = res.analysis?.totalSeconds || actualHorizon(res.points.length)
          return
        }
      } else {
        lstm.error = 'LSTM 预测服务不可用'
      }
    } catch (e) {
      lstm.error = e?.message || '预测失败'
    }

    // 降级：用变化率外推，如实标注不是模型预测
    try {
      const fb = lstmService.extrapolate(
        spec.name, series, lstm.horizon, FIRE_PROFILE[id] || null
      )
      if (fb) {
        lstm.predictions = fb.predictions
        lstm.source = fb.source
        lstm.sourceLabel = fb.sourceLabel
        lstm.updatedAt = Date.now()
        lstm.actualHorizon = actualHorizon(fb.predictions.length)
      }
    } catch {
      /* 兜底也失败就保持上一次结果 */
    } finally {
      lstm.running = false
    }
  }

  /* ══════════════ 本地兜底演化（仅后端不可达时） ══════════════ */

  const localFires = reactive({})
  let lastTick = 0

  function startFallback() {
    if (fallbackTimer) return
    lastTick = 0
    fallbackTimer = setInterval(stepFallback, LOCAL_FALLBACK_TICK_MS)
    Logger.warn('后端不可达，切换到本地兜底演化（数据来源已标记为 fallback）')
  }

  function stopFallback() {
    clearInterval(fallbackTimer)
    fallbackTimer = null
  }

  function stepFallback() {
    const now = performance.now()
    const dt = lastTick ? Math.min(2, (now - lastTick) / 1000) : 0
    lastTick = now
    if (dt <= 0) return

    for (const c of COMPARTMENTS) {
      const s = localFires[c.id]
      if (!s?.active) continue
      const p = FIRE_PROFILE[c.id]
      s.t += dt
      const g = 1 - Math.exp(-s.t / p.tau)
      const cur = readings[c.id]
      const k = 1 - Math.exp(-dt / 2.5)
      const target = clamp(20 + (p.tMax - 20) * g, 20, p.tMax)
      readings[c.id] = {
        temperature: +(cur.temperature + (target - cur.temperature) * k).toFixed(1),
        smoke: +clamp(cur.smoke + (p.smoke * 100 * g - cur.smoke) * k, 0, 100).toFixed(1),
        oxygen: +clamp(cur.oxygen - 0.42 * g * dt, 8, 20.9).toFixed(1),
        co: +Math.max(0, cur.co + (p.co * 500 * g - cur.co) * k).toFixed(0),
        source: 'fallback'
      }
      fires[c.id] = { ...fires[c.id], active: true, severity: clamp(g, 0, 1) }
      history.value.push({
        timestamp: Date.now(),
        ...readings[c.id],
        rates: {
          temperature: +((target - cur.temperature) * k * 60 / Math.max(dt, 0.1)).toFixed(1),
          smoke: +(p.smoke * 100 * g * 60).toFixed(1),
          oxygen: +(-0.42 * g * 60).toFixed(1),
          co: +(p.co * 500 * g * 60).toFixed(0)
        }
      })
      if (history.value.length > HISTORY_MAX) history.value.shift()
    }
    updatedAt.value = Date.now()
  }

  function localIgnite(id) {
    localFires[id] = { active: true, t: 0 }
    fires[id] = { ...fires[id], active: true, severity: 0 }
  }

  function localExtinguish(id) {
    if (localFires[id]) localFires[id].active = false
    fires[id] = { ...fires[id], active: false, severity: 0 }
  }

  /* ══════════════ 指令 ══════════════ */

  /**
   * 下发指令。优先走后端；后端不可达时明确降级为本地兜底，
   * 并在返回值里告诉调用方实际发生了什么。
   * @returns {{ok:boolean, via:'backend'|'fallback', message:string}}
   */
  async function command(compartmentId, action) {
    if (compartmentId == null) {
      return { ok: false, via: 'backend', message: '请先选择舱室' }
    }
    try {
      const res = await fireAPI.controlFire(compartmentId, action, { intensity: 0.6, spreadRate: 0.1 })
      const data = payload(res) || {}
      if (data.fire) {
        fires[compartmentId] = { ...fires[compartmentId], ...data.fire }
        fireIntensity.value = data.fire.active ? (data.fire.severity ?? 0) : 0
      }
      readFleet()
      readAlerts()
      return { ok: true, via: 'backend', message: data.message || res.data?.message || '指令已执行' }
    } catch (e) {
      // 后端不可达：本地兜底，让操作仍然有可见结果
      const active = fires[compartmentId]?.active
      if (action === 'start' && !active) {
        localIgnite(compartmentId)
        return { ok: true, via: 'fallback', message: '后端不可达，已启用本地兜底模拟' }
      }
      if (action === 'stop' || action === 'reset') {
        localExtinguish(compartmentId)
        return { ok: true, via: 'fallback', message: '后端不可达，已停止本地模拟' }
      }
      return {
        ok: false, via: 'fallback',
        message: e?.response?.data?.message || e?.message || '指令下发失败'
      }
    }
  }

  /* ══════════════ 告警流转 ══════════════ */

  async function ackAlert(id) {
    const a = alerts.value.find(x => x.id === id)
    if (a) a.acknowledged = true
    try { await alertAPI.acknowledgeAlert(id) } catch { /* 本地已标记 */ }
    readAlerts()
  }

  async function resolveAlert(id) {
    alerts.value = alerts.value.filter(x => x.id !== id)
    try { await alertAPI.resolveAlert(id) } catch { /* 本地已消解 */ }
    readAlerts()
  }

  /* ══════════════ AI ══════════════ */

  async function fetchModels() {
    try {
      const res = await aiAPI.models()
      const list = payload(res)
      ai.models = Array.isArray(list) ? list : []
      ai.model = ai.models[0]?.id || ai.models[0] || null
    } catch {
      ai.models = []
      ai.error = '大模型服务未连接'
    }
  }

  async function analyze() {
    const id = selectedId.value
    if (id == null || ai.loading) return
    ai.loading = true
    ai.error = null
    resetStages()
    try {
      const spec = getCompartmentById(id)
      const r = readings[id] || BASELINE
      pushStage('汇集传感器读数')
      // 后端 analyzeFire 读的是平铺的 compartmentName/temperature/smoke/oxygen/co，
      // 之前这里发的是 {compartment, data:{...}}，后端收到全是 undefined，
      // 提示词变成"温度：undefined°C"，分析结果毫无意义。
      const res = await aiAPI.analyze({
        compartmentId: id,
        compartmentName: spec.name,
        fireType: spec.name,
        temperature: r.temperature,
        smoke: r.smoke,
        oxygen: r.oxygen,
        co: r.co,
        provider: ai.model
      })
      pushStage('评估风险等级与火灾阶段')
      const result = payload(res) || null
      if (result) {
        pushStage('生成处置建议')
        ai.results[id] = result
      }
      finishStages()
    } catch (e) {
      ai.error = e?.response?.data?.message || e?.message || '分析服务不可用'
      finishStages()
    } finally {
      ai.loading = false
    }
  }

  /**
   * 剥掉模型偶尔夹带的 JSON / 代码块。
   * 系统提示已经要求用自然语言，但仍可能夹带 —— 值班界面里甩一段
   * 原始 JSON 对人毫无意义。宁可少显示，也不要让结构化数据糊在对话流里。
   */
  function stripJson(text) {
    if (!text) return ''
    let s = String(text)
    // 去掉 ```json ... ``` 代码块
    s = s.replace(/```(?:json)?\s*[\s\S]*?```/gi, '')
    // 去掉裸露的 JSON 对象（平衡括号匹配）
    let out = ''
    let depth = 0
    let start = -1
    for (let i = 0; i < s.length; i++) {
      const c = s[i]
      if (c === '{') {
        if (depth === 0) start = i
        depth++
      } else if (c === '}') {
        depth--
        if (depth === 0 && start >= 0) {
          // 仅当内容像 JSON（含引号键）才删，避免误伤正常文字
          const chunk = s.slice(start, i + 1)
          if (/"\s*\w+\s*"\s*:/.test(chunk)) {
            out += s.slice(0, start)
            s = s.slice(i + 1)
            i = -1
            start = -1
            depth = 0
            continue
          }
        }
        if (depth < 0) depth = 0
      }
    }
    out += s
    return out.replace(/\n{3,}/g, '\n\n').trim()
  }

  async function sendChat(text) {
    const content = String(text || '').trim()
    if (!content || ai.chatLoading) return
    const id = selectedId.value
    ai.chat.push({ role: 'user', content, ts: Date.now() })
    ai.chatLoading = true
    resetStages()
    try {
      const spec = getCompartmentById(id)
      const r = readings[id] || BASELINE
      // 后端 chat 要的是 OpenAI 风格的 messages 数组，
      // 之前只发了 {message}，后端校验直接 400。
      const history = ai.chat
        .filter(m => m.role !== 'error' && m.content)
        .map(m => ({ role: m.role, content: m.content }))
      history.push({ role: 'user', content })

      const body = {
        model: ai.model || 'glm',
        messages: history,
        context: {
          compartmentName: spec?.name,
          temperature: r.temperature,
          smoke: r.smoke,
          oxygen: r.oxygen,
          co: r.co
        }
      }

      // 先建一个空的 assistant 气泡，流式增量直接往里追加
      const bubble = reactive({ role: 'assistant', content: '', ts: Date.now(), streaming: true })
      ai.chat.push(bubble)

      let got = false
      const onEvent = (type, data) => {
        if (type === 'stage') {
          pushStage(data.text)
        } else if (type === 'delta') {
          got = true
          bubble.content += stripJson(data.text || '')
        } else if (type === 'done') {
          bubble.provider = data.provider
        } else if (type === 'error') {
          throw new Error(data.message || '流式对话失败')
        }
      }

      await streamChat(body, onEvent)

      if (!got) {
        // 流式没拿到内容（可能被代理缓冲/降级），退回一次性请求
        bubble.content = ''
        const res = await aiAPI.chat(body)
        const d = payload(res) || {}
        bubble.content = stripJson(d.reply || d.content || '（无回复）')
        bubble.provider = d.provider
      }
      bubble.streaming = false
      finishStages()
    } catch (e) {
      const msg = e?.response?.data?.message || e?.message || '对话服务不可用'
      // 失败如实显示在对话流里，不能静默吞掉
      ai.chat.push({ role: 'error', content: msg, ts: Date.now() })
      ai.error = msg
      finishStages()
    } finally {
      ai.chatLoading = false
    }
  }

  /* ══════════════ 派生量 ══════════════ */

  function fmt(v, digits = 1) {
    const n = Number(v)
    return Number.isFinite(n) ? n.toFixed(digits) : '--'
  }

  function severityOf(key, value) {
    const t = THRESHOLDS[key]
    if (!t || !Number.isFinite(value)) return 'ok'
    if (t.inverse) {
      if (value <= t.crit) return 'crit'
      if (value <= t.warn) return 'warn'
      return 'ok'
    }
    if (value >= t.crit) return 'crit'
    if (value >= t.warn) return 'warn'
    return 'ok'
  }

  const activeFires = computed(() =>
    COMPARTMENTS.filter(c => fires[c.id]?.active).map(c => ({
      ...c, ...fires[c.id], reading: readings[c.id]
    }))
  )

  const activeAlerts = computed(() => alerts.value.filter(a => a.status !== 'resolved'))

  const unreadAlerts = computed(() => activeAlerts.value.filter(a => !a.acknowledged).length)

  const worstRisk = computed(() => {
    const order = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }
    let worst = 'info'
    for (const c of COMPARTMENTS) {
      const f = fires[c.id]
      if (!f?.active) continue
      const r = readings[c.id]
      const lv = ['temperature', 'smoke', 'oxygen', 'co']
        .map(m => severityOf(m, r[m]))
        .reduce((a, b) => (b === 'crit' ? b : a), 'ok')
      const mapped = lv === 'crit' ? 'critical' : lv === 'warn' ? 'medium' : 'info'
      if (order[mapped] > order[worst]) worst = mapped
    }
    return worst
  })

  /* ══════════════ 生命周期 ══════════════ */

  const ctx = {
    // 状态
    readings, fires, history, historyRates, alerts, activeAlerts, unreadAlerts,
    engineInfo, backendOnline, lastError, updatedAt, lstm, ai, activeFires,
    selectedId, selectedName, selected, fireIntensity, worstRisk,
    THRESHOLDS, METRICS, FIRE_PROFILE, FIRE_TYPES, BASELINE,
    // 行为
    command, runPrediction, ackAlert, resolveAlert,
    analyze, sendChat, clearChat: () => { ai.chat = [] }, fetchModels,
    readFleet, readAlerts, readHistory, readEngineInfo,
    fmt, severityOf,

    start() {
      readFleet()
      scheduleFleet(FLEET_POLL_MS)
      historyTimer = setInterval(readHistory, HISTORY_POLL_MS)
      alertTimer = setInterval(readAlerts, ALERT_POLL_MS)
      predictTimer = setInterval(() => { if (fires[selectedId.value]?.active) runPrediction() }, PREDICT_POLL_MS)
      lstmTimer = setInterval(() => lstmService.checkHealth(true), 30000)
      readAlerts()
      readEngineInfo()
      readHistory()
      lstmService.checkHealth(true).then(ok => { lstm.available = ok })
      fetchModels()
    },

    stop() {
      clearInterval(fleetTimer); fleetTimer = null
      clearInterval(historyTimer); historyTimer = null
      clearInterval(alertTimer); alertTimer = null
      clearInterval(predictTimer); predictTimer = null
      clearInterval(lstmTimer); lstmTimer = null
      stopFallback()
    },

    async selectCompartment(id) {
      if (id == null) {
        selectedId.value = null
        selectedName.value = ''
        selected.value = null
        history.value = []
        historyRates.value = null
        lstm.predictions = []
        return
      }
      const c = getCompartmentById(id)
      selectedId.value = id
      selectedName.value = c?.name || ''
      selected.value = c || null
      lstm.predictions = []
      lstm.error = null
      await readHistory()
      if (fires[id]?.active) runPrediction()
    }
  }

  return ctx
}

export function provideOpsConsole() {
  const ctx = createOpsConsole()
  provide(KEY, ctx)
  return ctx
}

export function useOpsConsole() {
  const ctx = inject(KEY, null)
  if (!ctx) throw new Error('useOpsConsole 必须在 provideOpsConsole() 的子树内使用')
  return ctx
}
