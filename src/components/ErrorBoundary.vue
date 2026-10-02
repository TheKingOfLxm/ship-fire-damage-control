<template>
  <div v-if="error" class="error-boundary">
    <div class="error-content">
      <div class="error-icon">⚠️</div>
      <h3 class="error-title">出现了一些问题</h3>
      <p class="error-message">{{ error.message || '未知错误' }}</p>
      <div class="error-actions">
        <button @click="retry" class="retry-btn">重试</button>
        <button @click="reset" class="reset-btn">重置</button>
      </div>
      <details v-if="error.stack" class="error-details">
        <summary>错误详情</summary>
        <pre class="error-stack">{{ error.stack }}</pre>
      </details>
    </div>
  </div>
  <slot v-else />
</template>

<script setup>
import { ref, onErrorCaptured } from 'vue'

const error = ref(null)

onErrorCaptured((err, instance, info) => {
  console.error('Error captured by ErrorBoundary:', err)
  console.error('Component instance:', instance)
  console.error('Error info:', info)

  error.value = err
  return false // 阻止错误继续传播
})

const retry = () => {
  error.value = null
  // 触发父组件重新渲染
  window.location.reload()
}

const reset = () => {
  error.value = null
  // 可以在这里添加重置逻辑
}
</script>

<style scoped>
  .error-boundary {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
    padding: 20px;
    background: rgba(255, 77, 79, 0.1);
    border: 1px solid rgba(255, 77, 79, 0.3);
    border-radius: 8px;
    margin: 20px;
  }

  .error-content {
    text-align: center;
    max-width: 400px;
  }

  .error-icon {
    font-size: 48px;
    margin-bottom: 16px;
  }

  .error-title {
    color: #ff4d4f;
    margin: 0 0 12px 0;
    font-size: 18px;
    font-weight: bold;
  }

  .error-message {
    color: #666;
    margin: 0 0 20px 0;
    line-height: 1.5;
  }

  .error-actions {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-bottom: 16px;
  }

  .retry-btn,
  .reset-btn {
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: all 0.3s;
  }

  .retry-btn {
    background: #1890ff;
    color: white;
  }

  .retry-btn:hover {
    background: #40a9ff;
  }

  .reset-btn {
    background: #f5f5f5;
    color: #666;
    border: 1px solid #d9d9d9;
  }

  .reset-btn:hover {
    background: #e6f7ff;
    border-color: #1890ff;
    color: #1890ff;
  }

  .error-details {
    margin-top: 16px;
    text-align: left;
  }

  .error-details summary {
    cursor: pointer;
    color: #666;
    font-size: 14px;
    margin-bottom: 8px;
  }

  .error-stack {
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    font-size: 12px;
    color: #666;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
</style>
