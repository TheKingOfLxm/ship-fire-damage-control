/**
 * 服务端舱室火灾演化引擎（LSTM 驱动）
 * ═══════════════════════════════════════════════════════════════
 *
 * 演化链路的真实结构（如实描述，不夸大模型的作用）：
 *   LSTM 服务每个 tick 都把输入窗口**再锚定**到该舱室的真实 FDS 轨迹
 *   （游标随仿真时间前进），模型在锚定后的窗口上做前向，产出读数。
 *   也就是说：FDS 轨迹提供"火灾处于什么状态"，LSTM 负责算出这个
 *   状态下的读数细节。这不是回放——报出去的数是模型前向算出来的——
 *   但火灾的整体走向由训练轨迹决定，这一点必须向使用者披露。
 *
 * 服务端只做三件事，都不是"另建一套物理"：
 *   1. 物理量程钳制（温度不会超过 1500℃ 等）
 *   2. 越限时自动升/降级告警
 *   3. 灭火指令的分派：v7 干预模型把灭火状态交给模型本身响应；
 *      旧模型没有干预训练数据，退回运维层叠加（见 tick 循环注释）
 *
 * 必须向使用者披露的模型限制（实测自训练数据）：
 *   · 模型单次前向的可靠时域有限（v6/v7 约 30 秒），之后是分布外外推。
 *   · 退化通道（如机库温度）由 checkpoint 的 degenerateChannels 自述。
 *   · 干预能力按舱室生效：只有加载了 v7（4 通道）模型的舱室能真正
 *     预测"投入灭火后火会怎么走"，其余舱室的 suppress 仍是运维层叠加。
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
// v7 干预模型（4 通道，含灭火状态 S）的舱室名集合。
// 这些舱室的 suppress 由**模型本身**响应（后端把灭火水平填进 S 通道），
// 不再叠加显示层回落 —— 双重压制会把温度压得比任一机制都低。
let interventionModels = new Set()

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
    const interv = new Set()
    for (const [name, s] of Object.entries(shapes)) {
      for (const c of s?.degenerateChannels || []) {
        if (String(c).toLowerCase().includes('temperature')) dead.add('temperature')
      }
      // v7 干预模型：suppression 由模型响应（见 interventionModels 声明处）
      if (s?.interventionChannel) interv.add(name)
    }
    if (dead.size) noTemperatureSignal = dead
    if (interv.size) interventionModels = interv
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
/** 当前灭火水平（0..1），随指令后的时间爬坡。
 *  干预模型（v7）：按训练时的灭火剂到位曲线 1-exp(-t/5s)，约 10 秒满。
 *  旧模型（v6）：沿用显示层叠加的有效系数曲线（0.2 起步、每秒 +0.05）。
 */
function suppressionLevel(spec, st) {
  if (!st?.fire?.suppressed) return 0
  const t = st.fire.suppressedTicks || 0
  return interventionModels.has(spec.name)
    ? 1 - Math.exp(-t / 5)
    : Math.min(0.9, 0.2 + 0.05 * t)
}

async function lstmStep(spec, st, steps = STEPS_PER_TICK) {
  const payload = {
    compartmentName: spec.name,
    steps,
    // 当前灭火状态：v7 干预模型把它作为第 4 输入通道，输出扑救后的演化；
    // 旧模型不消费该字段（后端走显示层叠加）。
    suppression: Number(suppressionLevel(spec, st).toFixed(4))
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
  // 注意：计时基准在请求**成功后**才推进（见函数末尾）。失败期间流逝的
  // 墙钟时间不能丢 —— 否则 LSTM 挂 30 秒再恢复时，火灾等于被冻结了
  // 30 秒还不补步，与"按真实经过时间演化"的承诺矛盾。
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
  st.lastTickAt = now          // 成功才推进计时基准，失败的时间下一拍补回
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
  temperature: 'temperature', smoke: 'smoke', oxygen: 'gas', co: 'gas', co2: 'gas'
}
const METRIC_TITLE = {
  temperature: '温度告警', smoke: '烟雾告警', oxygen: '缺氧告警', co: '有毒气体告警',
  co2: '二氧化碳告警'
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
  const t = THRESHOLDS()[metric]
  if (!t) return `${spec.name}${metric} ${value}`
  const u = t.unit
  const v = metric === 'oxygen' || metric === 'smoke' ? value.toFixed(1) : value.toFixed(0)
  const n = { temperature: '温度', smoke: '烟雾浓度', oxygen: '氧气浓度', co: '一氧化碳', co2: '二氧化碳' }[metric]
  return `${spec.name}${n} ${v}${u}`
}

async function syncAlerts(spec, reading) {
  const now = new Date()
  for (const metric of ['temperature', 'smoke', 'oxygen', 'co', 'co2']) {
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

/** compartmentId -> 是否有一次 ignite 正在落库（并发护栏，见 ignite） */
const igniting = new Set()

export async function ignite(compartmentId, { intensity = 0.6, spreadRate = 0.1 } = {}) {
  const spec = getCompartment(compartmentId)
  if (!spec) throw Object.assign(new Error('舱室不存在'), { status: 404 })
  const st = stateOf(spec.id)
  if (st.fire && st.fire.status === 'active') return { fire: st.fire, created: false }
  // 两个客户端同时 start 同一舱室时，都会在上面的检查处看到"没有活跃火灾"，
  // 然后各自建一条 FireEvent —— 其中一条成为永不结束的孤儿记录。
  // ignite 的检查与赋值之间隔着 await FireEvent.create，必须加在途护栏。
  if (igniting.has(spec.id)) return { fire: st.fire, created: false }
  igniting.add(spec.id)
  try {
    const record = await FireEvent.create({
      compartmentId: spec.id,
      // fire_events.type 存的是火灾**起因**（ENUM），不是舱室类型。
      // 舱室类型如 engine_room 装不进 ENUM，必须走 config 里的 fireCause 映射。
      type: spec.fireCause || 'unknown',
      status: 'active',
      intensity, spreadRate, startTime: new Date()
    })

    st.fire = {
      id: record.id, status: 'active', steps: 0,
      suppressed: false, evacuated: false,
      suppressedTicks: 0, quietTicks: 0,
      startedAt: record.startTime
    }
    st.window = null       // 让 LSTM 按舱室基值重新播种
    st.last = null
    st.lastTickAt = 0      // 清计时基准：点火是第一拍，不能拿停火前的时间差来推火势
    st.lastEvolvedAt = 0
    st.coolTicks = 0
    await Compartment.update({ status: 'danger' }, { where: { id: spec.id } })
    logger.info('火灾已点燃', { compartmentId: spec.id, fireId: record.id })
    return { fire: st.fire, created: true }
  } finally {
    igniting.delete(spec.id)
  }
}

export async function suppress(compartmentId) {
  const spec = getCompartment(compartmentId)
  const st = spec && stateOf(spec.id)
  if (!st?.fire || st.fire.status !== 'active') {
    throw Object.assign(new Error('该舱室没有活跃火灾'), { status: 404 })
  }
  st.fire.suppressed = true
  st.fire.suppressedTicks = 0
  st.fire.quietTicks = 0
  // 落库是为了后端重启后能恢复"灭火进行中"的状态（restoreActiveFires）。
  // 旧库没有这两列时 update 会失败 —— 捕获后照常运行，仅丢持久化。
  try {
    await FireEvent.update({ suppressed: true }, { where: { id: st.fire.id } })
  } catch { /* 旧表结构缺列，见 app.js 的 sync({alter:true}) 说明 */ }
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
  try {
    await FireEvent.update({ evacuated: true }, { where: { id: st.fire.id } })
  } catch { /* 同上：旧表结构缺列时仅内存生效 */ }
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
      suppressedAt: new Date(), suppressedMethod: method,
      modelSteps: st.fire.steps || 0,
      suppressed: !!st.fire.suppressed, evacuated: !!st.fire.evacuated
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
  const stalled = active && st.lastEvolvedAt > 0 && Date.now() - st.lastEvolvedAt > 15000
  return {
    active,
    fireId: f?.id ?? null,
    suppressed: !!f?.suppressed,
    evacuated: !!f?.evacuated,
    // 火灾已燃烧的墙钟秒数（起火时刻来自 FireEvent.startTime）
    elapsedSeconds: active && f?.startedAt
      ? Math.max(0, Math.round((Date.now() - new Date(f.startedAt).getTime()) / 1000))
      : null,
    // 演化是否停摆：LSTM 服务掉线/超时后读数会冻结，必须让前端能区分
    // "火稳住了"和"引擎挂了"。15 秒没有任何成功推进即视为停摆。
    evolutionStalled: stalled,
    lastEvolvedAt: st.lastEvolvedAt || null,
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
      // 灭火干预能力按舱室如实披露：
      //   intervention 里的舱室 = v7 模型，suppress 由模型响应（真反事实）；
      //   其余舱室 = 运维层叠加（模型没有干预训练数据）。
      interventionCompartments: [...interventionModels],
      suppressionNote: interventionModels.size
        ? `${[...interventionModels].join('、')} 的灭火指令由 v7 干预模型响应`
          + `（灭火状态作为模型输入通道）；其余舱室的灭火为运维层叠加，`
          + `模型训练数据中不含灭火干预场景，不能预测扑救后的反事实演化。`
        : '所有舱室的灭火指令均为运维层叠加 —— 模型训练数据中不含灭火'
          + '干预场景，系统不能预测"投入灭火后火势如何演化"的反事实问题。',
      interCompartment: false,
      interCompartmentNote:
        '五个舱室是五个独立模型，不含舱间导热/蔓延耦合，'
        + '不能回答"火会不会蔓延到隔壁舱"。',
      derivedChannels: ['smoke', 'oxygen'],
      derivedChannelsNote:
        '模型只输出温度/CO/CO₂三个通道，烟雾与氧气为推导值（代理公式），非模型输出。',
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
          // 灭火计时在推进**前**步进：被灭火的第一个 tick，S 通道就应已生效
          if (st.fire.suppressed) {
            st.fire.suppressedTicks = (st.fire.suppressedTicks || 0) + 1
          }
          const out = await lstmStep(spec, st)
          st.window = out.window
          st.fire.steps = (st.fire.steps || 0) + out.diagnostics.rolloutSteps
          st.lastEvolvedAt = Date.now()
          const row = out.steps[out.steps.length - 1]
          const reading = decode(spec, row)

          if (st.fire.suppressed) {
            if (interventionModels.has(spec.name)) {
              // v7 干预模型：扑救响应由模型给出（S 通道已在请求里），
              // 这里**不做**显示层叠加 —— 双重压制会把温度压得比任一
              // 机制都低。只保留"读数贴近基线 → 自动判定熄火"。
            } else {
              // 旧模型（v6 及更早）的运维层叠加（如实披露）：
              // 训练数据里没有灭火干预场景，模型算不出"扑救后火怎么走"，
              // 这里做物理上说得通的显示层回落：
              //   · 有效系数随时间爬坡，上限 0.9；
              //   · 作用于全部通道（氧气/CO₂ 一并回落）；
              const k = suppressionLevel(spec, st)
              const b = spec.base
              reading.temperature = reading.temperature * (1 - k) + b.temperature * k
              reading.smoke *= (1 - k)
              reading.co *= (1 - k)
              reading.co2 = reading.co2 * (1 - k) + 400 * k
              reading.oxygen = reading.oxygen * (1 - k) + 20.9 * k
            }
            // 熄火判定两种模型共用：读数持续贴近基线 → 火被扑灭
            const b = spec.base
            if (reading.temperature < b.temperature + 15 && reading.smoke < 3) {
              st.fire.quietTicks = (st.fire.quietTicks || 0) + 1
            } else {
              st.fire.quietTicks = 0
            }
            if (st.fire.quietTicks >= 3) {
              logger.info('灭火系统压制生效，火灾自动熄灭', {
                compartmentId: spec.id,
                interventionModel: interventionModels.has(spec.name)
              })
              await extinguish(spec.id, { method: 'automatic' })
              continue
            }
          }

          st.last = reading
          await persist(spec, st, reading)
          await syncAlerts(spec, reading)
          // modelSteps 周期性落库：重启恢复时用来还原"已推进多远"。
          // 不必每拍都写 —— 最多丢 10 拍的进度，代价可接受。
          if (st.fire.id && st.fire.steps % 10 === 0) {
            FireEvent.update({ modelSteps: st.fire.steps }, { where: { id: st.fire.id } })
              .catch(() => { /* 旧表缺列，仅内存生效 */ })
          }
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
  try {
    const actives = await FireEvent.findAll({ where: { status: 'active' } })
    for (const rec of actives) {
      const spec = getCompartment(rec.compartmentId)
      if (!spec) continue
      const st = stateOf(spec.id)
      if (st.fire?.status === 'active') continue
      // 恢复的不只是"这舱有火"这个壳：灭火/疏散指令与已推进步数也一并
      // 还原 —— 旧版全部清零，重启等于"从零重烧 + 此前下达的指令静默消失"。
      st.fire = {
        id: rec.id, status: 'active',
        steps: Number(rec.modelSteps) || 0,
        suppressed: !!rec.suppressed, evacuated: !!rec.evacuated,
        suppressedTicks: rec.suppressed ? 1 : 0, quietTicks: 0,
        startedAt: rec.startTime
      }
      st.lastTickAt = 0        // 从当前时刻重新起算，不把停机时间当火势时间补进去
      st.lastEvolvedAt = 0
      logger.info('恢复活跃火灾', {
        compartmentId: spec.id, fireId: rec.id,
        suppressed: !!rec.suppressed, evacuated: !!rec.evacuated,
        modelSteps: Number(rec.modelSteps) || 0
      })
    }
    // 全部恢复成功才置位：查询失败时下一拍重试，不会永久放弃。
    restored = true
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

