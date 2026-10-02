/**
 * PyroSim 后端服务示例
 * 这是一个 Node.js Express 服务器示例，展示如何与 PyroSim 集成
 * 
 * 安装依赖:
 * npm install express cors axios ws
 * 
 * 运行:
 * node pyrosim-backend-example.js
 */

const express = require('express')
const cors = require('cors')
const axios = require('axios')
const WebSocket = require('ws')

const app = express()
const PORT = 8080

// 中间件
app.use(cors())
app.use(express.json())

// 模拟数据存储
const simulationData = new Map()
const fireStates = new Map()
const historicalData = new Map()

// 初始化模拟数据（根据您的实际项目文件）
const initializeSimulationData = () => {
  const compartments = [
    { id: 1, name: '电站间', type: 'power', projectFile: '电站间.psm' },
    { id: 2, name: '机库', type: 'hangar', projectFile: '机库.psm' },
    { id: 3, name: '士兵住舱', type: 'living', projectFile: '士兵住舱.psm' },
    { id: 4, name: '灶炉间', type: 'galley', projectFile: '灶炉间.psm' },
    { id: 5, name: '主机舱', type: 'engine', projectFile: '主机舱.psm' }
  ]

  compartments.forEach(compartment => {
    simulationData.set(compartment.id, {
      compartmentId: compartment.id,
      name: compartment.name,
      type: compartment.type,
      temperature: 25 + Math.random() * 5,
      smokeConcentration: 0,
      oxygenLevel: 20.9,
      coConcentration: 0,
      pressure: 101.3,
      humidity: 50 + Math.random() * 10,
      visibility: 100,
      fireStatus: 'inactive',
      timestamp: Date.now()
    })

    fireStates.set(compartment.id, {
      fireActive: false,
      fireIntensity: 0,
      spreadRate: 0,
      suppressionActive: false,
      lastUpdate: Date.now()
    })

    historicalData.set(compartment.id, [])
  })
}

// 生成模拟数据
const generateSimulationData = (compartmentId) => {
  const baseData = simulationData.get(compartmentId)
  const fireState = fireStates.get(compartmentId)
  
  if (!baseData || !fireState) return null

  const newData = { ...baseData }
  
  if (fireState.fireActive) {
    // 火灾活跃时的数据变化 - 更符合实际情况
    const timeElapsed = (Date.now() - fireState.lastUpdate) / 1000
    // 减缓火灾发展速度：每秒增长0.02而不是0.1
    const intensity = Math.min(fireState.fireIntensity + timeElapsed * 0.02, 1.0)
    
    // 更真实的参数范围
    newData.temperature = 25 + intensity * 150 + Math.random() * 3  // 最高175°C，更合理
    newData.smokeConcentration = intensity * 60 + Math.random() * 2  // 最高60%，减少随机性
    newData.oxygenLevel = Math.max(20.9 - intensity * 3, 16.0)  // 最低16%，更安全
    newData.coConcentration = intensity * 50 + Math.random() * 3  // 最高50ppm，更合理
    newData.visibility = Math.max(100 - intensity * 70, 20)  // 最低20%，保留基本可见度
    newData.fireStatus = 'active'
    
    fireState.fireIntensity = intensity
  } else {
    // 正常状态的数据变化 - 更稳定的基线
    newData.temperature = 25 + Math.random() * 2  // 25-27°C，减少波动
    newData.smokeConcentration = Math.random() * 1  // 0-1%，正常范围
    newData.oxygenLevel = 20.9 + Math.random() * 0.1  // 20.9-21%，稳定
    newData.coConcentration = Math.random() * 2  // 0-2ppm，正常水平
    newData.visibility = 100 - Math.random() * 3  // 97-100%，良好可见度
    newData.fireStatus = 'inactive'
  }
  
  newData.timestamp = Date.now()
  simulationData.set(compartmentId, newData)
  
  // 记录历史数据
  const history = historicalData.get(compartmentId) || []
  history.push({
    timestamp: Date.now(),
    temperature: newData.temperature,
    smokeConcentration: newData.smokeConcentration,
    oxygenLevel: newData.oxygenLevel,
    coConcentration: newData.coConcentration
  })
  
  // 保持最近100个数据点
  if (history.length > 100) {
    history.shift()
  }
  
  historicalData.set(compartmentId, history)
  
  return newData
}

// 获取模拟数据
const getSimulationData = (compartmentId) => {
  return generateSimulationData(compartmentId)
}

// API 路由

// 健康检查
app.get('/api/ping', (req, res) => {
  res.json({ 
    status: 'ok', 
    message: 'PyroSim 后端服务运行正常',
    timestamp: Date.now()
  })
})

// 获取模拟状态
app.get('/api/simulation/status', (req, res) => {
  const activeFires = Array.from(fireStates.values()).filter(state => state.fireActive).length
  const totalCompartments = simulationData.size
  
  res.json({
    status: 'running',
    activeFires,
    totalCompartments,
    timestamp: Date.now()
  })
})

// 获取所有舱室数据
app.get('/api/compartments', (req, res) => {
  const data = Array.from(simulationData.values()).map(compartment => 
    generateSimulationData(compartment.compartmentId)
  )
  
  res.json(data)
})

// 获取特定舱室数据
app.get('/api/compartment/:id', (req, res) => {
  const compartmentId = parseInt(req.params.id)
  const data = generateSimulationData(compartmentId)
  
  if (!data) {
    return res.status(404).json({ 
      error: '舱室不存在',
      compartmentId 
    })
  }
  
  res.json(data)
})

// 控制火灾
app.post('/api/fire/control', (req, res) => {
  const { compartmentId, action, ...params } = req.body
  
  if (!compartmentId || !action) {
    return res.status(400).json({ 
      error: '缺少必要参数',
      required: ['compartmentId', 'action']
    })
  }
  
  const fireState = fireStates.get(compartmentId)
  if (!fireState) {
    return res.status(404).json({ 
      error: '舱室不存在',
      compartmentId 
    })
  }
  
  switch (action) {
  case 'start':
    fireState.fireActive = true
    fireState.fireIntensity = params.intensity || 0.1
    fireState.spreadRate = params.spreadRate || 0.05
    fireState.lastUpdate = Date.now()
    break
      
  case 'stop':
    fireState.fireActive = false
    fireState.fireIntensity = 0
    fireState.spreadRate = 0
    fireState.lastUpdate = Date.now()
    break
      
  case 'reset':
    fireState.fireActive = false
    fireState.fireIntensity = 0
    fireState.spreadRate = 0
    fireState.suppressionActive = false
    fireState.lastUpdate = Date.now()
    break
      
  default:
    return res.status(400).json({ 
      error: '无效的操作类型',
      action,
      validActions: ['start', 'stop', 'reset']
    })
  }
  
  res.json({
    compartmentId,
    action,
    status: fireState.fireActive ? 'active' : 'inactive',
    timestamp: Date.now()
  })
})

// 重置火灾状态
app.post('/api/fire/reset', (req, res) => {
  const { compartmentId } = req.body
  
  if (!compartmentId) {
    return res.status(400).json({ 
      error: '缺少必要参数',
      required: ['compartmentId']
    })
  }
  
  const fireState = fireStates.get(compartmentId)
  const baseData = simulationData.get(compartmentId)
  
  if (!fireState || !baseData) {
    return res.status(404).json({ 
      error: '舱室不存在',
      compartmentId 
    })
  }
  
  // 重置火灾状态
  fireState.fireActive = false
  fireState.fireIntensity = 0
  fireState.spreadRate = 0
  fireState.suppressionActive = false
  fireState.lastUpdate = Date.now()
  
  // 重置模拟数据到初始状态 - 与正常状态一致
  baseData.temperature = 25 + Math.random() * 2  // 25-27°C，稳定范围
  baseData.smokeConcentration = Math.random() * 1  // 0-1%，正常水平
  baseData.oxygenLevel = 20.9 + Math.random() * 0.1  // 20.9-21%，稳定
  baseData.coConcentration = Math.random() * 2  // 0-2ppm，正常水平
  baseData.visibility = 100 - Math.random() * 3  // 97-100%，良好可见度
  baseData.fireStatus = 'inactive'
  baseData.timestamp = Date.now()
  
  res.json({
    code: 200,
    message: 'success',
    data: {
      compartmentId,
      fireStatus: 'inactive',
      temperature: baseData.temperature,
      smokeConcentration: baseData.smokeConcentration,
      oxygenLevel: baseData.oxygenLevel,
      coConcentration: baseData.coConcentration,
      timestamp: Date.now()
    }
  })
})

// 船舶信息接口
app.get('/api/ship/info', (req, res) => {
  res.json({
    id: 'SHIP-001',
    name: '智能船舶监控系统',
    type: '货轮',
    tonnage: 85000,
    buildYear: 2022,
    captain: '张船长',
    crewCount: 25,
    status: '正常',
    location: {
      latitude: 31.2304,
      longitude: 121.4737,
      port: '上海港'
    }
  })
})

// 船舶舱室列表
app.get('/api/ship/compartments', (req, res) => {
  const compartments = [
    { id: 1, name: '电站间', type: 'power', status: '正常', riskLevel: 'low' },
    { id: 2, name: '机库', type: 'hangar', status: '正常', riskLevel: 'medium' },
    { id: 3, name: '士兵住舱', type: 'living', status: '正常', riskLevel: 'low' },
    { id: 4, name: '灶炉间', type: 'galley', status: '正常', riskLevel: 'high' },
    { id: 5, name: '主机舱', type: 'engine', status: '正常', riskLevel: 'high' }
  ]
  res.json(compartments)
})

// 风险分布数据
app.get('/api/ship/risk-distribution', (req, res) => {
  res.json({
    high: 20,
    medium: 30,
    low: 50,
    total: 100,
    lastUpdate: Date.now()
  })
})

// 火灾数据接口
app.get('/api/fire/data', (req, res) => {
  const compartmentId = req.query.compartmentId
  if (!compartmentId) {
    return res.status(400).json({ error: '缺少舱室ID参数' })
  }
  
  const fireState = fireStates.get(parseInt(compartmentId))
  const simulationData = getSimulationData(parseInt(compartmentId))
  
  if (!fireState || !simulationData) {
    return res.status(404).json({ error: '舱室不存在' })
  }
  
  res.json({
    compartmentId: parseInt(compartmentId),
    fireStatus: fireState.fireActive ? 'active' : 'inactive',
    temperature: simulationData.temperature,
    smokeConcentration: simulationData.smokeConcentration,
    oxygenLevel: simulationData.oxygenLevel,
    coConcentration: simulationData.coConcentration,
    intensity: fireState.fireIntensity,
    spreadRate: fireState.spreadRate,
    suppressionActive: fireState.suppressionActive,
    timestamp: fireState.lastUpdate
  })
})

// 警报列表接口
app.get('/api/alerts', (req, res) => {
  const alerts = [
    {
      id: 1,
      level: 'warning',
      title: '温度异常',
      message: '电站间温度超过正常范围',
      compartmentId: 1,
      timestamp: Date.now() - 300000, // 5分钟前
      status: 'active'
    },
    {
      id: 2,
      level: 'info',
      title: '系统检查',
      message: '定期系统检查完成',
      timestamp: Date.now() - 600000, // 10分钟前
      status: 'resolved'
    }
  ]
  res.json(alerts)
})

// 清除警报
app.delete('/api/alerts/:id', (req, res) => {
  const alertId = req.params.id
  res.json({
    success: true,
    message: `警报 ${alertId} 已清除`,
    alertId: parseInt(alertId)
  })
})

// 获取历史数据
app.get('/api/history/:id', (req, res) => {
  const compartmentId = parseInt(req.params.id)
  const timeRange = parseInt(req.query.timeRange) || 60
  
  const history = historicalData.get(compartmentId)
  if (!history) {
    return res.status(404).json({ 
      error: '舱室不存在',
      compartmentId 
    })
  }
  
  // 过滤指定时间范围内的数据
  const cutoffTime = Date.now() - (timeRange * 60 * 1000)
  const filteredHistory = history.filter(point => point.timestamp >= cutoffTime)
  
  res.json(filteredHistory)
})

// 错误处理中间件
app.use((err, req, res, next) => {
  console.error('服务器错误:', err)
  res.status(500).json({ 
    error: '服务器内部错误',
    message: err.message 
  })
})

// 404 处理
app.use((req, res) => {
  res.status(404).json({ 
    error: '接口不存在',
    path: req.path,
    method: req.method 
  })
})

// WebSocket 服务器用于实时数据推送
const wss = new WebSocket.Server({ port: 8081 })

wss.on('connection', (ws) => {
  console.log('WebSocket 客户端已连接')
  
  // 发送欢迎消息
  ws.send(JSON.stringify({
    type: 'welcome',
    message: 'PyroSim 实时数据推送已连接',
    timestamp: Date.now()
  }))
  
  // 定期发送数据更新
  const interval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      const data = Array.from(simulationData.values()).map(compartment => 
        generateSimulationData(compartment.compartmentId)
      )
      
      ws.send(JSON.stringify({
        type: 'dataUpdate',
        data,
        timestamp: Date.now()
      }))
    }
  }, 2000)
  
  ws.on('close', () => {
    console.log('WebSocket 客户端已断开')
    clearInterval(interval)
  })
  
  ws.on('error', (error) => {
    console.error('WebSocket 错误:', error)
    clearInterval(interval)
  })
})

// 启动服务器
app.listen(PORT, () => {
  console.log('PyroSim 后端服务已启动')
  console.log(`HTTP 服务: http://localhost:${PORT}`)
  console.log('WebSocket 服务: ws://localhost:8081')
  
  // 初始化模拟数据
  initializeSimulationData()
  
  console.log('模拟数据已初始化')
  console.log(`已创建 ${simulationData.size} 个舱室的模拟数据`)
})

// 优雅关闭
process.on('SIGINT', () => {
  console.log('\n正在关闭 PyroSim 后端服务...')
  
  wss.close(() => {
    console.log('WebSocket 服务器已关闭')
  })
  
  process.exit(0)
})

module.exports = app

