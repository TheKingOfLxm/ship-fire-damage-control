/**
 * PyroSim API 接口
 * 提供与 PyroSim 后端交互的 API 方法
 */

import pyrosimService from '../services/pyrosimService.js'

// PyroSim API 接口
export const pyroAPI = {
  // 连接管理
  async connect(config) {
    return await pyrosimService.connect(config)
  },

  async disconnect() {
    pyrosimService.disconnect()
  },

  async getConnectionStatus() {
    return pyrosimService.getConnectionStatus()
  },

  // 模拟控制
  async getSimulationStatus() {
    return await pyrosimService.getSimulationStatus()
  },

  // 舱室数据
  async getCompartmentData(compartmentId) {
    return await pyrosimService.getCompartmentData(compartmentId)
  },

  async getAllCompartmentsData() {
    return await pyrosimService.getAllCompartmentsData()
  },

  // 火灾控制
  async controlFire(compartmentId, action, params = {}) {
    return await pyrosimService.controlFire(compartmentId, action, params)
  },

  // 历史数据
  async getHistoricalData(compartmentId, timeRange = 60) {
    return await pyrosimService.getHistoricalData(compartmentId, timeRange)
  },

  // 数据轮询
  startDataPolling(interval = 2000) {
    pyrosimService.startDataPolling(interval)
  },

  stopDataPolling() {
    pyrosimService.stopDataPolling()
  },

  // 缓存数据
  getCachedData(compartmentId) {
    return pyrosimService.getCachedData(compartmentId)
  },

  clearCache() {
    pyrosimService.clearCache()
  },

  // 事件监听
  on(event, callback) {
    pyrosimService.on(event, callback)
  },

  off(event, callback) {
    pyrosimService.off(event, callback)
  }
}

// 数据转换工具
export const dataTransform = {
  // 转换 PyroSim 数据格式为前端格式
  transformCompartmentData(pyrosimData) {
    return {
      id: pyrosimData.compartmentId,
      temperature: pyrosimData.temperature || 48,
      smoke: pyrosimData.smokeConcentration || 0,
      oxygen: pyrosimData.oxygenLevel || 20.9,
      co: pyrosimData.coConcentration || 0,
      pressure: pyrosimData.pressure || 101.3,
      humidity: pyrosimData.humidity || 50,
      visibility: pyrosimData.visibility || 100,
      timestamp: pyrosimData.timestamp || Date.now(),
      status: pyrosimData.fireStatus || 'inactive'
    }
  },

  // 转换历史数据格式
  transformHistoricalData(pyrosimHistory) {
    return pyrosimHistory.map(point => ({
      time: new Date(point.timestamp).toLocaleTimeString(),
      temperature: point.temperature,
      smoke: point.smokeConcentration,
      oxygen: point.oxygenLevel,
      co: point.coConcentration
    }))
  },

  // 转换火灾状态
  transformFireStatus(pyrosimStatus) {
    return {
      isActive: pyrosimStatus.fireActive || false,
      intensity: pyrosimStatus.fireIntensity || 0,
      spreadRate: pyrosimStatus.spreadRate || 0,
      suppressionActive: pyrosimStatus.suppressionActive || false,
      lastUpdate: pyrosimStatus.lastUpdate || Date.now()
    }
  }
}

// 错误处理工具
export const errorHandler = {
  // 处理 PyroSim 连接错误
  handleConnectionError(error) {
    console.error('PyroSim 连接错误:', error)
    
    // 根据错误类型返回用户友好的消息
    if (error.code === 'ECONNREFUSED') {
      return 'PyroSim 服务未启动或连接被拒绝'
    } else if (error.code === 'ETIMEDOUT') {
      return 'PyroSim 连接超时，请检查网络连接'
    } else if (error.response?.status === 404) {
      return 'PyroSim API 接口不存在'
    } else if (error.response?.status === 500) {
      return 'PyroSim 服务器内部错误'
    } else {
      return `PyroSim 连接失败: ${error.message}`
    }
  },

  // 处理数据获取错误
  handleDataError(error, compartmentId) {
    console.error(`获取舱室 ${compartmentId} 数据失败:`, error)
    
    if (error.code === 'ECONNREFUSED') {
      return 'PyroSim 服务连接失败'
    } else if (error.response?.status === 404) {
      return `舱室 ${compartmentId} 不存在`
    } else {
      return `获取数据失败: ${error.message}`
    }
  }
}

export default pyroAPI

