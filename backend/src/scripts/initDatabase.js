import db from '../models/index.js'
import logger from '../utils/logger.js'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

async function initializeDatabase() {
  try {
    logger.info('开始初始化数据库...')

    // 同步模型到数据库
    await db.sequelize.sync({ force: true })
    logger.info('数据库表结构已创建')

    // 创建默认船舶
    const ship = await db.Ship.create({
      name: '智能船舶-01',
      type: '消防船',
      length: 65,
      width: 12,
      height: 8,
      displacement: 1200,
      status: 'active'
    })
    logger.info('默认船舶已创建', { shipId: ship.id })

    // 创建舱室
    const compartments = [
      {
        shipId: ship.id,
        name: '电站间',
        type: 'power_station',
        positionX: 1.5,
        positionY: 0.8,
        positionZ: 0,
        firePositionX: 1.5,
        firePositionY: 1,
        firePositionZ: 0,
        cameraOffsetX: -1,
        cameraOffsetY: 2,
        cameraOffsetZ: 3,
        baseTemperature: 35,
        baseSmoke: 0,
        baseOxygen: 21,
        baseCO: 0,
        modelPath: '/models/电站间.glb'
      },
      {
        shipId: ship.id,
        name: '机库',
        type: 'hangar',
        positionX: 0.8,
        positionY: 0.6,
        positionZ: 0,
        firePositionX: 0.8,
        firePositionY: 0.8,
        firePositionZ: 0,
        cameraOffsetX: -2,
        cameraOffsetY: 1.5,
        cameraOffsetZ: 3,
        baseTemperature: 25,
        baseSmoke: 0,
        baseOxygen: 21,
        baseCO: 0,
        modelPath: '/models/机库.glb'
      },
      {
        shipId: ship.id,
        name: '士兵住舱',
        type: 'living_quarters',
        positionX: -0.5,
        positionY: 0.4,
        positionZ: 0,
        firePositionX: -0.5,
        firePositionY: 0.6,
        firePositionZ: 0,
        cameraOffsetX: -4,
        cameraOffsetY: 0.5,
        cameraOffsetZ: 3,
        baseTemperature: 22,
        baseSmoke: 0,
        baseOxygen: 21,
        baseCO: 0,
        modelPath: '/models/士兵住舱.glb'
      },
      {
        shipId: ship.id,
        name: '灶炉间',
        type: 'galley',
        positionX: 0.2,
        positionY: 0.4,
        positionZ: 0,
        firePositionX: 0.2,
        firePositionY: 0.6,
        firePositionZ: 0,
        cameraOffsetX: -3,
        cameraOffsetY: 0.5,
        cameraOffsetZ: 3,
        baseTemperature: 28,
        baseSmoke: 0,
        baseOxygen: 21,
        baseCO: 5,
        modelPath: '/models/灶炉间.glb'
      },
      {
        shipId: ship.id,
        name: '主机舱',
        type: 'engine_room',
        positionX: 0.8,
        positionY: 0.4,
        positionZ: 0,
        firePositionX: 0.8,
        firePositionY: 0.6,
        firePositionZ: 0,
        cameraOffsetX: -2,
        cameraOffsetY: 0.5,
        cameraOffsetZ: 3,
        baseTemperature: 45,
        baseSmoke: 0,
        baseOxygen: 21,
        baseCO: 10,
        modelPath: '/models/主机舱.glb'
      }
    ]

    for (const compartmentData of compartments) {
      await db.Compartment.create(compartmentData)
    }
    logger.info('默认舱室已创建', { count: compartments.length })

    logger.info('数据库初始化完成!')
    process.exit(0)
  } catch (error) {
    logger.error('数据库初始化失败:', error)
    process.exit(1)
  }
}

initializeDatabase()
