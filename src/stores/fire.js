import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useFireStore = defineStore('fire', () => {
  // 状态
  const selectedCompartment = ref(null)
  const compartmentFireStates = ref({
    1: false,
    2: false,
    3: false,
    4: false,
    5: false
  })
  const fireParameters = ref({})

  // 计算属性
  const isFireActive = computed(() => {
    if (selectedCompartment.value === null) return false
    return compartmentFireStates.value[selectedCompartment.value] === true
  })

  // 操作
  function setFireState(compartmentId, isActive) {
    compartmentFireStates.value[compartmentId] = isActive
  }

  function toggleFire(compartmentId) {
    compartmentFireStates.value[compartmentId] = !compartmentFireStates.value[compartmentId]
  }

  function selectCompartment(compartmentId) {
    selectedCompartment.value = compartmentId
  }

  function clearSelection() {
    selectedCompartment.value = null
  }

  function updateFireParameters(params) {
    fireParameters.value = { ...fireParameters.value, ...params }
  }

  function getCompartmentFireState(compartmentId) {
    return compartmentFireStates.value[compartmentId] || false
  }

  return {
    selectedCompartment,
    compartmentFireStates,
    fireParameters,
    isFireActive,
    setFireState,
    toggleFire,
    selectCompartment,
    clearSelection,
    updateFireParameters,
    getCompartmentFireState
  }
})
