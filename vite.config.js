import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  envDir: './config',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    // 代理是开发期唯一不依赖 CORS 的通路：前端用相对路径 /api 请求，
    // 由这里转发到后端。端口必须与 backend/.env 的 PORT 和
    // lstm-prediction-server/server.py 的 app.run(port=...) 一致。
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_BACKEND || 'http://localhost:3001',
        changeOrigin: true
      },
      '/lstm': {
        target: process.env.VITE_PROXY_LSTM || 'http://localhost:5001',
        changeOrigin: true
      }
    },
    // 开发服务器优化
    hmr: {
      overlay: false // 禁用错误覆盖层，使用自定义错误处理
    }
  },
  build: {
    // 构建优化 - 使用 ES2022 以支持 top-level await (WebGPU 需要)
    target: 'es2022',
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true, // 生产环境移除console
        drop_debugger: true
      }
    },
    rollupOptions: {
      output: {
        // 代码分割。只声明确实被引用的大依赖 —— 在 manualChunks 里写一个
        // 无人 import 的包，rollup 仍会生成一个空 chunk 并给出告警。
        manualChunks: {
          three: ['three'],
          'vue-vendor': ['vue', 'vue-router', 'pinia']
        }
      }
    },
    // 资源优化
    assetsInlineLimit: 4096, // 4KB以下资源内联
    chunkSizeWarningLimit: 1000
  },
  // 依赖预构建优化
  optimizeDeps: {
    include: ['three'],
    esbuildOptions: {
      target: 'es2022' // 支持 top-level await (WebGPU 需要)
    }
  }
})
