/**
 * PyroSim 数据获取服务
 * 负责与 PyroSim 软件进行数据交互
 */

import axios from 'axios'

// 轻量事件发射器（浏览器可用，替代 Node.js 的 events 模块）
class SimpleEventEmitter {
  constructor() {
    this.listeners = new Map()
  }

  on(event, callback) {
    const existing = this.listeners.get(event) || []
    existing.push(callback)
    this.listeners.set(event, existing)
    return this
  }

  off(event, callback) {
    const existing = this.listeners.get(event)
    if (!existing) return this
    const index = existing.indexOf(callback)
    if (index !== -1) existing.splice(index, 1)
    return this
  }

  emit(event, ...args) {
    const existing = this.listeners.get(event) || []
    // 拷贝数组，避免回调内增删监听造成迭代问题
    for (const cb of [...existing]) {
      try {
        cb(...args)
      } catch (err) {
        // 避免吞掉错误，但也不打断其他监听
        // eslint-disable-next-line no-console
        console.error('Event listener error:', err)
      }
    }
    return this
  }

  removeAllListeners(event) {
    if (event) {
      this.listeners.delete(event)
    } else {
      this.listeners.clear()
    }
    return this
  }
}

class PyroSimService extends SimpleEventEmitter {
  constructor() {
    super()
    this.isConnected = false
    this.simulationData = new Map()
    this.updateInterval = null
    this.reconnectAttempts = 0
    this.maxReconnectAttempts = 5
    this.reconnectDelay = 3000
  }

  /**
   * 连接到 PyroSim
   * @param {Object} config - 连接配置
   */
  async connect(config = {}) {
    const defaultConfig = {
      host: 'localhost',
      port: 8080,
      timeout: 5000,
      ...config
    }

    try {
      this.config = defaultConfig
      this.baseURL = `http://${defaultConfig.host}:${defaultConfig.port}`
      
      // 测试连接
      await this.ping()
      
      this.isConnected = true
      this.reconnectAttempts = 0
      this.emit('connected')
      
      console.log('PyroSim 连接成功')
      return true
    } catch (error) {
      console.error('PyroSim 连接失败:', error.message)
      this.emit('error', error)
      return false
    }
  }

  /**
   * 断开连接
   */
  disconnect() {
    this.isConnected = false
    this.stopDataPolling()
    this.emit('disconnected')
    console.log('PyroSim 连接已断开')
  }

  /**
   * 测试连接
   */
  async ping() {
    try {
      const response = await axios.get(`${this.baseURL}/api/ping`, {
        timeout: this.config.timeout
      })
      return response.data
    } catch (error) {
      throw new Error(`PyroSim 连接测试失败: ${error.message}`)
    }
  }

  /**
   * 获取模拟状态
   */
  async getSimulationStatus() {
    try {
      const response = await axios.get(`${this.baseURL}/api/simulation/status`, {
        timeout: this.config.timeout
      })
      return response.data
    } catch (error) {
      throw new Error(`获取模拟状态失败: ${error.message}`)
    }
  }

  /**
   * 获取舱室数据
   * @param {number} compartmentId - 舱室ID
   */
  async getCompartmentData(compartmentId) {
    try {
      const response = await axios.get(`${this.baseURL}/api/compartment/${compartmentId}`, {
        timeout: this.config.timeout
      })
      
      // 缓存数据
      this.simulationData.set(compartmentId, {
        ...response.data,
        timestamp: Date.now()
      })
      
      return response.data
    } catch (error) {
      throw new Error(`获取舱室 ${compartmentId} 数据失败: ${error.message}`)
    }
  }

  /**
   * 获取所有舱室数据
   */
  async getAllCompartmentsData() {
    try {
      const response = await axios.get(`${this.baseURL}/api/compartments`, {
        timeout: this.config.timeout
      })
      
      // 缓存所有数据
      response.data.forEach(compartment => {
        this.simulationData.set(compartment.id, {
          ...compartment,
          timestamp: Date.now()
        })
      })
      
      return response.data
    } catch (error) {
      throw new Error(`获取所有舱室数据失败: ${error.message}`)
    }
  }

  /**
   * 控制火灾模拟
   * @param {number} compartmentId - 舱室ID
   * @param {string} action - 操作类型 (start/stop/reset)
   * @param {Object} params - 参数
   */
  async controlFire(compartmentId, action, params = {}) {
    try {
      const response = await axios.post(`${this.baseURL}/api/fire/control`, {
        compartmentId,
        action,
        ...params
      }, {
        timeout: this.config.timeout
      })
      
      return response.data
    } catch (error) {
      throw new Error(`控制火灾模拟失败: ${error.message}`)
    }
  }

  /**
   * 获取历史数据
   * @param {number} compartmentId - 舱室ID
   * @param {number} timeRange - 时间范围（分钟）
   */
  async getHistoricalData(compartmentId, timeRange = 60) {
    try {
      const response = await axios.get(`${this.baseURL}/api/history/${compartmentId}`, {
        params: { timeRange },
        timeout: this.config.timeout
      })
      
      return response.data
    } catch (error) {
      throw new Error(`获取历史数据失败: ${error.message}`)
    }
  }

  /**
   * 开始数据轮询
   * @param {number} interval - 轮询间隔（毫秒）
   */
  startDataPolling(interval = 2000) {
    if (this.updateInterval) {
      clearInterval(this.updateInterval)
    }

    this.updateInterval = setInterval(async () => {
      if (!this.isConnected) {
        return
      }

      try {
        const data = await this.getAllCompartmentsData()
        this.emit('dataUpdate', data)
      } catch (error) {
        console.error('数据轮询错误:', error.message)
        this.handleConnectionError(error)
      }
    }, interval)
  }

  /**
   * 停止数据轮询
   */
  stopDataPolling() {
    if (this.updateInterval) {
      clearInterval(this.updateInterval)
      this.updateInterval = null
    }
  }

  /**
   * 处理连接错误
   */
  async handleConnectionError(error) {
    this.isConnected = false
    this.emit('error', error)

    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++
      console.log(`尝试重连 PyroSim (${this.reconnectAttempts}/${this.maxReconnectAttempts})`)
      
      setTimeout(async () => {
        try {
          await this.connect(this.config)
          this.startDataPolling()
        } catch (reconnectError) {
          console.error('重连失败:', reconnectError.message)
        }
      }, this.reconnectDelay)
    } else {
      console.error('PyroSim 重连次数超限，停止重连')
      this.emit('maxReconnectAttemptsReached')
    }
  }

  /**
   * 获取缓存的模拟数据
   * @param {number} compartmentId - 舱室ID
   */
  getCachedData(compartmentId) {
    return this.simulationData.get(compartmentId)
  }

  /**
   * 清除缓存数据
   */
  clearCache() {
    this.simulationData.clear()
  }

  /**
   * 获取连接状态
   */
  getConnectionStatus() {
    return {
      isConnected: this.isConnected,
      reconnectAttempts: this.reconnectAttempts,
      dataCount: this.simulationData.size
    }
  }
}

// 创建单例实例
const pyrosimService = new PyroSimService()

export default pyrosimService

