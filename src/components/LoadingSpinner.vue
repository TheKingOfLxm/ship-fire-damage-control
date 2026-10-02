<template>
  <div v-if="loading" class="loading-container" :class="{ overlay: overlay }">
    <div class="loading-spinner">
      <div class="spinner"></div>
      <div v-if="text" class="loading-text">{{ text }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  loading: {
    type: Boolean,
    default: false
  },
  text: {
    type: String,
    default: '加载中...'
  },
  overlay: {
    type: Boolean,
    default: false
  }
})
</script>

<style scoped>
  .loading-container {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }

  .loading-container.overlay {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    z-index: 1000;
  }

  .loading-spinner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }

  .spinner {
    width: 40px;
    height: 40px;
    border: 3px solid rgba(255, 255, 255, 0.3);
    border-top: 3px solid #0088ff;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }

  .loading-text {
    color: #fff;
    font-size: 14px;
    font-weight: 500;
  }

  @keyframes spin {
    0% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  /* 小尺寸变体 */
  .loading-container.small .spinner {
    width: 24px;
    height: 24px;
    border-width: 2px;
  }

  .loading-container.small .loading-text {
    font-size: 12px;
  }

  /* 大尺寸变体 */
  .loading-container.large .spinner {
    width: 60px;
    height: 60px;
    border-width: 4px;
  }

  .loading-container.large .loading-text {
    font-size: 16px;
  }
</style>
