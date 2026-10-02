/**
 * 统一日志工具
 * 生产环境自动禁用调试日志
 */

const isDevelopment = import.meta.env.DEV

const Logger = {
  /**
   * 调试日志 - 仅开发环境
   */
  debug: (...args) => {
    if (isDevelopment) {
      console.log('[DEBUG]', ...args)
    }
  },

  /**
   * 信息日志 - 仅开发环境
   */
  info: (...args) => {
    if (isDevelopment) {
      console.log('[INFO]', ...args)
    }
  },

  /**
   * 警告日志 - 保留
   */
  warn: (...args) => {
    console.warn('[WARN]', ...args)
  },

  /**
   * 错误日志 - 保留
   */
  error: (...args) => {
    console.error('[ERROR]', ...args)
  },

  /**
   * API请求日志 - 仅开发环境
   */
  api: (method, url, data) => {
    if (isDevelopment) {
      console.log(`[API] ${method?.toUpperCase()} ${url}`, data)
    }
  },

  /**
   * 组件生命周期日志 - 仅开发环境
   */
  lifecycle: (component, action, data) => {
    if (isDevelopment) {
      console.log(`[Lifecycle] ${component} ${action}`, data || '')
    }
  }
}

export default Logger
