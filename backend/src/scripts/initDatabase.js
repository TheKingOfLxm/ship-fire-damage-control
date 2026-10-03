/**
 * 数据库初始化脚本
 * ═══════════════════════════════════════════════════════════════
 * ⚠ 会 sync({ force: true }) 删表重建，只在首次部署时跑。
 *
 * 舱室与船舶数据**全部来自 config/compartments.json**（唯一权威定义），
 * 并显式写入配置里的 id —— 保证数据库编号与前端/后端 layout 完全一致。
 *
 * 旧版在这里硬编码了一份舱室数组，且顺序与配置错位（电站间拿到 id=1，
 * 而配置里 id=1 是主机舱），正是历史上"前端操作到别的舱室还不报错"
 * 那个 bug 的来源。本版从配置播种后，这类错位不再可能发生。
 *
 * 日常对齐（不删数据）请用 syncLayout.js。
 */
import db from '../models/index.js'
import sequelize from '../config/database.js'
import { ALL_COMPARTMENTS, SHIP } from '../services/layout.js'
import logger from '../utils/logger.js'

const cOf = s => Math.round(s * 100) / 100

async function initializeDatabase() {
  try {
    logger.info('开始初始化数据库（⚠ 将删表重建，所有现有数据会丢失）...')

    await sequelize.sync({ force: true })
    logger.info('数据库表结构已创建')

    const shipCfg = SHIP()
    await db.Ship.create({
      id: shipCfg.id,
      name: shipCfg.name,
      type: shipCfg.type,
      length: shipCfg.length,
      width: shipCfg.beam,
      status: 'active'
    })
    logger.info('默认船舶已创建', { shipId: shipCfg.id, name: shipCfg.name })

    for (const cfg of ALL_COMPARTMENTS()) {
      await db.Compartment.create({
        id: cfg.id,                     // 显式用配置 id，杜绝编号错位
        shipId: shipCfg.id,
        name: cfg.name,
        type: cfg.type,
        positionX: cOf(cfg.anchor.x),
        positionY: cOf(cfg.anchor.y),
        positionZ: cOf(cfg.anchor.z),
        firePositionX: cOf(cfg.fireOrigin.x),
        firePositionY: cOf(cfg.fireOrigin.y),
        firePositionZ: cOf(cfg.fireOrigin.z),
        cameraOffsetX: cOf(cfg.camera.offset.x),
        cameraOffsetY: cOf(cfg.camera.offset.y),
        cameraOffsetZ: cOf(cfg.camera.offset.z),
        baseTemperature: cfg.base.temperature,
        baseSmoke: cfg.base.smoke,
        baseOxygen: cfg.base.oxygen,
        baseCO: cfg.base.co,
        modelPath: cfg.model,
        status: 'normal'
      })
    }
    logger.info('默认舱室已创建', { count: ALL_COMPARTMENTS().length, source: 'config/compartments.json' })

    logger.info('数据库初始化完成!')
    process.exit(0)
  } catch (error) {
    logger.error('数据库初始化失败:', error)
    process.exit(1)
  }
}

initializeDatabase()
