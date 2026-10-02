import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
// 设计令牌必须全局可用：旧代码从 MainView.vue 里引入，
// 依赖 MainView 已挂载才有 :root 变量，组件单独渲染时会失效。
import './assets/styles/modern-ui.css'
import './assets/styles/global.css'
import './assets/styles/icons.css'

// 在开发环境下引入 mock，并在应用启动前完成注册
if (import.meta.env.DEV) {
  await import('./mock')
}

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
