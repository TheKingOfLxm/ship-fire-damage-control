/**
 * 船舶布置与舱室定义 —— 全应用唯一数据源 (Single Source of Truth)
 *
 * 坐标系约定（与 tools/build_ship.py 导出的 GLB 完全一致）：
 *   +X = 船艏 (bow)      -X = 船艉 (stern)
 *   +Y = 甲板向上 (up)    Y = 0 为设计水线面 (DWL)
 *   +Z = 右舷 (starboard)
 *   单位 = 米 (meters)
 *
 * 任何舱室的 3D 锚点、包围盒、相机预设、火灾类型映射都只在这里定义。
 * Blender 建模脚本读取同一套数值，Vue 侧直接 import，两边不会漂移。
 */

/** 母船基础信息 */
export const SHIP = {
  name: '海鹰级护卫舰',
  nameEn: 'HAIYING-CLASS FRIGATE',
  hullNumber: 'FFG-572',
  type: '导弹护卫舰',
  typeEn: 'Guided-Missile Frigate',
  /** 总体尺寸（米） */
  length: 115,
  beam: 13.6,
  draft: 4.2,
  depthToMainDeck: 4.5,
  /** 主甲板高度（世界坐标 Y） */
  mainDeckY: 4.5,
  /** 飞行甲板高度 */
  flightDeckY: 8.2,
  /** 设计水线面 */
  waterlineY: 0
}

/** 火灾类型元数据 —— 全应用统一的类型字典 */
export const FIRE_TYPES = {
  engine: { key: 'engine', label: '机械火灾', short: '机械', color: '#f97316' },
  power: { key: 'power', label: '电气火灾', short: '电气', color: '#eab308' },
  galley: { key: 'galley', label: '厨房火灾', short: '厨房', color: '#fb923c' },
  living: { key: 'living', label: '生活区火灾', short: '生活', color: '#38bdf8' },
  hangar: { key: 'hangar', label: '燃油火灾', short: '燃油', color: '#ef4444' }
}

/**
 * 舱室定义
 *
 * anchor    —— 相机对准点 / 火焰锚点（世界坐标，米）
 * bounds    —— 舱室在船体坐标系下的包围盒；与 Blender 脚本
 *               tools/build_ship.py 的 COMPARTMENT_SPECS 一一对应，
 *               同时用于生成 ship.glb 中的 COMPARTMENT_<id> 拾取体
 * camera    —— 进入该舱室后的相机预设
 *   offset: 相对 anchor 的相机偏移
 * model     —— 舱室内部模型路径（相对 /public）
 */
export const COMPARTMENTS = [
  {
    id: 1,
    name: '主机舱',
    code: 'E-01',
    fireType: 'engine',
    deck: '下层甲板 · 机舱区',
    description: '燃气轮机与推进系统所在，燃油管路与高温部件集中，是全舰火灾后果最严重的舱室。',
    anchor: { x: -39, y: 0.5, z: 0 },
    /** 起火点：滑油日用柜附近，不是舱室几何中心 */
    fireOrigin: { x: -44.5, y: -3.2, z: 3.2 },
    /** 火焰基准尺寸（米），随火灾强度缩放 */
    fireSize: { w: 9.0, h: 5.4 },
    bounds: { min: { x: -48, y: -3.6, z: -5.2 }, max: { x: -30, y: 4.0, z: 5.2 } },
    camera: { offset: { x: -14, y: 10, z: 17 } },
    model: '/models/compartments/main-engine.glb',
    risk: 'critical'
  },
  {
    id: 2,
    name: '电站间',
    code: 'E-02',
    fireType: 'power',
    deck: '下层甲板 · 动力区',
    description: '主发电机与配电盘所在，火灾易引发全舰失电与推进系统瘫痪。',
    anchor: { x: 1, y: 2.6, z: 0 },
    /** 起火点：主配电盘 */
    fireOrigin: { x: 3.0, y: 0.7, z: 4.0 },
    fireSize: { w: 6.0, h: 3.8 },
    bounds: { min: { x: -4, y: 0.4, z: -5.0 }, max: { x: 6, y: 5.0, z: 5.0 } },
    camera: { offset: { x: -8, y: 9, z: 12 } },
    model: '/models/compartments/power-room.glb',
    risk: 'high'
  },
  {
    id: 3,
    name: '灶炉间',
    code: 'E-03',
    fireType: 'galley',
    deck: '上层甲板 · 艏部生活区',
    description: '厨房与餐炊区域，油烟与明火并存，是舰艇日常火灾的高发舱室。',
    anchor: { x: 25.5, y: 6.0, z: 0 },
    /** 起火点：炉灶台面 */
    fireOrigin: { x: 23.9, y: 5.6, z: -4.0 },
    fireSize: { w: 3.8, h: 2.8 },
    bounds: { min: { x: 22, y: 4.6, z: -4.6 }, max: { x: 29, y: 7.6, z: 4.6 } },
    camera: { offset: { x: -2, y: 8, z: 12 } },
    model: '/models/compartments/galley.glb',
    risk: 'medium'
  },
  {
    id: 4,
    name: '士兵住舱',
    code: 'E-04',
    fireType: 'living',
    deck: '上层甲板 · 生活区',
    description: '水手居住与休息舱室，人员密集、可燃物多，火灾直接威胁人员安全。',
    anchor: { x: 14, y: 3.0, z: 0 },
    /** 起火点：上层铺位 */
    fireOrigin: { x: 14.0, y: 1.4, z: 4.2 },
    fireSize: { w: 5.6, h: 3.5 },
    bounds: { min: { x: 8, y: 0.8, z: -5.0 }, max: { x: 20, y: 5.4, z: 5.0 } },
    camera: { offset: { x: 3, y: 9, z: 13 } },
    model: '/models/compartments/berthing.glb',
    risk: 'high'
  },
  {
    id: 5,
    name: '机库',
    code: 'E-05',
    fireType: 'hangar',
    deck: '后甲板 · 机库区',
    description: '直升机库与燃油加注区，紧邻航空燃油管路，是最高等级的火灾危险区。',
    anchor: { x: -17, y: 3.2, z: 0 },
    /** 起火点：航空燃油加注站 */
    fireOrigin: { x: -25.8, y: 0.9, z: -4.6 },
    fireSize: { w: 8.0, h: 4.4 },
    bounds: { min: { x: -28, y: 0.5, z: -5.6 }, max: { x: -6, y: 6.2, z: 5.6 } },
    camera: { offset: { x: 5, y: 12, z: 19 } },
    model: '/models/compartments/hangar.glb',
    risk: 'critical'
  }
]

/** 风险等级字典（顺序即严重程度，由高到低） */
export const RISK_LEVELS = {
  critical: { key: 'critical', label: '危急', color: '#ef4444', order: 4 },
  high: { key: 'high', label: '高', color: '#f97316', order: 3 },
  medium: { key: 'medium', label: '中', color: '#eab308', order: 2 },
  low: { key: 'low', label: '低', color: '#22c55e', order: 1 },
  unknown: { key: 'unknown', label: '未知', color: '#64748b', order: 0 }
}

/* ------------------------------------------------------------------ */
/* 查询辅助                                                             */
/* ------------------------------------------------------------------ */

export const getCompartmentById = id => COMPARTMENTS.find(c => c.id === id) || null

export const getFireType = key => FIRE_TYPES[key] || { key: 'unknown', label: '未知类型', short: '未知', color: '#64748b' }

export const getRiskLevel = key => RISK_LEVELS[key] || RISK_LEVELS.unknown

/** 兼容旧调用方：把任意 fireType 解析为可展示的类型对象 */
export const resolveFireType = type => getFireType(type)

/** 按 GLB 节点名（COMPARTMENT_<id>）反查舱室 */
export const getCompartmentByNodeName = nodeName => {
  const m = /^COMPARTMENT_(\d+)$/.exec(nodeName || '')
  return m ? getCompartmentById(Number(m[1])) : null
}
