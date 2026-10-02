import db from '../models/index.js'
import { asyncHandler } from '../middleware/errorHandler.js'
import { Op } from 'sequelize'
import logger from '../utils/logger.js'
import { getCompartment, ALL_COMPARTMENTS, THRESHOLDS } from '../services/layout.js'
import * as sim from '../services/fireSimulation.js'

const { FireEvent, FireData, Alert, Compartment } = db

/** 允许的指令集合 —— 二期之前只认 start/stop，suppress/evacuate/reset 全被 400 拒掉 */
const ACTIONS = ['start', 'stop', 'suppress', 'evacuate', 'reset']

/* ------------------------------------------------------------------ */
/* 读数                                                                */
/* ------------------------------------------------------------------ */

export const getCompartmentFireData = asyncHandler(async (req, res) => {
  const { compartmentId } = req.params
  const spec = getCompartment(compartmentId)
  if (!spec) {
    return res.status(404).json({ success: false, message: '舱室不存在' })
  }

  const compartment = await Compartment.findByPk(spec.id)
  const reading = sim.currentReading(spec.id) || spec.base
  const status = sim.fireStatus(spec.id)

  // 从库里的最新一条取权威时间戳
  const latest = await FireData.findOne({
    where: { compartmentId: spec.id },
    order: [['timestamp', 'DESC']]
  })

  res.json({
    success: true,
    data: {
      compartmentId: spec.id,
      compartmentCode: spec.code,
      compartmentName: spec.name,
      compartmentType: spec.type,
      fireStatus: status.active ? 'active' : 'inactive',
      // 火灾状态机，前端据此决定按钮可用性与提示
      fire: {
        active: status.active,
        fireId: status.fireId,
        elapsedSeconds: Math.round(status.elapsed),
        suppressed: status.suppressed,
        evacuated: status.evacuated,
        severity: Number(status.severity.toFixed(3))
      },
      temperature: Number(reading.temperature.toFixed(1)),
      smoke: Number(reading.smoke.toFixed(1)),
      oxygen: Number(reading.oxygen.toFixed(1)),
      co: Number(reading.co.toFixed(0)),
      co2: 400 + Math.round(reading.temperature * 2.5),
      // 各项是否越限，前端可直接用来上色
      thresholds: {
        temperature: levelOf('temperature', reading.temperature),
        smoke: levelOf('smoke', reading.smoke),
        oxygen: levelOf('oxygen', reading.oxygen),
        co: levelOf('co', reading.co)
      },
      source: status.active ? 'simulation' : 'sensor',
      timestamp: latest?.timestamp || new Date()
    }
  })
})

function levelOf(metric, value) {
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

/** 全舰态势总览：一次拿齐所有舱室，供态势面板与告警中心使用 */
export const getFleetStatus = asyncHandler(async (req, res) => {
  const items = ALL_COMPARTMENTS().map(spec => {
    const reading = sim.currentReading(spec.id) || spec.base
    const st = sim.fireStatus(spec.id)
    const worst = ['temperature', 'smoke', 'oxygen', 'co']
      .map(m => levelOf(m, reading[m]))
      .reduce((a, b) => rank(b) > rank(a) ? b : a, 'info')
    return {
      compartmentId: spec.id,
      compartmentCode: spec.code,
      compartmentName: spec.name,
      compartmentType: spec.type,
      fireActive: st.active,
      severity: Number(st.severity.toFixed(3)),
      temperature: Number(reading.temperature.toFixed(1)),
      smoke: Number(reading.smoke.toFixed(1)),
      oxygen: Number(reading.oxygen.toFixed(1)),
      co: Number(reading.co.toFixed(0)),
      overall: worst,
      risk: spec.risk
    }
  })
  res.json({
    success: true,
    data: {
      compartments: items,
      activeFires: items.filter(i => i.fireActive).length,
      worstOverall: items.reduce((a, b) => rank(b.overall) > rank(a.overall) ? b : a, items[0]).overall,
      serverTime: new Date()
    }
  })
})

/** 演化引擎自述：把「用的什么模型、有什么已知限制」如实暴露给前端 */
export const getEngineInfo = asyncHandler(async (req, res) => {
  const info = sim.engineInfo()
  // 顺带探一下 LSTM 服务的配置，把真实采样间隔取回来
  try {
    const { default: axios } = await import('axios')
    const url = process.env.LSTM_SERVICE_URL || 'http://localhost:5001'
    const r = await axios.get(`${url}/api/lstm/config`, { timeout: 4000 })
    info.lstmConfig = r.data?.data || null
  } catch {
    info.lstmConfig = null
  }
  res.json({ success: true, data: info })
})

const rank = l => ({ info: 0, low: 1, medium: 2, high: 3, critical: 4 }[l] ?? 0)

/* ------------------------------------------------------------------ */
/* 指令                                                                */
/* ------------------------------------------------------------------ */

export const controlFire = asyncHandler(async (req, res) => {
  const { compartmentId } = req.params
  const { action, intensity = 0.6, spreadRate = 0.1 } = req.body || {}

  if (!ACTIONS.includes(action)) {
    return res.status(400).json({
      success: false,
      message: `无效的操作，支持：${ACTIONS.join(' / ')}`
    })
  }

  const spec = getCompartment(compartmentId)
  if (!spec) return res.status(404).json({ success: false, message: '舱室不存在' })

  const before = sim.fireStatus(spec.id)

  // 前置条件校验：没有火灾时不能执行扑灭/疏散
  if ((action === 'suppress' || action === 'evacuate') && !before.active) {
    return res.status(409).json({
      success: false,
      message: `${spec.name}当前没有活跃火灾，无法执行该指令`,
      data: { fire: before }
    })
  }

  const run = {
    start: () => sim.ignite(spec.id, { intensity, spreadRate }),
    suppress: () => sim.suppress(spec.id),
    evacuate: () => sim.evacuate(spec.id),
    stop: () => sim.extinguish(spec.id, { method: 'manual' }),
    reset: () => sim.resetCompartment(spec.id)
  }[action]

  const result = await run()
  const after = sim.fireStatus(spec.id)

  logger.info('火灾指令已执行', { compartmentId: spec.id, action })

  res.json({
    success: true,
    message: MESSAGES[action](spec.name),
    data: {
      compartmentId: spec.id,
      compartmentName: spec.name,
      action,
      previous: before,
      fire: after
    }
  })
})

const MESSAGES = {
  start: n => `${n}火灾模拟已启动`,
  stop: n => `${n}火灾已终止`,
  suppress: n => `${n}灭火系统已启动，正在压制火势`,
  evacuate: n => `${n}人员疏散指令已执行`,
  reset: n => `${n}数据已重置为初始状态`
}

/* ------------------------------------------------------------------ */
/* 历史与预测输入                                                      */
/* ------------------------------------------------------------------ */

export const getFireHistory = asyncHandler(async (req, res) => {
  const { compartmentId } = req.params
  const { limit = 180, offset = 0 } = req.query
  const spec = getCompartment(compartmentId)
  if (!spec) return res.status(404).json({ success: false, message: '舱室不存在' })

  const take = Math.min(1000, parseInt(limit, 10) || 180)
  const skip = Math.max(0, parseInt(offset, 10) || 0)
  const count = await FireData.count({ where: { compartmentId: spec.id } })

  // 趋势图要的是"最近这一段"，不是历史上最早的那一段。
  // 直接 order ASC + limit 拿到的是最旧的 N 条，曲线会永远冻结在远古数据上。
  // 因此先按时间倒序取，再翻回正序供绘图。
  // 多取 1 条作为窗口左边的锚点，好让窗口内第一点也能算出真实变化速率。
  const rowsDesc = await FireData.findAll({
    where: { compartmentId: spec.id },
    order: [['timestamp', 'DESC']],
    limit: take + 1,
    offset: skip
  })
  const windowed = rowsDesc.reverse()
  const anchor = windowed.length > take ? windowed.shift() : null
  const rows = windowed

  // 附带变化速率：前端用来显示"每分钟上升 X℃"和识别拐点
  const series = rows.map((r, i) => {
    const prev = i > 0 ? rows[i - 1] : anchor
    const dt = prev ? (new Date(r.timestamp) - new Date(prev.timestamp)) / 1000 : 0
    return {
      timestamp: r.timestamp,
      temperature: r.temperature,
      smoke: r.smoke,
      oxygen: r.oxygen,
      co: r.co,
      co2: r.co2,
      rates: prev ? {
        // ℃/min，正数表示上升
        temperature: dt > 0 ? ((r.temperature - prev.temperature) / dt) * 60 : 0,
        smoke: dt > 0 ? ((r.smoke - prev.smoke) / dt) * 60 : 0,
        oxygen: dt > 0 ? ((r.oxygen - prev.oxygen) / dt) * 60 : 0,
        co: dt > 0 ? ((r.co - prev.co) / dt) * 60 : 0
      } : null
    }
  })

  res.json({
    success: true,
    data: {
      compartmentId: spec.id,
      compartmentName: spec.name,
      total: count,
      limit: take,
      offset: skip,
      series
    }
  })
})

/** 火灾事件流水 */
export const getFireEvents = asyncHandler(async (req, res) => {
  const { compartmentId, limit = 50 } = req.query
  const where = {}
  if (compartmentId) where.compartmentId = Number(compartmentId)
  const rows = await FireEvent.findAll({
    where,
    order: [['startTime', 'DESC']],
    limit: Math.min(200, parseInt(limit, 10) || 50)
  })
  res.json({ success: true, data: rows })
})

export default {
  getCompartmentFireData,
  getFleetStatus,
  getEngineInfo,
  controlFire,
  getFireHistory,
  getFireEvents
}
