import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useShipStore = defineStore('ship', () => {
  // 状态
  const compartments = ref([])
  const shipInfo = ref(null)
  const modelLoaded = ref(false)

  // 操作
  function loadShipData(data) {
    compartments.value = data.compartments || []
    shipInfo.value = data.shipInfo || null
  }

  function setModelLoaded(loaded) {
    modelLoaded.value = loaded
  }

  function getCompartmentById(id) {
    return compartments.value.find(c => c.id === id)
  }

  return {
    compartments,
    shipInfo,
    modelLoaded,
    loadShipData,
    setModelLoaded,
    getCompartmentById
  }
})
