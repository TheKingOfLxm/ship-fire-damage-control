/**
 * 全局 Toast 队列
 * ─────────────────────────────────────────────────────────────
 * 之前所有操作结果只写进 console，用户得不到任何反馈。
 * 这里提供统一的轻量反馈通道，任意组件都能调用。
 */
import { ref } from 'vue'

let seq = 0
const items = ref([])

function push(type, title, desc = '', duration = 4200) {
  const id = ++seq
  const toast = { id, type, title, desc }
  items.value = [...items.value, toast]
  if (duration > 0) {
    setTimeout(() => dismiss(id), duration)
  }
  return id
}

function dismiss(id) {
  items.value = items.value.filter(t => t.id !== id)
}

function clear() {
  items.value = []
}

export function useToast() {
  return {
    items,
    dismiss,
    clear,
    success: (title, desc) => push('success', title, desc),
    error: (title, desc) => push('error', title, desc, 6000),
    warning: (title, desc) => push('warning', title, desc, 5000),
    info: (title, desc) => push('info', title, desc)
  }
}
