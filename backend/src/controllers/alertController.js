import db from '../models/index.js'
import { asyncHandler } from '../middleware/errorHandler.js'
import logger from '../utils/logger.js'
import { Op } from 'sequelize'

const { Alert, Compartment } = db

export const getAlerts = asyncHandler(async (req, res) => {
  const { status, level, limit = 50, offset = 0 } = req.query

  const where = {}
  if (status) where.status = status
  if (level) where.level = level

  const { count, rows } = await Alert.findAndCountAll({
    where,
    include: [{
      model: Compartment,
      as: 'compartment',
      attributes: ['id', 'name']
    }],
    order: [['createdAt', 'DESC']],
    limit: parseInt(limit),
    offset: parseInt(offset)
  })

  res.json({
    success: true,
    data: {
      total: count,
      records: rows,
      limit: parseInt(limit),
      offset: parseInt(offset)
    }
  })
})

export const getActiveAlerts = asyncHandler(async (req, res) => {
  // 「确认」只是标记已读，不等于消解。已确认的告警仍要留在值班列表里，
  // 否则操作员点完确认条目直接消失，看不出自己处理过哪一条。
  const alerts = await Alert.findAll({
    where: {
      status: { [Op.in]: ['active', 'acknowledged'] }
    },
    include: [{
      model: Compartment,
      as: 'compartment',
      attributes: ['id', 'name']
    }],
    order: [['level', 'DESC'], ['createdAt', 'DESC']]
  })

  res.json({
    success: true,
    data: alerts
  })
})

export const acknowledgeAlert = asyncHandler(async (req, res) => {
  const { id } = req.params

  const alert = await Alert.findByPk(id)
  if (!alert) {
    return res.status(404).json({
      success: false,
      message: '告警不存在'
    })
  }

  await alert.update({
    status: 'acknowledged',
    acknowledgedAt: new Date()
  })

  logger.info('告警已确认', { alertId: id })

  res.json({
    success: true,
    data: alert
  })
})

export const resolveAlert = asyncHandler(async (req, res) => {
  const { id } = req.params

  const alert = await Alert.findByPk(id)
  if (!alert) {
    return res.status(404).json({
      success: false,
      message: '告警不存在'
    })
  }

  await alert.update({
    status: 'resolved',
    resolvedAt: new Date()
  })

  logger.info('告警已解决', { alertId: id })

  res.json({
    success: true,
    data: alert
  })
})

export const createAlert = asyncHandler(async (req, res) => {
  const data = req.body

  const alert = await Alert.create(data)

  logger.info('创建告警', { alertId: alert.id, level: alert.level })

  res.status(201).json({
    success: true,
    data: alert
  })
})

export default {
  getAlerts,
  getActiveAlerts,
  acknowledgeAlert,
  resolveAlert,
  createAlert
}
