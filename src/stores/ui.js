import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  // 状态
  const leftPanelCollapsed = ref(false)
  const rightPanelCollapsed = ref(false)
  const loading = ref(false)
  const error = ref(null)
  const notification = ref(null)

  // 操作
  function toggleLeftPanel() {
    leftPanelCollapsed.value = !leftPanelCollapsed.value
  }

  function toggleRightPanel() {
    rightPanelCollapsed.value = !rightPanelCollapsed.value
  }

  function setLeftPanelCollapsed(collapsed) {
    leftPanelCollapsed.value = collapsed
  }

  function setRightPanelCollapsed(collapsed) {
    rightPanelCollapsed.value = collapsed
  }

  function setLoading(isLoading) {
    loading.value = isLoading
  }

  function showError(message) {
    error.value = message
  }

  function clearError() {
    error.value = null
  }

  function showNotification(message, type = 'info') {
    notification.value = { message, type, timestamp: Date.now() }
  }

  function clearNotification() {
    notification.value = null
  }

  return {
    leftPanelCollapsed,
    rightPanelCollapsed,
    loading,
    error,
    notification,
    toggleLeftPanel,
    toggleRightPanel,
    setLeftPanelCollapsed,
    setRightPanelCollapsed,
    setLoading,
    showError,
    clearError,
    showNotification,
    clearNotification
  }
})
