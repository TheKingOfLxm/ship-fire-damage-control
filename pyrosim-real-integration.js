/**
 * 真实 PyroSim 软件集成后端服务
 * 用于连接和获取真实 PyroSim 软件的数据
 * 
 * 安装依赖:
 * npm install express cors axios ws child_process fs path
 * 
 * 运行:
 * node pyrosim-real-integration.js
 */

const express = require('express')
const cors = require('cors')
const axios = require('axios')
const WebSocket = require('ws')
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')

const app = express()
const PORT = 8080

// 中间件
app.use(cors())
app.use(express.json())

// PyroSim 配置
const PYROSIM_CONFIG = {
  // PyroSim 软件路径（根据实际安装路径修改）
  executablePath: 'D:\\PyroSim.exe',
  // 或者使用命令行版本
  // commandLinePath: 'D:\\pyrosim-cli.exe',
  
  // 项目文件路径（请将您的 .psm 文件放在这个目录下）
  projectPath: './pyrosim-projects',
  
  // 数据输出路径
  dataOutputPath: './pyrosim-output',
  
  // 舱室映射配置（根据您的实际项目文件）
  compartmentMapping: {
    1: { name: '电站间', type: 'power', node: 'POWER_STATION', projectFile: '电站间.psm' },
    2: { name: '机库', type: 'hangar', node: 'HANGAR', projectFile: '机库.psm' },
    3: { name: '士兵住舱', type: 'living', node: 'SOLDIER_CABIN', projectFile: '士兵住舱.psm' },
    4: { name: '灶炉间', type: 'galley', node: 'GALLEY', projectFile: '灶炉间.psm' },
    5: { name: '主机舱', type: 'engine', node: 'MAIN_ENGINE', projectFile: '主机舱.psm' }
  },
  
  // 项目文件列表
  projectFiles: [
    { name: '电站间', file: '电站间.psm', type: 'power' },
    { name: '机库', file: '机库.psm', type: 'hangar' },
    { name: '士兵住舱', file: '士兵住舱.psm', type: 'living' },
    { name: '灶炉间', file: '灶炉间.psm', type: 'galley' },
    { name: '主机舱', file: '主机舱.psm', type: 'engine' }
  ]
}

// 数据存储
const simulationData = new Map()
const fireStates = new Map()
const historicalData = new Map()
let pyrosimProcess = null
let isSimulationRunning = false

// 初始化数据
const initializeData = () => {
  Object.keys(PYROSIM_CONFIG.compartmentMapping).forEach(id => {
    const compartment = PYROSIM_CONFIG.compartmentMapping[id]
    
    simulationData.set(parseInt(id), {
      compartmentId: parseInt(id),
      name: compartment.name,
      type: compartment.type,
      node: compartment.node,
      temperature: 25,
      smokeConcentration: 0,
      oxygenLevel: 20.9,
      coConcentration: 0,
      pressure: 101.3,
      humidity: 50,
      visibility: 100,
      fireStatus: 'inactive',
      timestamp: Date.now()
    })

    fireStates.set(parseInt(id), {
      fireActive: false,
      fireIntensity: 0,
      spreadRate: 0,
      suppressionActive: false,
      lastUpdate: Date.now()
    })

    historicalData.set(parseInt(id), [])
  })
}

// 创建必要的目录
const createDirectories = () => {
  const dirs = [PYROSIM_CONFIG.projectPath, PYROSIM_CONFIG.dataOutputPath]
  
  dirs.forEach(dir => {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true })
      console.log(`创建目录: ${dir}`)
    }
  })
}

// 启动 PyroSim 模拟
const startPyroSimSimulation = async (projectFile) => {
  return new Promise((resolve, reject) => {
    try {
      // 检查 PyroSim 可执行文件是否存在
      if (!fs.existsSync(PYROSIM_CONFIG.executablePath)) {
        throw new Error(`PyroSim 可执行文件不存在: ${PYROSIM_CONFIG.executablePath}`)
      }

      // 启动 PyroSim 进程
      pyrosimProcess = spawn(PYROSIM_CONFIG.executablePath, [
        projectFile,
        '--run-simulation',
        '--output-dir', PYROSIM_CONFIG.dataOutputPath,
        '--data-format', 'json',
        '--update-interval', '2000'  // 每2秒更新一次
      ], {
        cwd: PYROSIM_CONFIG.projectPath,
        stdio: ['pipe', 'pipe', 'pipe']
      })

      pyrosimProcess.stdout.on('data', (data) => {
        console.log('PyroSim 输出:', data.toString())
        
        // 解析 PyroSim 输出
        try {
          const output = data.toString()
          if (output.includes('Simulation started')) {
            isSimulationRunning = true
            resolve(true)
          }
        } catch (error) {
          console.error('解析 PyroSim 输出失败:', error)
        }
      })

      pyrosimProcess.stderr.on('data', (data) => {
        console.error('PyroSim 错误:', data.toString())
      })

      pyrosimProcess.on('close', (code) => {
        console.log(`PyroSim 进程结束，退出码: ${code}`)
        isSimulationRunning = false
        pyrosimProcess = null
      })

      pyrosimProcess.on('error', (error) => {
        console.error('PyroSim 进程错误:', error)
        reject(error)
      })

      // 超时处理
      setTimeout(() => {
        if (!isSimulationRunning) {
          reject(new Error('PyroSim 启动超时'))
        }
      }, 30000)

    } catch (error) {
      reject(error)
    }
  })
}

// 停止 PyroSim 模拟
const stopPyroSimSimulation = () => {
  if (pyrosimProcess) {
    pyrosimProcess.kill('SIGTERM')
    pyrosimProcess = null
    isSimulationRunning = false
    console.log('PyroSim 模拟已停止')
  }
}

// 读取 PyroSim 输出数据
const readPyroSimData = () => {
  try {
    const outputDir = PYROSIM_CONFIG.dataOutputPath
    const files = fs.readdirSync(outputDir)
    
    // 查找最新的数据文件
    const dataFiles = files.filter(file => file.endsWith('.json'))
    if (dataFiles.length === 0) {
      return null
    }

    // 按修改时间排序，获取最新文件
    const latestFile = dataFiles
      .map(file => ({
        name: file,
        time: fs.statSync(path.join(outputDir, file)).mtime
      }))
      .sort((a, b) => b.time - a.time)[0]

    const filePath = path.join(outputDir, latestFile.name)
    const data = JSON.parse(fs.readFileSync(filePath, 'utf8'))
    
    return data
  } catch (error) {
    console.error('读取 PyroSim 数据失败:', error)
    return null
  }
}

// 解析 PyroSim 数据格式
const parsePyroSimData = (rawData) => {
  if (!rawData || !rawData.nodes) {
    return null
  }

  const parsedData = new Map()

  // 根据节点映射解析数据
  Object.keys(PYROSIM_CONFIG.compartmentMapping).forEach(id => {
    const compartment = PYROSIM_CONFIG.compartmentMapping[id]
    const nodeData = rawData.nodes[compartment.node]

    if (nodeData) {
      const compartmentData = {
        compartmentId: parseInt(id),
        name: compartment.name,
        type: compartment.type,
        node: compartment.node,
        temperature: nodeData.temperature || 25,
        smokeConcentration: nodeData.smoke_concentration || 0,
        oxygenLevel: nodeData.oxygen_level || 20.9,
        coConcentration: nodeData.co_concentration || 0,
        pressure: nodeData.pressure || 101.3,
        humidity: nodeData.humidity || 50,
        visibility: nodeData.visibility || 100,
        fireStatus: nodeData.fire_status || 'inactive',
        timestamp: Date.now()
      }

      parsedData.set(parseInt(id), compartmentData)

      // 更新历史数据
      const history = historicalData.get(parseInt(id)) || []
      history.push({
        timestamp: Date.now(),
        temperature: compartmentData.temperature,
        smokeConcentration: compartmentData.smokeConcentration,
        oxygenLevel: compartmentData.oxygenLevel,
        coConcentration: compartmentData.coConcentration
      })

      // 保持最近100个数据点
      if (history.length > 100) {
        history.shift()
      }

      historicalData.set(parseInt(id), history)
    }
  })

  return parsedData
}

// 更新模拟数据
const updateSimulationData = () => {
  if (!isSimulationRunning) {
    return
  }

  try {
    const rawData = readPyroSimData()
    if (rawData) {
      const parsedData = parsePyroSimData(rawData)
      if (parsedData) {
        // 更新数据存储
        parsedData.forEach((data, id) => {
          simulationData.set(id, data)
        })
        
        console.log('PyroSim 数据已更新')
      }
    }
  } catch (error) {
    console.error('更新模拟数据失败:', error)
  }
}

// API 路由

// 健康检查
app.get('/api/ping', (req, res) => {
  res.json({
    status: 'ok',
    message: 'PyroSim 真实集成服务运行正常',
    simulationRunning: isSimulationRunning,
    timestamp: Date.now()
  })
})

// 获取模拟状态
app.get('/api/simulation/status', (req, res) => {
  const activeFires = Array.from(fireStates.values()).filter(state => state.fireActive).length
  const totalCompartments = simulationData.size

  res.json({
    status: isSimulationRunning ? 'running' : 'stopped',
    activeFires,
    totalCompartments,
    pyrosimProcess: pyrosimProcess ? 'running' : 'stopped',
    timestamp: Date.now()
  })
})

// 启动 PyroSim 模拟
app.post('/api/simulation/start', async (req, res) => {
  try {
    const { projectFile } = req.body
    
    if (!projectFile) {
      return res.status(400).json({
        error: '缺少项目文件参数',
        required: ['projectFile']
      })
    }

    const success = await startPyroSimSimulation(projectFile)
    
    if (success) {
      res.json({
        message: 'PyroSim 模拟已启动',
        projectFile,
        timestamp: Date.now()
      })
    } else {
      res.status(500).json({
        error: 'PyroSim 模拟启动失败'
      })
    }
  } catch (error) {
    console.error('启动模拟失败:', error)
    res.status(500).json({
      error: '启动模拟失败',
      message: error.message
    })
  }
})

// 停止 PyroSim 模拟
app.post('/api/simulation/stop', (req, res) => {
  try {
    stopPyroSimSimulation()
    res.json({
      message: 'PyroSim 模拟已停止',
      timestamp: Date.now()
    })
  } catch (error) {
    console.error('停止模拟失败:', error)
    res.status(500).json({
      error: '停止模拟失败',
      message: error.message
    })
  }
})

// 获取所有舱室数据
app.get('/api/compartments', (req, res) => {
  // 更新数据
  updateSimulationData()
  
  const data = Array.from(simulationData.values())
  res.json(data)
})

// 获取特定舱室数据
app.get('/api/compartment/:id', (req, res) => {
  const compartmentId = parseInt(req.params.id)
  
  // 更新数据
  updateSimulationData()
  
  const data = simulationData.get(compartmentId)
  if (!data) {
    return res.status(404).json({
      error: '舱室不存在',
      compartmentId
    })
  }
  
  res.json(data)
})

// 控制火灾（通过 PyroSim 脚本）
app.post('/api/fire/control', async (req, res) => {
  const { compartmentId, action, ...params } = req.body
  
  if (!compartmentId || !action) {
    return res.status(400).json({
      error: '缺少必要参数',
      required: ['compartmentId', 'action']
    })
  }
  
  const compartment = PYROSIM_CONFIG.compartmentMapping[compartmentId]
  if (!compartment) {
    return res.status(404).json({
      error: '舱室不存在',
      compartmentId
    })
  }

  try {
    // 生成 PyroSim 控制脚本
    const scriptContent = generatePyroSimScript(compartmentId, action, params)
    const scriptPath = path.join(PYROSIM_CONFIG.projectPath, `control_${compartmentId}_${action}.psm`)
    
    // 写入脚本文件
    fs.writeFileSync(scriptPath, scriptContent)
    
    // 执行脚本（如果 PyroSim 支持命令行执行）
    if (fs.existsSync(PYROSIM_CONFIG.commandLinePath)) {
      const scriptProcess = spawn(PYROSIM_CONFIG.commandLinePath, [
        '--execute-script',
        scriptPath
      ])
      
      scriptProcess.on('close', (code) => {
        console.log(`控制脚本执行完成，退出码: ${code}`)
      })
    }
    
    // 更新本地状态
    const fireState = fireStates.get(compartmentId)
    if (fireState) {
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
      }
    }
    
    res.json({
      compartmentId,
      action,
      status: fireState?.fireActive ? 'active' : 'inactive',
      timestamp: Date.now()
    })
    
  } catch (error) {
    console.error('控制火灾失败:', error)
    res.status(500).json({
      error: '控制火灾失败',
      message: error.message
    })
  }
})

// 生成 PyroSim 控制脚本
const generatePyroSimScript = (compartmentId, action, params) => {
  const compartment = PYROSIM_CONFIG.compartmentMapping[compartmentId]
  
  let script = `# PyroSim 控制脚本
# 舱室: ${compartment.name} (${compartment.node})
# 操作: ${action}
# 时间: ${new Date().toISOString()}

`

  switch (action) {
  case 'start':
    script += `# 启动火灾
SET_HEAT_SOURCE ${compartment.node} ${params.intensity || 0.5}
SET_FIRE_SPREAD_RATE ${compartment.node} ${params.spreadRate || 0.1}
ACTIVATE_FIRE ${compartment.node}
`
    break
      
  case 'stop':
    script += `# 停止火灾
DEACTIVATE_FIRE ${compartment.node}
CLEAR_HEAT_SOURCE ${compartment.node}
`
    break
      
  case 'reset':
    script += `# 重置状态
DEACTIVATE_FIRE ${compartment.node}
CLEAR_HEAT_SOURCE ${compartment.node}
RESET_NODE ${compartment.node}
`
    break
  }
  
  return script
}

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

// 获取项目列表
app.get('/api/projects', (req, res) => {
  try {
    const projectsDir = PYROSIM_CONFIG.projectPath
    const files = fs.readdirSync(projectsDir)
    const projects = files
      .filter(file => file.endsWith('.psm'))
      .map(file => ({
        name: file,
        path: path.join(projectsDir, file),
        size: fs.statSync(path.join(projectsDir, file)).size,
        modified: fs.statSync(path.join(projectsDir, file)).mtime
      }))
    
    res.json(projects)
  } catch (error) {
    console.error('获取项目列表失败:', error)
    res.status(500).json({
      error: '获取项目列表失败',
      message: error.message
    })
  }
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
    message: 'PyroSim 真实数据推送已连接',
    timestamp: Date.now()
  }))
  
  // 定期发送数据更新
  const interval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN && isSimulationRunning) {
      // 更新数据
      updateSimulationData()
      
      const data = Array.from(simulationData.values())
      
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
  console.log('PyroSim 真实集成服务已启动')
  console.log(`HTTP 服务: http://localhost:${PORT}`)
  console.log('WebSocket 服务: ws://localhost:8081')
  
  // 创建必要目录
  createDirectories()
  
  // 初始化数据
  initializeData()
  
  console.log('数据已初始化')
  console.log(`已创建 ${simulationData.size} 个舱室的数据结构`)
  console.log(`PyroSim 可执行文件路径: ${PYROSIM_CONFIG.executablePath}`)
  console.log(`项目文件路径: ${PYROSIM_CONFIG.projectPath}`)
  console.log(`数据输出路径: ${PYROSIM_CONFIG.dataOutputPath}`)
})

// 优雅关闭
process.on('SIGINT', () => {
  console.log('\n正在关闭 PyroSim 真实集成服务...')
  
  // 停止 PyroSim 模拟
  stopPyroSimSimulation()
  
  // 关闭 WebSocket 服务器
  wss.close(() => {
    console.log('WebSocket 服务器已关闭')
  })
  
  process.exit(0)
})

module.exports = app

