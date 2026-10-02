import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAlertStore = defineStore('alert', () => {
  // 状态
  const alerts = ref([])
  const currentAlert = ref(null)
  const alertHistory = ref([])

  // 操作
  function addAlert(alert) {
    const newAlert = {
      id: Date.now(),
      timestamp: Date.now(),
      ...alert
    }
    alerts.value.push(newAlert)
    alertHistory.value.push(newAlert)
    currentAlert.value = newAlert
  }

  function clearAlert(alertId) {
    alerts.value = alerts.value.filter(a => a.id !== alertId)
    if (currentAlert.value?.id === alertId) {
      currentAlert.value = alerts.value[alerts.value.length - 1] || null
    }
  }

  function clearAllAlerts() {
    alerts.value = []
    currentAlert.value = null
  }

  function getAlertsByLevel(level) {
    return alerts.value.filter(a => a.level === level)
  }

  function getActiveAlerts() {
    return alerts.value.filter(a => !a.resolved)
  }

  function resolveAlert(alertId) {
    const alert = alerts.value.find(a => a.id === alertId)
    if (alert) {
      alert.resolved = true
      alert.resolvedAt = Date.now()
    }
  }

  return {
    alerts,
    currentAlert,
    alertHistory,
    addAlert,
    clearAlert,
    clearAllAlerts,
    getAlertsByLevel,
    getActiveAlerts,
    resolveAlert
  }
})
