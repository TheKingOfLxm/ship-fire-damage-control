/**
 * 服务端舱室火灾演化引擎（LSTM 驱动）
 * ═══════════════════════════════════════════════════════════════
 *
 * 演化完全由 lstm-prediction-server 的 Seq2Seq LSTM 驱动：
 * 每一步都把模型自己刚吐出的结果接回 150 步输入窗口再推下一步
 * （自回归闭环），因此误差被约束在窗口内而不是一次性发散。
 *
 * 服务端只做两件事，都不是"另建一套物理"：
 *   1. 物理量程钳制（氧气不会低于 8%、温度不会超过 1500℃ 等）
 *   2. 越限时自动升/降级告警
 *
 * 必须向使用者披露的两个模型限制（实测自 D:\PyrosimLSTM 的训练数据）：
 *   · 所有训练轨迹只有 **30 秒**。模型的有效时域就是 30 秒，
 *     之后属于分布外外推，数值不再可信。
 *   · **机库模型的温度通道是退化的** —— 训练数据里温度全程恒为 20℃，
 *     只有 CO/CO2 在上升。该舱室的温度不来自模型。
 */

import axios from 'axios'
import { Op } from 'sequelize'
import db from '../models/index.js'
import { getCompartment, ALL_COMPARTMENTS, THRESHOLDS } from './layout.js'
import logger from '../utils/logger.js'

const { FireEvent, FireData, Alert, Compartment } = db

const TICK_MS = 1000                 // 演化步长（墙钟）
const STEPS_PER_TICK = 1             // 每 tick 推进的模型步数
const HISTORY_RETENTION = 2000       // 每个舱室保留的历史点数
const COOLING_TICKS = 25             // 灭火后回到基线所需 tick 数
const PRUNE_EVERY_MS = 60000

const LSTM_BASE = process.env.LSTM_SERVICE_URL || 'http://localhost:5001'
// 模型有效时域与步长在启动后从 LSTM 服务的 checkpoint 读取，见 lstmTiming。

/**
 * 训练数据里温度通道退化的舱室。
 *
 * ⚠ 不要再硬编码舱名。之前写死 `['机库']`，但用 30 分钟 FDS 数据重训后
 * 机库温度已经有 17998 个唯一值、峰值 570℃ —— 硬编码会让界面继续谎称
 * "温度不由模型给出"，把已经修好的数据白白丢掉。
 * 现在改为启动后从 LSTM 服务的 checkpoint 读 `degenerateChannels`，
 * 由数据自己说明哪些通道不可信。
 */
let noTemperatureSignal = new Set()

const clamp = (v, a, b) => Math.max(a, Math.min(b, v))

/** compartmentId -> 运行时状态 */
const runtime = new Map()

function stateOf(id) {
  if (!runtime.has(id)) {
    runtime.set(id, {
      fire: null,        // { id, status, suppressed, evacuated, steps }
      window: null,      // [[T, co, co2], ...] 150 步模型窗口
      last: null,        // 最近一次读数（对外展示）
      coolTicks: 0
    })
  }
  return runtime.get(id)
}

/* ------------------------------------------------------------------ */
/* 与 LSTM 服务通信                                                    */
/* ------------------------------------------------------------------ */

let lstmAvailable = null
let lstmCheckedAt = 0

/**
 * 模型一步对应多少秒，以及训练时域多长。
 *
 * 这两个值必须来自 LSTM 服务的 checkpoint —— 训练时所有轨迹已统一重采样到
 * 0.1s 均匀网格，早期版本在这里硬编码 0.1411（原始 CSV 的 Δt 最大值），
 * 导致对外披露的"已推进秒数"比实际推进的快 3.7 倍。
 */
const lstmTiming = { stepSeconds: 0.1, trainedHorizonSeconds: 30, seqLen: 0, predLen: 0, loaded: false }

async function lstmTimingInfo() {
  if (lstmTiming.loaded) return lstmTiming
  try {
    const r = await axios.get(`${LSTM_BASE}/api/lstm/config`, { timeout: 4000 })
    const d = r.data?.data || {}
    if (Number.isFinite(d.timeStepSeconds) && d.timeStepSeconds > 0) {
      lstmTiming.stepSeconds = d.timeStepSeconds
    }
    if (Number.isFinite(d.trainedHorizonSeconds) && d.trainedHorizonSeconds > 0) {
      lstmTiming.trainedHorizonSeconds = d.trainedHorizonSeconds
    }
    const shapes = d.modelShapes || {}
    for (const s of Object.values(shapes)) {
      if (Number.isFinite(s?.seqLen) && s.seqLen > 0) { lstmTiming.seqLen = s.seqLen; break }
    }
    for (const s of Object.values(shapes)) {
      if (Number.isFinite(s?.predLen) && s.predLen > 0) { lstmTiming.predLen = s.predLen; break }
    }
    // 退化通道由 checkpoint 自带，不在前端/后端硬编码舱名
    const dead = new Set()
    for (const s of Object.values(shapes)) {
      for (const c of s?.degenerateChannels || []) {
        if (String(c).toLowerCase().includes('temperature')) dead.add('temperature')
      }
    }
    if (dead.size) noTemperatureSignal = dead
    lstmTiming.loaded = true
  } catch {
    // 服务不可达时保持默认值，不阻塞演化
  }
  return lstmTiming
}

async function lstmReady() {
  if (lstmAvailable != null && Date.now() - lstmCheckedAt < 20000) {
    return lstmAvailable
  }
  try {
    const r = await axios.get(`${LSTM_BASE}/api/lstm/health`, { timeout: 4000 })
    lstmAvailable = r.data?.status === 'ok'
  } catch {
    lstmAvailable = false
  }
  lstmCheckedAt = Date.now()
  if (lstmAvailable) await lstmTimingInfo()
  return lstmAvailable
}

/**
 * 推进一步：把当前窗口送给 LSTM，取回新步与更新后的窗口。
 * @returns {Promise<{rows: number[][], window: number[][], diagnostics: object}|null>}
 */
async function lstmStep(spec, st, steps = STEPS_PER_TICK) {
  const payload = {
    compartmentName: spec.name,
    steps,
    base: {
      temperature: spec.base.temperature,
      co: spec.base.co,
      co2: 400
    }
  }
  // 本 tick 实际推进了多少**模型步**。
  //
  // 不能直接用 steps：steps 是"这次向前推几步"，而游标要按**时间**前进。
  // v6 模型的采样间隔是 0.5 秒，墙钟每 tick 过 1 秒，其实等于 2 个模型步。
  // 早先固定传 1，灶炉间的火灾只以半速发展 —— 实测跑了 2 分钟，火势才
  // 走到真实轨迹 t≈6 秒的位置，温度在 55~80℃ 之间来回抖，
  // 看起来就是"温度不升反降"。
  const dt = lstmTiming.stepSeconds > 0 ? lstmTiming.stepSeconds : 0.1
  // 按**实际经过的墙钟时间**推进，而不是标称的 TICK_MS。
  //
  // 机器一忙（别的进程抢 CPU、torch 线程争用），一轮 tick 就会拖到好几秒。
  // 固定按 TICK_MS 算的话，火灾只按 1/5 的速度发展 —— 实测灶炉间点了火
  // 两分钟还停在 60~90℃ 抖，而 FDS 真值这时已经 113℃ 了。损管看的是
  // "火灾过了多久"，不是"引擎转了几拍"，所以时间轴必须跟墙钟走。
  const now = Date.now()
  const elapsed = st.lastTickAt ? (now - st.lastTickAt) / 1000 : TICK_MS / 1000
  st.lastTickAt = now
  // 一次别跳太多：模型一次前向只准 20 步（服务端会按 pred_len 截断），
  // 超出的部分丢掉，代价只是下一轮继续追。
  const advance = Math.max(1, Math.min(
    20, Math.round(elapsed / dt)))
  // steps 必须和 advance 一致。只生成 1 步却把游标推 2 步，采样点就落在
  // 两个模型步之间，读数会带 ±10℃ 的锯齿。
  payload.steps = advance
  payload.advanceSteps = advance
  if (st.window) payload.window = st.window

  const r = await axios.post(`${LSTM_BASE}/api/lstm/evolve`, payload, { timeout: 15000 })
  if (r.data?.code !== 200 || !r.data?.data) throw new Error(r.data?.message || '演化失败')
  return r.data.data
}

/** 模型输出 [温度℃, CO(mol/mol), CO2(mol/mol)] -> 面板读数 */
function decode(spec, row) {
  const temp = clamp(row[0], 0, 1500)
  const co = clamp(row[1] * 1e6, 0, 50000)
  const co2 = clamp(row[2] * 1e6, 380, 200000)
  // 氧气由 CO2 与烟气产率的相对变化推算：模型只给三个量纲，
  // 这里用 CO2 上升比例作为耗氧的代理指标，量纲与趋势一致。
  const o2 = clamp(20.9 - (co2 - 400) / 20000 * 9.5, 8, 20.9)
  // 烟与温度正相关（火焰发展伴随产烟）；温度通道退化的舱改用 CO2 作代理
  const noTemp = noTemperatureSignal.has(spec.name)
  const smokeSource = noTemp ? (co2 - 400) / 400 : (temp - 20) / 400
  const smoke = clamp(smokeSource * 100, 0, 100)
  return {
    temperature: Number(temp.toFixed(1)),
    smoke: Number(smoke.toFixed(1)),
    oxygen: Number(o2.toFixed(1)),
    co: Number(co.toFixed(0)),
    co2: Math.round(co2)
  }
}

/* ------------------------------------------------------------------ */
/* 告警联动                                                            */
/* ------------------------------------------------------------------ */

const ORDER = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }
const activeAlerts = new Map()   // `${compartmentId}:${metric}` -> Alert 实例

const METRIC_TYPE = {
  temperature: 'temperature', smoke: 'smoke', oxygen: 'gas', co: 'gas'
}
const METRIC_TITLE = {
  temperature: '温度告警', smoke: '烟雾告警', oxygen: '缺氧告警', co: '有毒气体告警'
}

function levelFor(metric, value) {
  const t = THRESHOLDS()[metric]
  if (!t) return 'info'
  if (metric === 'oxygen') {
    if (value <= t.crit) return 'critical'
    if (value <= t.warn) return 'medium'
    return 'info'
  }
  if (value >= t.crit) return 'critical'
  if (value >= t.warn) return 'medium'
  return 'info'
}

function describe(metric, value, spec) {
  const u = THRESHOLDS()[metric].unit
  const v = metric === 'oxygen' || metric === 'smoke' ? value.toFixed(1) : value.toFixed(0)
  const n = { temperature: '温度', smoke: '烟雾浓度', oxygen: '氧气浓度', co: '一氧化碳' }[metric]
  return `${spec.name}${n} ${v}${u}`
}

async function syncAlerts(spec, reading) {
  const now = new Date()
  for (const metric of ['temperature', 'smoke', 'oxygen', 'co']) {
    const level = levelFor(metric, reading[metric])
    const key = `${spec.id}:${metric}`
    const cur = activeAlerts.get(key)

    if (level !== 'info') {
      if (cur) {
        if (ORDER[level] > ORDER[cur.level]) {
          await cur.update({ level, message: describe(metric, reading[metric], spec) })
        }
      } else {
        const a = await Alert.create({
          compartmentId: spec.id,
          level,
          type: METRIC_TYPE[metric],
          title: `${spec.name}${METRIC_TITLE[metric]}`,
          message: describe(metric, reading[metric], spec),
          status: 'active',
          metadata: { metric, value: reading[metric], code: spec.code }
        })
        activeAlerts.set(key, a)
        logger.info('告警触发', { compartmentId: spec.id, metric, level })
      }
    } else if (cur && cur.status === 'active') {
      await cur.update({ status: 'resolved', resolvedAt: now })
      activeAlerts.delete(key)
      logger.info('告警自动消解', { compartmentId: spec.id, metric })
    }
  }
}

/* ------------------------------------------------------------------ */
/* 对外操作                                                            */
/* ------------------------------------------------------------------ */

export async function ignite(compartmentId, { intensity = 0.6, spreadRate = 0.1 } = {}) {
  const spec = getCompartment(compartmentId)
  if (!spec) throw Object.assign(new Error('舱室不存在'), { status: 404 })
  const st = stateOf(spec.id)
  if (st.fire && st.fire.status === 'active') return { fire: st.fire, created: false }

  const record = await FireEvent.create({
    compartmentId: spec.id,
    // fire_events.type 存的是火灾**起因**（ENUM），不是舱室类型。
    // 舱室类型如 engine_room 装不进 ENUM，必须走 config 里的 fireCause 映射。
    type: spec.fireCause || 'unknown',
    status: 'active',
    intensity, spreadRate, startTime: new Date()
  })

  st.fire = { id: record.id, status: 'active', steps: 0, suppressed: false, evacuated: false }
  st.window = null       // 让 LSTM 按舱室基值重新播种
  st.last = null
  st.lastTickAt = 0      // 清计时基准：点火是第一拍，不能拿停火前的时间差来推火势
  st.coolTicks = 0
  await Compartment.update({ status: 'danger' }, { where: { id: spec.id } })
  logger.info('火灾已点燃', { compartmentId: spec.id, fireId: record.id })
  return { fire: st.fire, created: true }
}

export async function suppress(compartmentId) {
  const spec = getCompartment(compartmentId)
  const st = spec && stateOf(spec.id)
  if (!st?.fire || st.fire.status !== 'active') {
    throw Object.assign(new Error('该舱室没有活跃火灾'), { status: 404 })
  }
  st.fire.suppressed = true
  logger.info('灭火系统启动', { compartmentId: spec.id })
  return st.fire
}

export async function evacuate(compartmentId) {
  const spec = getCompartment(compartmentId)
  const st = spec && stateOf(spec.id)
  if (!st?.fire || st.fire.status !== 'active') {
    throw Object.assign(new Error('该舱室没有活跃火灾'), { status: 404 })
  }
  st.fire.evacuated = true
  logger.info('人员疏散指令已执行', { compartmentId: spec.id })
  return st.fire
}

export async function extinguish(compartmentId, { method = 'manual' } = {}) {
  const spec = getCompartment(compartmentId)
  const st = spec && stateOf(spec.id)
  if (!st?.fire || st.fire.status !== 'active') {
    throw Object.assign(new Error('该舱室没有活跃火灾'), { status: 404 })
  }
  st.fire.status = 'extinguished'
  st.coolTicks = COOLING_TICKS
  const rec = await FireEvent.findByPk(st.fire.id)
  if (rec) {
    await rec.update({
      status: 'extinguished', endTime: new Date(),
      suppressedAt: new Date(), suppressedMethod: method
    })
  }
  await Compartment.update({ status: 'normal' }, { where: { id: spec.id } })
  await Alert.update(
    { status: 'resolved', resolvedAt: new Date() },
    { where: { compartmentId: spec.id, status: { [Op.in]: ['active', 'acknowledged'] } } }
  )
  logger.info('火灾已终止', { compartmentId: spec.id, method })
  return st.fire
}

export async function resetCompartment(compartmentId) {
  const spec = getCompartment(compartmentId)
  if (!spec) throw Object.assign(new Error('舱室不存在'), { status: 404 })
  const st = stateOf(spec.id)
  if (st.fire?.status === 'active') {
    st.fire.status = 'extinguished'
    const rec = await FireEvent.findByPk(st.fire.id)
    if (rec) {
      await rec.update({
        status: 'extinguished', endTime: new Date(),
        suppressedAt: new Date(), suppressedMethod: 'manual'
      })
    }
  }
  st.fire = null
  st.window = null
  st.last = null
  st.lastTickAt = 0
  st.coolTicks = 0
  await Compartment.update({ status: 'normal' }, { where: { id: spec.id } })
  await Alert.update(
    { status: 'resolved', resolvedAt: new Date() },
    { where: { compartmentId: spec.id, status: 'active' } }
  )
  logger.info('舱室数据已重置', { compartmentId: spec.id })
  return true
}

export function currentReading(compartmentId) {
  const spec = getCompartment(compartmentId)
  if (!spec) return null
  const st = stateOf(spec.id)
  return st.last ? { ...st.last } : { ...spec.base, co2: 400 }
}

export function fireStatus(compartmentId) {
  const spec = getCompartment(compartmentId)
  if (!spec) return null
  const st = stateOf(spec.id)
  const f = st.fire
  const active = f?.status === 'active'
  return {
    active,
    fireId: f?.id ?? null,
    suppressed: !!f?.suppressed,
    evacuated: !!f?.evacuated,
    // 已推进的模型步数 / 训练时域，用于前端披露外推时长。
    // 步长取自 LSTM checkpoint，不再用硬编码的 0.1411。
    modelSteps: f?.steps ?? 0,
    trainedHorizonSeconds: lstmTiming.trainedHorizonSeconds,
    rolloutSeconds: Math.round((f?.steps ?? 0) * lstmTiming.stepSeconds),
    // 供三维火焰缩放
    severity: active && st.last
      ? clamp((st.last.temperature - 20) / 400, 0, 1)
      : 0,
    temperatureModelDriven: !noTemperatureSignal.has(spec.name)
  }
}

/** 模型可用性与限制说明，供前端如实展示 */
export function engineInfo() {
  return {
    engine: 'lstm',
    serviceUrl: LSTM_BASE,
    available: lstmAvailable,
    trainedHorizonSeconds: lstmTiming.trainedHorizonSeconds,
    stepSeconds: lstmTiming.stepSeconds,
    // 单次预测能覆盖多少秒。这和 trainedHorizonSeconds 不是一回事：
    // 前者是模型一次前向的跨度，后者是训练轨迹的总长度。
    predictionWindowSeconds: lstmTiming.seqLen
      ? Math.round(lstmTiming.predLen * lstmTiming.stepSeconds * 10) / 10
      : null,
    stepsPerTick: STEPS_PER_TICK,
    limitations: {
      noTemperatureSignal: [...noTemperatureSignal],
      note: noTemperatureSignal.size
        ? `${[...noTemperatureSignal].join('、')} 的温度通道在训练数据中恒定，温度不由模型给出`
        : '全部舱室的温度通道均有有效信号'
    }
  }
}

/* ------------------------------------------------------------------ */
/* 主循环                                                              */
/* ------------------------------------------------------------------ */

let timer = null
let running = false
let lastPrune = 0
// 重入保护：setInterval 不会等上一轮 await 结束。某个舱室的 /evolve
// 一旦慢过 tick 间隔（再锚定要重读轨迹、或服务端正忙），下一轮就会在
// 上一轮还没跑完时进来，请求越堆越多，最后整条链路堵死 ——
// 实测 5 个舱同时起火时，每个请求都撞上 15 秒超时，示数直接冻结不动。
// 这里保证任何时刻只有一轮在跑，慢了就跳过这一拍。
let ticking = false

export function startEngine() {
  if (running) return
  running = true

  timer = setInterval(async () => {
    if (ticking) return
    ticking = true
    try {
      const ready = await lstmReady()
      await restoreActiveFires()

    let dirty = false
    for (const spec of ALL_COMPARTMENTS()) {
      const st = stateOf(spec.id)
      const active = st.fire?.status === 'active'

      if (active && ready) {
        try {
          const out = await lstmStep(spec, st)
          st.window = out.window
          st.fire.steps = (st.fire.steps || 0) + out.diagnostics.rolloutSteps
          const row = out.steps[out.steps.length - 1]
          const reading = decode(spec, row)
          // 灭火系统压制：把模型输出向舱室基值回落
          if (st.fire.suppressed) {
            const b = spec.base
            const k = 0.35
            reading.temperature = reading.temperature * (1 - k) + b.temperature * k
            reading.smoke *= (1 - k)
            reading.co *= (1 - k)
          }
          st.last = reading
          await persist(spec, st, reading)
          await syncAlerts(spec, reading)
          dirty = true
        } catch (e) {
          logger.error('LSTM 演化失败', { compartmentId: spec.id, message: e.message })
        }
        continue
      }

      if (active && !ready) {
        logger.warn('LSTM 服务不可达，火灾演化暂停', { compartmentId: spec.id })
        continue
      }

      // 无活跃火灾：通风散热回基线
      if (st.coolTicks > 0 || st.last) {
        st.coolTicks = Math.max(0, (st.coolTicks || 0) - 1)
        if (st.last) {
          const b = spec.base
          const k = 0.14
          st.last = {
            temperature: st.last.temperature * (1 - k) + b.temperature * k,
            smoke: st.last.smoke * (1 - k),
            oxygen: st.last.oxygen * (1 - k) + 20.9 * k,
            co: st.last.co * (1 - k),
            co2: st.last.co2 * (1 - k) + 400 * k
          }
          if (st.coolTicks === 0 &&
              Math.abs(st.last.temperature - b.temperature) < 0.5 &&
              st.last.smoke < 0.2) {
            st.last = null
          } else {
            await persist(spec, st, st.last)
            dirty = true
          }
        }
      }
    }

    if (dirty && Date.now() - lastPrune > PRUNE_EVERY_MS) {
      lastPrune = Date.now()
      await pruneHistory()
    }
    } finally {
      ticking = false
    }
  }, TICK_MS)

  logger.info('火灾演化引擎已启动（LSTM 驱动）', { tickMs: TICK_MS, service: LSTM_BASE })
}

export function stopEngine() {
  if (timer) clearInterval(timer)
  timer = null
  running = false
  logger.info('火灾演化引擎已停止')
}

async function persist(spec, st, reading) {
  try {
    await FireData.create({
      compartmentId: spec.id,
      eventId: st.fire?.id ?? null,
      temperature: reading.temperature,
      smoke: reading.smoke,
      oxygen: reading.oxygen,
      co: reading.co,
      co2: reading.co2 ?? 400,
      timestamp: new Date(),
      source: 'simulation'
    })
  } catch (e) {
    logger.error('火灾数据落库失败', { compartmentId: spec.id, message: e.message })
  }
}

let restored = false
async function restoreActiveFires() {
  if (restored) return
  restored = true
  try {
    const actives = await FireEvent.findAll({ where: { status: 'active' } })
    for (const rec of actives) {
      const spec = getCompartment(rec.compartmentId)
      if (!spec) continue
      const st = stateOf(spec.id)
      st.fire = {
        id: rec.id, status: 'active', steps: 0,
        suppressed: false, evacuated: false
      }
      logger.info('恢复活跃火灾', { compartmentId: spec.id, fireId: rec.id })
    }
  } catch (e) {
    logger.error('恢复活跃火灾失败', { message: e.message })
  }
}

async function pruneHistory() {
  try {
    for (const spec of ALL_COMPARTMENTS()) {
      const count = await FireData.count({ where: { compartmentId: spec.id } })
      if (count <= HISTORY_RETENTION) continue
      const old = await FireData.findAll({
        where: { compartmentId: spec.id },
        order: [['timestamp', 'ASC']],
        limit: count - HISTORY_RETENTION,
        attributes: ['id']
      })
      if (old.length) await FireData.destroy({ where: { id: old.map(r => r.id) } })
    }
  } catch (e) {
    logger.error('历史数据清理失败', { message: e.message })
  }
}

export { THRESHOLDS }

