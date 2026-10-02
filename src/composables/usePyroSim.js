/**
 * PyroSim 组合式函数
 * 提供 Vue 组件中使用的 PyroSim 功能
 */

import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import { pyroAPI, dataTransform, errorHandler } from '../api/pyrosim.js'

export function usePyroSim() {
  // 响应式状态
  const isConnected = ref(false)
  const isLoading = ref(false)
  const error = ref(null)
  const connectionStatus = ref({})
  
  // 模拟数据
  const simulationData = reactive(new Map())
  const historicalData = reactive(new Map())
  
  // 连接配置
  const config = reactive({
    host: 'localhost',
    port: 8080,
    timeout: 10000  // 增加超时时间，因为真实 PyroSim 可能需要更长时间
  })

  // 计算属性
  const isDataAvailable = computed(() => simulationData.size > 0)
  const connectedCompartments = computed(() => Array.from(simulationData.keys()))

  // 连接 PyroSim
  const connect = async (customConfig = {}) => {
    isLoading.value = true
    error.value = null
    
    try {
      const connectConfig = { ...config, ...customConfig }
      const success = await pyroAPI.connect(connectConfig)
      
      if (success) {
        isConnected.value = true
        connectionStatus.value = pyroAPI.getConnectionStatus()
        
        // 开始数据轮询
        pyroAPI.startDataPolling(2000)
        
        return true
      } else {
        throw new Error('连接失败')
      }
    } catch (err) {
      error.value = errorHandler.handleConnectionError(err)
      isConnected.value = false
      return false
    } finally {
      isLoading.value = false
    }
  }

  // 断开连接
  const disconnect = () => {
    pyroAPI.disconnect()
    isConnected.value = false
    simulationData.clear()
    historicalData.clear()
    error.value = null
  }

  // 获取舱室数据
  const getCompartmentData = async (compartmentId) => {
    if (!isConnected.value) {
      throw new Error('PyroSim 未连接')
    }

    try {
      const rawData = await pyroAPI.getCompartmentData(compartmentId)
      const transformedData = dataTransform.transformCompartmentData(rawData)
      
      // 更新响应式数据
      simulationData.set(compartmentId, transformedData)
      
      return transformedData
    } catch (err) {
      error.value = errorHandler.handleDataError(err, compartmentId)
      throw err
    }
  }

  // 获取所有舱室数据
  const getAllCompartmentsData = async () => {
    if (!isConnected.value) {
      throw new Error('PyroSim 未连接')
    }

    try {
      const rawData = await pyroAPI.getAllCompartmentsData()
      
      // 转换并更新所有数据
      rawData.forEach(item => {
        const transformedData = dataTransform.transformCompartmentData(item)
        simulationData.set(item.compartmentId, transformedData)
      })
      
      return Array.from(simulationData.values())
    } catch (err) {
      error.value = errorHandler.handleConnectionError(err)
      throw err
    }
  }

  // 控制火灾
  const controlFire = async (compartmentId, action, params = {}) => {
    if (!isConnected.value) {
      throw new Error('PyroSim 未连接')
    }

    try {
      const result = await pyroAPI.controlFire(compartmentId, action, params)
      
      // 更新火灾状态
      if (simulationData.has(compartmentId)) {
        const data = simulationData.get(compartmentId)
        data.status = result.status
        simulationData.set(compartmentId, data)
      }
      
      return result
    } catch (err) {
      error.value = errorHandler.handleDataError(err, compartmentId)
      throw err
    }
  }

  // 获取历史数据
  const getHistoricalData = async (compartmentId, timeRange = 60) => {
    if (!isConnected.value) {
      throw new Error('PyroSim 未连接')
    }

    try {
      const rawData = await pyroAPI.getHistoricalData(compartmentId, timeRange)
      const transformedData = dataTransform.transformHistoricalData(rawData)
      
      // 更新历史数据
      historicalData.set(compartmentId, transformedData)
      
      return transformedData
    } catch (err) {
      error.value = errorHandler.handleDataError(err, compartmentId)
      throw err
    }
  }

  // 获取模拟状态
  const getSimulationStatus = async () => {
    if (!isConnected.value) {
      throw new Error('PyroSim 未连接')
    }

    try {
      return await pyroAPI.getSimulationStatus()
    } catch (err) {
      error.value = errorHandler.handleConnectionError(err)
      throw err
    }
  }

  // 获取缓存的舱室数据
  const getCachedCompartmentData = (compartmentId) => {
    return simulationData.get(compartmentId) || null
  }

  // 获取缓存的历史数据
  const getCachedHistoricalData = (compartmentId) => {
    return historicalData.get(compartmentId) || []
  }

  // 清除缓存
  const clearCache = () => {
    simulationData.clear()
    historicalData.clear()
    pyroAPI.clearCache()
  }

  // 更新连接状态
  const updateConnectionStatus = () => {
    if (isConnected.value) {
      connectionStatus.value = pyroAPI.getConnectionStatus()
    }
  }

  // 事件监听器
  const setupEventListeners = () => {
    // 监听连接事件
    pyroAPI.on('connected', () => {
      isConnected.value = true
      error.value = null
      updateConnectionStatus()
    })

    pyroAPI.on('disconnected', () => {
      isConnected.value = false
      updateConnectionStatus()
    })

    pyroAPI.on('error', (err) => {
      error.value = errorHandler.handleConnectionError(err)
      isConnected.value = false
    })

    // 监听数据更新事件
    pyroAPI.on('dataUpdate', (data) => {
      data.forEach(item => {
        const transformedData = dataTransform.transformCompartmentData(item)
        simulationData.set(item.compartmentId, transformedData)
      })
      updateConnectionStatus()
    })

    // 监听重连失败事件
    pyroAPI.on('maxReconnectAttemptsReached', () => {
      error.value = 'PyroSim 重连次数超限，请手动重新连接'
      isConnected.value = false
    })
  }

  // 清理事件监听器
  const cleanupEventListeners = () => {
    pyroAPI.off('connected')
    pyroAPI.off('disconnected')
    pyroAPI.off('error')
    pyroAPI.off('dataUpdate')
    pyroAPI.off('maxReconnectAttemptsReached')
  }

  // 生命周期钩子
  onMounted(() => {
    setupEventListeners()
  })

  onUnmounted(() => {
    cleanupEventListeners()
    disconnect()
  })

  return {
    // 状态
    isConnected,
    isLoading,
    error,
    connectionStatus,
    simulationData,
    historicalData,
    config,
    
    // 计算属性
    isDataAvailable,
    connectedCompartments,
    
    // 方法
    connect,
    disconnect,
    getCompartmentData,
    getAllCompartmentsData,
    controlFire,
    getHistoricalData,
    getSimulationStatus,
    getCachedCompartmentData,
    getCachedHistoricalData,
    clearCache,
    updateConnectionStatus
  }
}

// 单舱室 PyroSim 功能
export function usePyroSimCompartment(compartmentId) {
  const {
    isConnected,
    isLoading,
    error,
    connect,
    disconnect,
    getCompartmentData,
    controlFire,
    getHistoricalData,
    getCachedCompartmentData,
    getCachedHistoricalData
  } = usePyroSim()

  // 当前舱室的数据
  const compartmentData = computed(() => {
    return getCachedCompartmentData(compartmentId.value) || {
      id: compartmentId.value,
      temperature: 48,
      smoke: 0,
      oxygen: 20.9,
      co: 0,
      pressure: 101.3,
      humidity: 50,
      visibility: 100,
      timestamp: Date.now(),
      status: 'inactive'
    }
  })

  // 当前舱室的历史数据
  const compartmentHistory = computed(() => {
    return getCachedHistoricalData(compartmentId.value) || []
  })

  // 火灾状态
  const fireStatus = computed(() => {
    return compartmentData.value.status === 'active'
  })

  // 获取当前舱室数据
  const fetchCompartmentData = async () => {
    if (!compartmentId.value) return null
    return await getCompartmentData(compartmentId.value)
  }

  // 控制当前舱室火灾
  const controlCompartmentFire = async (action, params = {}) => {
    if (!compartmentId.value) return null
    return await controlFire(compartmentId.value, action, params)
  }

  // 获取当前舱室历史数据
  const fetchCompartmentHistory = async (timeRange = 60) => {
    if (!compartmentId.value) return []
    return await getHistoricalData(compartmentId.value, timeRange)
  }

  return {
    // 状态
    isConnected,
    isLoading,
    error,
    compartmentData,
    compartmentHistory,
    fireStatus,
    
    // 方法
    connect,
    disconnect,
    fetchCompartmentData,
    controlCompartmentFire,
    fetchCompartmentHistory
  }
}
