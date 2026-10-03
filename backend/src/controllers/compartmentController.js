import db from '../models/index.js'
import { asyncHandler } from '../middleware/errorHandler.js'
import logger from '../utils/logger.js'

const { Compartment, Ship, FireEvent, FireData } = db

export const getCompartments = asyncHandler(async (req, res) => {
  const compartments = await Compartment.findAll({
    include: [{
      model: Ship,
      as: 'ship',
      attributes: ['id', 'name', 'type']
    }],
    attributes: { exclude: ['createdAt', 'updatedAt'] }
  })

  res.json({
    success: true,
    data: compartments
  })
})

export const getCompartmentById = asyncHandler(async (req, res) => {
  const { id } = req.params

  const compartment = await Compartment.findByPk(id, {
    include: [{
      model: Ship,
      as: 'ship'
    }]
  })

  if (!compartment) {
    return res.status(404).json({
      success: false,
      message: '舱室不存在'
    })
  }

  // 获取当前活跃火灾
  const activeFire = await FireEvent.findOne({
    where: {
      compartmentId: id,
      status: 'active'
    },
    attributes: ['id', 'type', 'intensity', 'startTime']
  })

  res.json({
    success: true,
    data: {
      ...compartment.toJSON(),
      activeFire
    }
  })
})

// 写接口的字段白名单：req.body 直灌 db.create/update 属于 mass assignment，
// 调用方可以借此改写任意列（shipId/status 等）。status 由仿真引擎维护，
// 不在写接口的可写范围内。
const COMPARTMENT_FIELDS = [
  'shipId', 'name', 'type',
  'positionX', 'positionY', 'positionZ',
  'firePositionX', 'firePositionY', 'firePositionZ',
  'cameraOffsetX', 'cameraOffsetY', 'cameraOffsetZ',
  'baseTemperature', 'baseSmoke', 'baseOxygen', 'baseCO',
  'modelPath'
]

function pickCompartmentFields(body = {}) {
  const out = {}
  for (const k of COMPARTMENT_FIELDS) {
    if (body[k] !== undefined) out[k] = body[k]
  }
  return out
}

export const createCompartment = asyncHandler(async (req, res) => {
  const compartment = await Compartment.create(pickCompartmentFields(req.body))

  logger.info('创建舱室', { compartmentId: compartment.id })

  res.status(201).json({
    success: true,
    data: compartment
  })
})

export const updateCompartment = asyncHandler(async (req, res) => {
  const { id } = req.params
  const data = pickCompartmentFields(req.body)

  const compartment = await Compartment.findByPk(id)
  if (!compartment) {
    return res.status(404).json({
      success: false,
      message: '舱室不存在'
    })
  }

  await compartment.update(data)

  logger.info('更新舱室', { compartmentId: id })

  res.json({
    success: true,
    data: compartment
  })
})

export const deleteCompartment = asyncHandler(async (req, res) => {
  const { id } = req.params

  const compartment = await Compartment.findByPk(id)
  if (!compartment) {
    return res.status(404).json({
      success: false,
      message: '舱室不存在'
    })
  }

  await compartment.destroy()

  logger.info('删除舱室', { compartmentId: id })

  res.json({
    success: true,
    message: '舱室已删除'
  })
})

export default {
  getCompartments,
  getCompartmentById,
  createCompartment,
  updateCompartment,
  deleteCompartment
}
