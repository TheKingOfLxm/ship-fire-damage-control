import axios from 'axios'
import Logger from '@/utils/logger'

// 创建axios实例
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:3001/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
api.interceptors.request.use(
  config => {
    Logger.api(config.method, config.url)
    return config
  },
  error => {
    Logger.error('API请求失败', { error })
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  response => {
    return response
  },
  error => {
    // 后端未启动时前端会持续轮询，这类失败属于可预期状态而非异常，
    // 用 debug 级别记录，避免把控制台刷满 error。
    const status = error.response?.status
    if (error.response) {
      Logger.error('API响应错误', {
        status,
        data: error.response.data,
        message: error.message
      })
    } else {
      Logger.debug('API不可达（后端未启动？）', {
        url: error.config?.url,
        message: error.message
      })
    }
    return Promise.reject(error)
  }
)

// 船舶相关API
export const shipAPI = {
  // 获取船舶基本信息
  getShipInfo: () => api.get('/ship/info'),

  // 获取舱室列表
  getCompartments: () => api.get('/compartments'),

  // 获取舱室详情
  getCompartmentDetail: id => api.get(`/compartments/${id}`),

  // 获取舱室实时数据
  getCompartmentsRealtime: () => api.get('/compartments/realtime'),

  // 获取风险分布数据
  getRiskDistribution: () => api.get('/ship/risk-distribution')
}

// 火灾相关API
export const fireAPI = {
  // 获取舱室火灾数据
  getFireData: compartmentId => api.get(`/fire/${compartmentId}`),

  // 全舰态势总览：一次拿齐所有舱室 + 火灾状态 + 阈值判定
  getFleetStatus: () => api.get('/fleet/status'),

  // 演化引擎自述（用的什么模型、有什么已知限制）
  getEngineInfo: () => api.get('/engine/info'),

  /**
   * 控制火灾
   * @param {number} compartmentId
   * @param {'start'|'stop'|'suppress'|'evacuate'|'reset'} action
   */
  controlFire: (compartmentId, action, params = {}) =>
    api.post(`/fire/${compartmentId}/control`, { action, ...params }),

  // 获取火灾历史（返回 series，含每分钟变化速率）
  getFireHistory: (compartmentId, params = {}) =>
    api.get(`/fire/${compartmentId}/history`, { params }),

  // 火灾事件流水
  getFireEvents: (params = {}) => api.get('/fire/events/list', { params })
}

// 警报相关API
export const alertAPI = {
  // 获取警报列表
  getAlerts: (params = {}) => api.get('/alerts', { params }),

  // 获取活跃警报
  getActiveAlerts: () => api.get('/alerts/active'),

  // 确认警报
  acknowledgeAlert: alertId => api.patch(`/alerts/${alertId}/acknowledge`),

  // 解决警报
  resolveAlert: alertId => api.patch(`/alerts/${alertId}/resolve`),

  // 手动创建警报
  createAlert: payload => api.post('/alerts', payload)
}

// AI大模型相关API
export const aiAPI = {
  // 分析火灾
  analyzeFire: (payload) => api.post('/ai/analyze', payload),

  // 统一分析入口（兼容旧接口）
  analyze: (payload) => api.post('/ai/analyze', payload),

  // 获取可用的大模型列表
  models: () => api.get('/ai/models'),

  // 与大模型进行对话
  chat: (payload) => api.post('/ai/chat', payload),

  // 让大模型模拟未来数据趋势
  simulate: (payload) => api.post('/ai/simulate', payload)
}

export default api
