import express from 'express'
import fireController from '../controllers/fireController.js'
import compartmentController from '../controllers/compartmentController.js'
import alertController from '../controllers/alertController.js'
import { analyzeFire, getModels, chat, chatStream } from '../controllers/aiController.js'
import * as layout from '../services/layout.js'

const router = express.Router()

// 火灾相关路由
router.get('/fire/:compartmentId', fireController.getCompartmentFireData)
router.post('/fire/:compartmentId/control', fireController.controlFire)
router.get('/fire/:compartmentId/history', fireController.getFireHistory)
router.get('/fire/events/list', fireController.getFireEvents)
// 全舰态势总览：一次拿齐所有舱室，避免前端轮询 5 次
router.get('/fleet/status', fireController.getFleetStatus)
// 演化引擎自述：用的什么引擎、有什么已知限制，供前端如实展示
router.get('/engine/info', fireController.getEngineInfo)

// 舱室相关路由
router.get('/compartments', compartmentController.getCompartments)
router.get('/compartments/:id', compartmentController.getCompartmentById)
router.post('/compartments', compartmentController.createCompartment)
router.put('/compartments/:id', compartmentController.updateCompartment)
router.delete('/compartments/:id', compartmentController.deleteCompartment)

// 告警相关路由
router.get('/alerts', alertController.getAlerts)
router.get('/alerts/active', alertController.getActiveAlerts)
router.patch('/alerts/:id/acknowledge', alertController.acknowledgeAlert)
router.patch('/alerts/:id/resolve', alertController.resolveAlert)
router.post('/alerts', alertController.createAlert)

// AI分析路由
router.post('/ai/analyze', analyzeFire)
router.get('/ai/models', getModels)
router.post('/ai/chat', chat)
// 流式对话（SSE）：前端逐字显示 + 阶段提示
router.post('/ai/chat/stream', chatStream)

// 船舶信息路由 —— 与 config/compartments.json 同源
router.get('/ship/info', async (req, res) => {
  const ship = layout.SHIP()
  res.json({
    success: true,
    data: {
      id: ship.id,
      name: ship.name,
      nameEn: ship.nameEn,
      hullNumber: ship.hullNumber,
      type: ship.type,
      typeLabel: ship.typeLabel,
      length: ship.length,
      beam: ship.beam,
      draft: ship.draft,
      status: 'active'
    }
  })
})

export default router
