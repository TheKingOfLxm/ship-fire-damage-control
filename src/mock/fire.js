import Mock from 'mockjs'
import { shipInfo } from './ship' // 引入 shipInfo

// 存储每个舱室的火灾激活状态（动态初始化，支持所有舱室）
const fireActiveStates = {}
const fireStartTimes = {}
const coolingStartTimes = {}
const lastSimulatedValues = {}

// 初始化函数：确保舱室状态存在
const ensureCompartmentState = (compartmentId) => {
  if (fireActiveStates[compartmentId] === undefined) {
    fireActiveStates[compartmentId] = false
    fireStartTimes[compartmentId] = null
    coolingStartTimes[compartmentId] = null
    lastSimulatedValues[compartmentId] = null
  }
}

// === PyroSim 风格火灾曲线辅助函数 ===
const pyroSimBaseCurves = {
  temperature: [
    { time: 0, value: 0 },
    { time: 15, value: 0.01 },
    { time: 30, value: 0.03 },
    { time: 60, value: 0.12 },
    { time: 120, value: 0.35 },
    { time: 240, value: 0.68 },
    { time: 360, value: 0.9 },
    { time: 600, value: 1 },
    { time: 900, value: 0.87 },
    { time: 1200, value: 0.62 },
    { time: 1500, value: 0.35 }
  ],
  co: [
    { time: 0, value: 0 },
    { time: 60, value: 0.06 },
    { time: 180, value: 0.28 },
    { time: 360, value: 0.65 },
    { time: 600, value: 1 },
    { time: 840, value: 0.92 },
    { time: 1080, value: 0.6 },
    { time: 1380, value: 0.3 },
    { time: 1680, value: 0.12 }
  ]
}

const evaluatePyroSimCurve = (elapsedTime, curve, timeScale = 1) => {
  if (!curve || curve.length === 0) return 0
  const adjustedTime = elapsedTime / Math.max(0.1, timeScale)

  if (adjustedTime <= curve[0].time) return curve[0].value
  for (let i = 1; i < curve.length; i++) {
    const currentPoint = curve[i]
    const prevPoint = curve[i - 1]
    if (adjustedTime <= currentPoint.time) {
      const range = currentPoint.time - prevPoint.time
      const progress = (adjustedTime - prevPoint.time) / (range || 1)
      return prevPoint.value + (currentPoint.value - prevPoint.value) * progress
    }
  }
  return curve[curve.length - 1].value
}

const getPyroSimTimeScale = (riskLevel = 'medium') => {
  if (riskLevel === 'high') return 0.75
  if (riskLevel === 'low') return 1.25
  return 1
}

const smoothApproach = (current, target, factor = 0.35) => {
  return current + (target - current) * Math.min(1, Math.max(0, factor))
}

const applyJitter = (value, span) => {
  return value + (Math.random() - 0.5) * span
}

const clamp = (value, min, max) => Math.max(min, Math.min(max, value))

const computeIntensityModifier = (analysis) => {
  const risk = analysis.risk || 0.4
  const ventilationFactor = analysis.ventilation?.factor ?? 0.5
  const suppressionFactor = analysis.suppression?.activated
    ? 1 - (analysis.suppression.factor || 0)
    : 1

  const ventilationImpact = 0.8 + (1 - ventilationFactor) * 0.4
  const base = 0.6 + risk * 0.5
  const modifier = base * ventilationImpact * suppressionFactor

  return clamp(modifier, 0.4, 1.25)
}


// 大模型火灾模拟数据生成器 - 基于舱室参数智能分析
const generateFireData = (compartmentId, elapsedTime, currentData) => {
  const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
  if (!compartment) {
    console.warn(`generateFireData: Compartment ${compartmentId} not found`)
    return currentData
  }

  // 大模型分析舱室参数
  const analysis = analyzeCompartmentForFire(compartment, elapsedTime)
  
  console.log(`🧠 大模型分析舱室 ${compartment.name}:`, {
    riskLevel: compartment.riskLevel,
    area: compartment.area,
    equipment: compartment.equipment,
    fireSuppression: compartment.fireSuppression,
    analysis: analysis
  })

  // 基于分析结果生成火灾数据
  const fireData = generateRealisticFireData(compartment, analysis, elapsedTime, currentData)
  
  console.log('🔥 生成火灾数据:', fireData)
  return fireData
}

// 大模型舱室火灾分析
const analyzeCompartmentForFire = (compartment, elapsedTime) => {
  const { name, riskLevel, area, equipment, fireSuppression, sensors } = compartment
  
  // 火灾发展阶段分析
  const firePhase = analyzeFirePhase(elapsedTime)
  
  // 舱室火灾风险分析
  const fireRisk = analyzeFireRisk(riskLevel, equipment, area, elapsedTime)
  
  // 通风条件分析
  const ventilation = analyzeVentilation(area, sensors)
  
  // 灭火系统影响分析
  const suppressionEffect = analyzeSuppressionEffect(fireSuppression, elapsedTime)
  
  return {
    phase: firePhase,
    risk: fireRisk,
    ventilation: ventilation,
    suppression: suppressionEffect,
    compartmentType: name
  }
}

// 火灾阶段分析
const analyzeFirePhase = (elapsedTime) => {
  if (elapsedTime < 60) return 'initial'      // 初期：0-1分钟
  if (elapsedTime < 300) return 'growth'      // 发展期：1-5分钟
  if (elapsedTime < 900) return 'fully_developed' // 充分发展期：5-15分钟
  return 'decay'                              // 衰减期：15分钟+
}

// 舱室火灾风险分析
const analyzeFireRisk = (riskLevel, equipment, area, elapsedTime) => {
  let riskScore = 0
  
  // 基础风险等级
  switch (riskLevel) {
  case 'high': riskScore += 0.8; break
  case 'medium': riskScore += 0.5; break
  case 'low': riskScore += 0.2; break
  }
  
  // 设备风险分析
  const highRiskEquipment = ['主发动机', '发电机', '燃油系统', '锅炉', '燃油泵']
  const mediumRiskEquipment = ['空压机', '冷却系统', '燃油加热器']
  
  equipment.forEach(eq => {
    if (highRiskEquipment.some(he => eq.includes(he))) riskScore += 0.3
    else if (mediumRiskEquipment.some(me => eq.includes(me))) riskScore += 0.15
  })
  
  // 舱室面积影响（大面积火灾蔓延更快）
  if (area > 1000) riskScore += 0.2
  else if (area > 500) riskScore += 0.1
  
  // 时间影响：火灾随时间发展，风险逐渐增加
  // 调整时间因子，让火灾更缓慢发展（120秒内达到最大风险，更符合真实火灾发展速度）
  const timeFactor = Math.min(1, elapsedTime / 120) // 120秒（2分钟）内达到最大风险
  // 初始风险为基础风险的30%，120秒后达到100%
  riskScore = riskScore * (0.3 + timeFactor * 0.7) // 初始风险为基础风险的30%，逐渐增长到100%
  
  return Math.min(1, riskScore)
}

// 通风条件分析
const analyzeVentilation = (area, sensors) => {
  // 基于传感器数量和舱室面积判断通风条件
  const sensorDensity = sensors.temperature + sensors.smoke + sensors.oxygen
  const ventilationFactor = Math.min(1, sensorDensity / (area / 100))
  
  return {
    factor: ventilationFactor,
    description: ventilationFactor > 0.7 ? '良好' : ventilationFactor > 0.4 ? '一般' : '较差'
  }
}

// 灭火系统影响分析
const analyzeSuppressionEffect = (fireSuppression, elapsedTime) => {
  if (!fireSuppression || fireSuppression.length === 0) return { factor: 0 }
  
  let effectFactor = 0
  fireSuppression.forEach(system => {
    if (system.includes('CO2')) effectFactor += 0.4
    else if (system.includes('泡沫')) effectFactor += 0.3
    else if (system.includes('水雾')) effectFactor += 0.2
    else effectFactor += 0.1
  })
  
  // 灭火系统需要时间启动（假设2分钟后生效）
  const activationDelay = Math.max(0, (elapsedTime - 120) / 180) // 3分钟内完全生效
  
  return {
    factor: Math.min(0.8, effectFactor * activationDelay),
    activated: elapsedTime > 120
  }
}

// 基于分析结果生成真实的火灾数据
const generateRealisticFireData = (compartment, analysis, elapsedTime, currentData) => {
  const { phase, compartmentType } = analysis
  
  // 基础环境数据
  const baseTemp = compartment.baseTemp
  const baseSmoke = compartment.baseSmoke
  const baseOxygen = compartment.baseOxygen
  const baseCO = compartment.baseCO
  
  // 计算最大可能值（基于舱室类型）
  const maxValues = calculateMaxValues(compartmentType)
  
  // 生成数据 - 从当前温度继续上升，而不是从基础温度重新开始
  // 使用当前数据作为起点，这样可以在已有温度基础上继续升温
  const currentTemp = currentData?.temperature || baseTemp
  const currentSmoke = currentData?.smoke || baseSmoke
  const currentOxygen = currentData?.oxygen || baseOxygen
  const currentCO = currentData?.co || baseCO
  
  // 根据 PyroSim 火灾曲线计算归一化强度
  const timeScale = getPyroSimTimeScale(compartment.riskLevel)
  const intensityModifier = computeIntensityModifier(analysis)
  const pyrosimTempNormalized = clamp(
    evaluatePyroSimCurve(elapsedTime, pyroSimBaseCurves.temperature, timeScale) * intensityModifier,
    0,
    1
  )
  const pyrosimCONormalized = clamp(
    evaluatePyroSimCurve(elapsedTime, pyroSimBaseCurves.co, timeScale) * (0.85 + intensityModifier * 0.25),
    0,
    1
  )

  // 派生其他量的趋势（保持与 PyroSim 同步）
  const pyrosimSmokeNormalized = clamp(pyrosimTempNormalized * 0.65 + pyrosimCONormalized * 0.35, 0, 1)
  const pyrosimOxygenNormalized = clamp(pyrosimTempNormalized * 0.82, 0, 1)

  // 计算目标值
  const targetTemp = baseTemp + (maxValues.temp - baseTemp) * pyrosimTempNormalized
  const targetSmoke = baseSmoke + (maxValues.smoke - baseSmoke) * pyrosimSmokeNormalized
  const targetOxygen = baseOxygen - (baseOxygen - maxValues.oxygen) * pyrosimOxygenNormalized
  const targetCO = baseCO + (maxValues.co - baseCO) * pyrosimCONormalized

  // 平滑逼近 + PyroSim 风格的微扰动
  const temperature = clamp(
    applyJitter(
      smoothApproach(currentTemp, targetTemp, 0.4),
      (maxValues.temp - baseTemp) * 0.015
    ),
    baseTemp,
    maxValues.temp
  )
  const smoke = clamp(
    applyJitter(
      smoothApproach(currentSmoke, targetSmoke, 0.45),
      (maxValues.smoke - baseSmoke) * 0.02
    ),
    baseSmoke,
    maxValues.smoke
  )
  const oxygen = clamp(
    applyJitter(
      smoothApproach(currentOxygen, targetOxygen, 0.3),
      (baseOxygen - maxValues.oxygen) * 0.01
    ),
    maxValues.oxygen,
    baseOxygen
  )
  const co = clamp(
    applyJitter(
      smoothApproach(currentCO, targetCO, 0.5),
      (maxValues.co - baseCO) * 0.03
    ),
    baseCO,
    maxValues.co
  )
  
  const result = {
    temperature,
    smoke,
    oxygen,
    co
  }
  
  // 添加调试日志
  console.log('🔥 火灾数据计算详情:', {
    compartment: compartment.name,
    elapsedTime: elapsedTime.toFixed(1) + 's',
    phase: phase,
    currentTemp: currentTemp.toFixed(1),
    baseTemp,
    targetTemp: targetTemp.toFixed(1),
    maxTemp: maxValues.temp,
    pyrosimTempNormalized: pyrosimTempNormalized.toFixed(3),
    pyrosimCONormalized: pyrosimCONormalized.toFixed(3),
    intensityModifier: intensityModifier.toFixed(3),
    calculatedTemp: temperature.toFixed(1),
    finalTemp: result.temperature.toFixed(1)
  })
  
  return result
}

// 根据舱室类型计算最大可能值
const calculateMaxValues = (compartmentType) => {
  if (compartmentType.includes('机舱') || compartmentType.includes('主机')) {
    return { temp: 800, smoke: 800, oxygen: 15, co: 300 } // 机舱火灾最严重
  } else if (compartmentType.includes('货舱')) {
    return { temp: 600, smoke: 900, oxygen: 16, co: 200 } // 货舱烟雾最严重
  } else if (compartmentType.includes('燃油舱')) {
    return { temp: 700, smoke: 700, oxygen: 14, co: 250 } // 燃油舱危险性高
  } else if (compartmentType.includes('驾驶室') || compartmentType.includes('生活区')) {
    return { temp: 400, smoke: 400, oxygen: 18, co: 100 } // 居住区域相对安全
  } else {
    return { temp: 500, smoke: 500, oxygen: 17, co: 150 } // 默认值
  }
}

// 降温模拟数据生成器（使用大模型逻辑）
const generateCoolingData = (compartmentId, elapsedTime, initialData) => {
  const coolingRate = 0.03 // 每秒降温百分比（更真实的物理降温）

  // 使用舱室的基础温度作为目标基准值，而不是硬编码
  const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
  const targetBaseTemp = compartment?.baseTemp || 48
  const targetBaseSmoke = compartment?.baseSmoke || 20
  const targetBaseOxygen = compartment?.baseOxygen || 20.9
  const targetBaseCO = compartment?.baseCO || 5

  // 定义初始数据值，即使 initialData 不完整也提供全面的默认值
  const startTemp =
    initialData && initialData.temperature !== undefined ? initialData.temperature : targetBaseTemp
  const startSmoke =
    initialData && initialData.smoke !== undefined ? initialData.smoke : targetBaseSmoke
  const startOxygen =
    initialData && initialData.oxygen !== undefined ? initialData.oxygen : targetBaseOxygen
  const startCO = initialData && initialData.co !== undefined ? initialData.co : targetBaseCO

  // 大模型降温物理模型：非线性降温曲线（更符合物理规律）
  
  // 温度：指数衰减到环境温度
  const tempDiff = startTemp - targetBaseTemp
  const temperature = targetBaseTemp + tempDiff * Math.exp(-elapsedTime * coolingRate * 1.2)
  
  // 烟雾：快速扩散和沉降
  const smokeDiff = startSmoke - targetBaseSmoke
  const smoke = targetBaseSmoke + smokeDiff * Math.exp(-elapsedTime * coolingRate * 1.5)
  
  // 氧气：逐渐恢复，但有通风限制
  const oxygenDiff = targetBaseOxygen - startOxygen
  const oxygen = startOxygen + oxygenDiff * (1 - Math.exp(-elapsedTime * coolingRate * 0.8))
  
  // CO：逐渐稀释，但有残留
  const coDiff = startCO - targetBaseCO
  const co = targetBaseCO + coDiff * Math.exp(-elapsedTime * coolingRate * 0.6)

  return {
    temperature: Math.max(targetBaseTemp, temperature),
    smoke: Math.max(targetBaseSmoke, smoke),
    oxygen: Math.min(targetBaseOxygen, oxygen),
    co: Math.max(targetBaseCO, co)
  }
}

// 获取实时火灾数据
Mock.mock(/\/api\/fire\/data\?compartmentId=\d+/, 'get', options => {
  const compartmentId = parseInt(options.url.match(/compartmentId=(\d+)/)[1])
  ensureCompartmentState(compartmentId) // 确保状态初始化
  const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
  console.log(`🔥 Mock fire/data 被调用: compartmentId=${compartmentId}, fireActive=${fireActiveStates[compartmentId]}, fireStartTime=${fireStartTimes[compartmentId]}`)

  // 如果火灾激活
  if (fireActiveStates[compartmentId] && fireStartTimes[compartmentId] !== null) {
    const elapsedTime = (Date.now() - fireStartTimes[compartmentId]) / 1000
    // 使用lastSimulatedValues作为升温起点，如果第一次开启则用基础数据
    const currentBaseData = lastSimulatedValues[compartmentId] || {
      temperature: compartment.baseTemp,
      smoke: compartment.baseSmoke,
      oxygen: compartment.baseOxygen,
      co: compartment.baseCO
    }
    
    console.log(`🔥 火灾激活状态，生成火灾数据: elapsedTime=${elapsedTime.toFixed(1)}s, currentBaseTemp=${currentBaseData.temperature.toFixed(1)}°C`)
    
    const dataPoint = generateFireData(compartmentId, elapsedTime, currentBaseData)
    // 记录当前活跃状态下的最新数值，便于 stop 时无缝衔接为降温起点
    lastSimulatedValues[compartmentId] = dataPoint
    const result = {
      code: 200,
      data: {
        ...dataPoint,
        fireStatus: 'active'
      },
      message: 'success'
    }
    console.log('🔥 返回火灾激活数据:', result.data)
    return result
  }
  // 如果处于降温阶段
  else if (
    coolingStartTimes[compartmentId] !== null &&
    lastSimulatedValues[compartmentId] !== null
  ) {
    const elapsedTime = (Date.now() - coolingStartTimes[compartmentId]) / 1000
    const cooledData = generateCoolingData(
      compartmentId,
      elapsedTime,
      lastSimulatedValues[compartmentId]
    )

    // 如果已降到接近基础值，停止降温，返回基础数据
    const threshold = 0.5 // 接近基础值的阈值
    if (
      Math.abs(cooledData.temperature - compartment.baseTemp) < threshold &&
      Math.abs(cooledData.smoke - compartment.baseSmoke) < threshold &&
      Math.abs(cooledData.oxygen - compartment.baseOxygen) < threshold &&
      Math.abs(cooledData.co - compartment.baseCO) < threshold
    ) {
      coolingStartTimes[compartmentId] = null
      lastSimulatedValues[compartmentId] = null
      return {
        code: 200,
        data: {
          temperature: compartment.baseTemp,
          smoke: compartment.baseSmoke,
          oxygen: compartment.baseOxygen,
          co: compartment.baseCO,
          fireStatus: 'inactive'
        },
        message: 'success'
      }
    }

    // 记录当前降温值，便于随时“从当前值重新点火”
    lastSimulatedValues[compartmentId] = cooledData

    return {
      code: 200,
      data: {
        ...cooledData,
        fireStatus: 'cooling'
      },
      message: 'success'
    }
  }
  // 默认返回舱室基础数据（未起火且不在降温阶段）
  else {
    const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
    return {
      code: 200,
      data: {
        temperature: compartment?.baseTemp || 48,
        smoke: compartment?.baseSmoke || 20,
        oxygen: compartment?.baseOxygen || 20.9,
        co: compartment?.baseCO || 5,
        fireStatus: 'inactive'
      },
      message: 'success'
    }
  }
})

// 获取历史火灾数据
Mock.mock(/\/api\/fire\/history\?compartmentId=\d+/, 'get', options => {
  const compartmentId = parseInt(options.url.match(/compartmentId=(\d+)/)[1])
  const historyData = []

  // 生成最近6个时间点的数据
  for (let i = 0; i < 6; i++) {
    const elapsedTime = i * 20 // 每20秒一个数据点
    // 历史数据暂时不考虑实时火灾状态，简单模拟
    const baseData = shipInfo.compartments.find(c => c.id === compartmentId)
    historyData.push({
      time: new Date(Date.now() - (6 - i) * 20000).toLocaleTimeString(),
      ...generateFireData(compartmentId, elapsedTime, {
        temperature: baseData.baseTemp,
        smoke: baseData.baseSmoke,
        oxygen: baseData.baseOxygen,
        co: baseData.baseCO
      })
    })
  }

  return {
    code: 200,
    data: historyData,
    message: 'success'
  }
})

// 控制火灾状态
Mock.mock(/\/api\/fire\/control/, 'post', options => {
  const { compartmentId, action } = JSON.parse(options.body)
  ensureCompartmentState(compartmentId) // 确保状态初始化
  // const currentCompartment = shipInfo.compartments.find(c => c.id === compartmentId); // 移除此行，避免 linter 错误

  if (action === 'query') {
    return {
      code: 200,
      data: {
        compartmentId,
        status: fireActiveStates[compartmentId] ? 'active' : 'inactive',
        timestamp: new Date().toISOString()
      },
      message: 'success'
    }
  }

  if (action === 'start') {
    console.log(`🔥 Start action: compartmentId=${compartmentId}, currentFireActive=${fireActiveStates[compartmentId]}, coolingActive=${coolingStartTimes[compartmentId] !== null}`)
    
    // 如果正在降温，先计算当前降温后的值
    if (coolingStartTimes[compartmentId] !== null && lastSimulatedValues[compartmentId] !== null) {
      const elapsedCooling = (Date.now() - coolingStartTimes[compartmentId]) / 1000
      const currentCooled = generateCoolingData(
        compartmentId,
        elapsedCooling,
        lastSimulatedValues[compartmentId]
      )
      lastSimulatedValues[compartmentId] = currentCooled
      console.log('🔥 从降温状态重新点火，当前温度:', currentCooled.temperature.toFixed(1))
    }
    
    // 如果此前没有记录过值，则使用舱室的基础温度作为起点
    const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
    if (!lastSimulatedValues[compartmentId]) {
      if (compartment) {
        lastSimulatedValues[compartmentId] = {
          temperature: compartment.baseTemp,
          smoke: compartment.baseSmoke,
          oxygen: compartment.baseOxygen,
          co: compartment.baseCO
        }
        console.log(`🔥 首次点火，使用舱室基础值: 温度=${compartment.baseTemp}°C, 烟雾=${compartment.baseSmoke}, 氧气=${compartment.baseOxygen}%, CO=${compartment.baseCO}ppm`)
      } else {
        // 如果舱室不存在，使用通用基线
        lastSimulatedValues[compartmentId] = { temperature: 48, smoke: 20, oxygen: 20.9, co: 5 }
        console.log('🔥 首次点火，舱室不存在，使用通用基线:', lastSimulatedValues[compartmentId])
      }
    }
    
    // 始终更新开始时间（即使已经激活，也重新开始计时，避免重复点击导致的时间混乱）
    fireStartTimes[compartmentId] = Date.now()
    coolingStartTimes[compartmentId] = null // 结束降温阶段
    fireActiveStates[compartmentId] = true
    
    console.log(`🔥 火灾已启动: compartmentId=${compartmentId}, startTime=${fireStartTimes[compartmentId]}, initialValues=`, lastSimulatedValues[compartmentId])
  } else if (action === 'stop') {
    if (fireActiveStates[compartmentId]) {
      // 只有在激活时才记录结束时间
      const elapsedTimeAtStop = (Date.now() - fireStartTimes[compartmentId]) / 1000
      // 以最近一次活跃时记录的值作为起点，确保从当前读数进入降温
      const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
      const baseForCooling = lastSimulatedValues[compartmentId] || (compartment ? {
        temperature: compartment.baseTemp,
        smoke: compartment.baseSmoke,
        oxygen: compartment.baseOxygen,
        co: compartment.baseCO
      } : { temperature: 48, smoke: 20, oxygen: 20.9, co: 5 })

      // 用 active 阶段的最近值推进到当前时刻，得到更贴近的“停止瞬间值”
      const simulatedDataAtStop = generateFireData(
        compartmentId,
        elapsedTimeAtStop,
        baseForCooling
      )

      lastSimulatedValues[compartmentId] = simulatedDataAtStop
      coolingStartTimes[compartmentId] = Date.now()
    }
    fireActiveStates[compartmentId] = false
    fireStartTimes[compartmentId] = null // 清除火灾开始时间
  }

  return {
    code: 200,
    data: {
      compartmentId,
      action,
      status: fireActiveStates[compartmentId] ? 'active' : 'inactive',
      timestamp: new Date().toISOString()
    },
    message: 'success'
  }
})

// 重置到初始数据
Mock.mock(/\/api\/fire\/reset/, 'post', options => {
  const { compartmentId } = JSON.parse(options.body)
  ensureCompartmentState(compartmentId) // 确保状态初始化
  // 将该舱室相关状态全部还原
  fireActiveStates[compartmentId] = false
  fireStartTimes[compartmentId] = null
  coolingStartTimes[compartmentId] = null
  lastSimulatedValues[compartmentId] = null

  const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
  const baseline = compartment ? {
    temperature: compartment.baseTemp,
    smoke: compartment.baseSmoke,
    oxygen: compartment.baseOxygen,
    co: compartment.baseCO
  } : { 
    temperature: 48, 
    smoke: 20, 
    oxygen: 20.9, 
    co: 5 
  }
  
  console.log(`🔥 重置舱室 ${compartmentId}: 使用基础值 - 温度=${baseline.temperature}°C, 烟雾=${baseline.smoke}, 氧气=${baseline.oxygen}%, CO=${baseline.co}ppm`)

  return {
    code: 200,
    data: baseline,
    message: 'reset success'
  }
})
