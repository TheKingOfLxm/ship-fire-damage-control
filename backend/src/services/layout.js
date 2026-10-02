/**
 * 舱室布局读取 —— 后端侧的统一布局入口
 *
 * 权威定义在 config/compartments.json，前端 src/config/shipLayout.js 读的是同一份。
 * 二期之前后端在 initDatabase.js 里另写了一份舱室数组，导致：
 *   id=1 在前端是主机舱、在数据库里是电站间
 * 前端发 compartmentId=1 实际会操作到别的舱室，且不报错。
 * 现在两侧共用一份 JSON，id 天然一致。
 */
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
// backend/src/services -> 项目根
const CONFIG_PATH = resolve(__dirname, '../../../config/compartments.json')

let cache = null

function load() {
  if (cache) return cache
  cache = JSON.parse(readFileSync(CONFIG_PATH, 'utf8'))
  return cache
}

export const SHIP = () => load().ship
export const ALL_COMPARTMENTS = () => load().compartments
export const THRESHOLDS = () => load().thresholds

export function getCompartment(id) {
  const n = Number(id)
  return load().compartments.find(c => c.id === n) || null
}

export function getCompartmentByName(name) {
  return load().compartments.find(c => c.name === name) || null
}

export { CONFIG_PATH, join as _join }
