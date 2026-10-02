import { ref, computed } from 'vue'
import { shipAPI, fireAPI, alertAPI } from '@/api'

// 通用API状态管理
export function useApiState() {
  const loading = ref(false)
  const error = ref(null)
  const data = ref(null)

  const setLoading = value => {
    loading.value = value
    if (value) {
      error.value = null
    }
  }

  const setError = err => {
    error.value = err
    loading.value = false
  }

  const setData = newData => {
    data.value = newData
    loading.value = false
    error.value = null
  }

  const reset = () => {
    loading.value = false
    error.value = null
    data.value = null
  }

  return {
    loading: computed(() => loading.value),
    error: computed(() => error.value),
    data: computed(() => data.value),
    setLoading,
    setError,
    setData,
    reset
  }
}

// 船舶信息API
export function useShipInfo() {
  const { loading, error, data, setLoading, setError, setData } = useApiState()

  const fetchShipInfo = async () => {
    try {
      setLoading(true)
      const response = await shipAPI.getShipInfo()
      if (response.data.code === 200) {
        setData(response.data.data)
      } else {
        setError(new Error(response.data.message || '获取船舶信息失败'))
      }
    } catch (err) {
      setError(err)
    }
  }

  return {
    shipInfo: data,
    loading,
    error,
    fetchShipInfo
  }
}

// 舱室数据API
export function useCompartments() {
  const { loading, error, data, setLoading, setError, setData } = useApiState()

  const fetchCompartments = async () => {
    try {
      setLoading(true)
      const response = await shipAPI.getCompartments()
      if (response.data.code === 200) {
        setData(response.data.data)
      } else {
        setError(new Error(response.data.message || '获取舱室数据失败'))
      }
    } catch (err) {
      setError(err)
    }
  }

  const fetchCompartmentsRealtime = async () => {
    try {
      setLoading(true)
      const response = await shipAPI.getCompartmentsRealtime()
      if (response.data.code === 200) {
        setData(response.data.data)
      } else {
        setError(new Error(response.data.message || '获取实时数据失败'))
      }
    } catch (err) {
      setError(err)
    }
  }

  return {
    compartments: data,
    loading,
    error,
    fetchCompartments,
    fetchCompartmentsRealtime
  }
}

// 火灾控制API
export function useFireControl() {
  const { loading, error, setLoading, setError } = useApiState()

  const controlFire = async (compartmentId, action) => {
    try {
      setLoading(true)
      const response = await fireAPI.controlFire(compartmentId, action)
      // 处理不同的响应格式
      if (response.data.status) {
        // 直接格式: {status: 'active'/'inactive'}
        return { status: response.data.status, compartmentId, action }
      } else if (response.data.code === 200) {
        // 包装格式: {code: 200, data: {...}}
        return response.data.data
      } else {
        setError(new Error(response.data.message || '火灾控制失败'))
        return null
      }
    } catch (err) {
      setError(err)
      return null
    }
  }

  const getFireData = async compartmentId => {
    // 如果没有有效的舱室ID，直接返回null
    if (!compartmentId) {
      return null
    }
    
    try {
      setLoading(true)
      const response = await fireAPI.getFireData(compartmentId)
      if (response.data.code === 200) {
        return response.data.data
      } else {
        setError(new Error(response.data.message || '获取火灾数据失败'))
        return null
      }
    } catch (err) {
      setError(err)
      return null
    }
  }

  return {
    loading,
    error,
    controlFire,
    getFireData
  }
}

// 警报API
export function useAlerts() {
  const { loading, error, data, setLoading, setError, setData } = useApiState()

  const fetchAlerts = async () => {
    try {
      setLoading(true)
      const response = await alertAPI.getAlerts()
      if (response.data.code === 200) {
        setData(response.data.data)
      } else {
        setError(new Error(response.data.message || '获取警报失败'))
      }
    } catch (err) {
      setError(err)
    }
  }

  const clearAlert = async alertId => {
    try {
      setLoading(true)
      const response = await alertAPI.clearAlert(alertId)
      if (response.data.code === 200) {
        // 重新获取警报列表
        await fetchAlerts()
      } else {
        setError(new Error(response.data.message || '清除警报失败'))
      }
    } catch (err) {
      setError(err)
    }
  }

  return {
    alerts: data,
    loading,
    error,
    fetchAlerts,
    clearAlert
  }
}
