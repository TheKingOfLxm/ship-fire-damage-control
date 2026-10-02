/**
 * 舱室布局同步
 * ═══════════════════════════════════════════════════════════════
 * 把数据库里的舱室记录对齐到 config/compartments.json。
 *
 * 为什么需要：二期之前后端 initDatabase.js 自带一份舱室数组，
 * 编号与前端完全不同（后端 1=电站间，前端 1=主机舱）。前端发
 * compartmentId=1 时后端会去操作电站间，且不报任何错。
 *
 * 同步策略：**按 name 匹配**（名称是稳定业务键），而不是按 id。
 * 匹配上的更新所有字段；没匹配上的按配置插入；
 * 配置里没有、库里多余的标为废弃（置 status 而非删除，保留历史外键）。
 *
 * 用法:
 *   node src/scripts/syncLayout.js          # 同步
 *   node src/scripts/syncLayout.js --dry    # 只报告差异
 */
import db from '../models/index.js'
import sequelize from '../config/database.js'
import { ALL_COMPARTMENTS, SHIP, THRESHOLDS } from '../services/layout.js'
import logger from '../utils/logger.js'

const { Compartment, Ship, FireEvent, FireData, Alert } = db
const DRY = process.argv.includes('--dry')

const cOf = s => Math.round(s * 100) / 100

async function main() {
  await sequelize.authenticate()

  // ---- 船舶 ----
  const shipCfg = SHIP()
  let ship = await Ship.findOne({ where: { id: shipCfg.id } })
  if (!ship) {
    ship = await Ship.create({
      id: shipCfg.id,
      name: shipCfg.name,
      type: shipCfg.type,
      length: shipCfg.length,
      width: shipCfg.beam,
      status: 'active'
    })
    logger.info('创建船舶', { id: ship.id, name: ship.name })
  } else if (ship.name !== shipCfg.name) {
    await ship.update({
      name: shipCfg.name, type: shipCfg.type,
      length: shipCfg.length, width: shipCfg.beam
    })
    logger.info('更新船舶信息', { name: shipCfg.name })
  }

  // ---- 舱室 ----
  const existing = await Compartment.findAll()
  const byName = new Map(existing.map(r => [r.name, r]))
  const cfgNames = new Set(ALL_COMPARTMENTS().map(c => c.name))

  let created = 0
  let updated = 0

  /*
   * 编号重映射
   * ----------
   * 主键不能直接 update（Sequelize 会忽略），而配置编号与库里的编号
   * 恰好是错位的（1↔5、2↔1…），直接改会撞主键。
   * 做法：先整体挪到临时负数编号腾出空间，再落到目标编号。
   * 若两表之间存在外键数据，fire_events / fire_data / alerts 的
   * compartmentId 也要同步搬运，否则历史数据会指向错误的舱室。
   */
  const remap = []
  for (const cfg of ALL_COMPARTMENTS()) {
    const row = byName.get(cfg.name)
    if (row && row.id !== cfg.id) remap.push({ row, from: row.id, to: cfg.id })
  }

  if (remap.length) {
    const touched = remap.map(r => r.from)
    if (!DRY) {
      // 1) 全部挪到临时负数编号
      await sequelize.query(
        'UPDATE compartments SET id = -id WHERE id IN (:ids)', { replacements: { ids: touched } }
      )
      await sequelize.query(
        'UPDATE fire_events SET compartmentId = -compartmentId WHERE compartmentId IN (:ids)',
        { replacements: { ids: touched } }
      )
      await sequelize.query(
        'UPDATE fire_data SET compartmentId = -compartmentId WHERE compartmentId IN (:ids)',
        { replacements: { ids: touched } }
      )
      await sequelize.query(
        'UPDATE alerts SET compartmentId = -compartmentId WHERE compartmentId IN (:ids)',
        { replacements: { ids: touched } }
      )
      // 2) 落到目标编号
      for (const r of remap) {
        await sequelize.query('UPDATE compartments SET id = :to WHERE id = :from', {
          replacements: { from: -r.from, to: r.to }
        })
        for (const t of ['fire_events', 'fire_data', 'alerts']) {
          await sequelize.query(
            `UPDATE ${t} SET compartmentId = :to WHERE compartmentId = :from`,
            { replacements: { from: -r.from, to: r.to } }
          )
        }
      }
    }
    logger.info('舱室编号重映射', {
      dry: DRY,
      mapping: remap.map(r => `${r.row.name}: ${r.from} -> ${r.to}`)
    })
    console.log('\n编号重映射:')
    for (const r of remap) console.log(`  ${r.row.name}: ${r.from} -> ${r.to}`)
  }

  for (const cfg of ALL_COMPARTMENTS()) {
    const fields = {
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
    }

    const row = await Compartment.findOne({ where: { name: cfg.name } })
    if (row) {
      const changes = []
      for (const [k, v] of Object.entries(fields)) {
        if (k === 'status') continue      // 状态由仿真引擎维护，不在此覆盖
        if (row[k] !== v) changes.push(`${k}: ${row[k]} -> ${v}`)
      }
      if (changes.length) {
        if (!DRY) await row.update(fields)
        updated++
        logger.info(`同步舱室 ${cfg.name}`, { changes: DRY ? changes : changes.length })
      }
    } else {
      if (!DRY) await Compartment.create({ id: cfg.id, ...fields })
      created++
      logger.info(`新建舱室 ${cfg.name}`, { id: cfg.id })
    }
  }

  // 配置里已不存在的舱室：保留数据但标记废弃，避免历史外键断裂
  for (const row of existing) {
    if (cfgNames.has(row.name)) continue
    if (row.status !== 'deprecated') {
      if (!DRY) await row.update({ status: 'deprecated' })
      logger.warn('舱室已不在配置中，标记为废弃', { name: row.name, id: row.id })
    }
  }

  const final = await Compartment.findAll({ order: [['id', 'ASC']] })
  const th = THRESHOLDS()

  logger.info('=== 布局同步完成 ===', {
    dry: DRY, created, updated,
    total: final.length,
    thresholds: th
  })
  console.log('\n最终舱室映射（数据库 id -> 名称）:')
  for (const r of final) {
    console.log(`  id=${r.id}  ${r.name.padEnd(6, '　')}  type=${String(r.type).padEnd(16)}  ${r.modelPath}`)
  }
  console.log('')
}

main()
  .then(async () => { await sequelize.close(); process.exit(0) })
  .catch(async e => { console.error('同步失败:', e); await sequelize.close(); process.exit(1) })
