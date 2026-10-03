import express from 'express'
import cors from 'cors'
import helmet from 'helmet'
import compression from 'compression'
import rateLimit from 'express-rate-limit'
import dotenv from 'dotenv'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'
import logger from './utils/logger.js'
import errorHandler from './middleware/errorHandler.js'
import apiRoutes from './routes/index.js'
import sequelize from './config/database.js'
import { startEngine } from './services/fireSimulation.js'

dotenv.config()

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const app = express()

// 安全中间件
app.use(helmet())

// CORS配置
// 允许列表来自 CORS_ORIGIN（逗号分隔）。开发期额外放行任意 localhost 端口 ——
// Vite 端口会随 --port 漂移，写死单个端口必然在某次换端口后整条链路静默失败。
const allowedOrigins = (process.env.CORS_ORIGIN || 'http://localhost:5173')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean)
const isLocalDevOrigin = origin =>
  process.env.NODE_ENV !== 'production' && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)

app.use(cors({
  origin(origin, cb) {
    if (!origin) return cb(null, true)                       // 同源请求 / curl / 监控探针
    if (allowedOrigins.includes(origin)) return cb(null, true)
    if (isLocalDevOrigin(origin)) return cb(null, true)
    // 不抛错：抛错会变成 500，看起来像服务端故障。
    // 不下发 ACAO 头即可让浏览器自行拦截，语义正确且不污染错误日志。
    cb(null, false)
  },
  credentials: true
}))

// 压缩响应
app.use(compression())

// 请求体解析
app.use(express.json({ limit: '10mb' }))
app.use(express.urlencoded({ extended: true, limit: '10mb' }))

// 速率限制
// 默认值必须覆盖自家前端的正常轮询：fleet 2s + history 2s + alerts 5s
// + predict 10s + LSTM 健康 30s ≈ 78 req/min ≈ 1170 req/15min。
// 旧默认 1000 比前端正常速率还低，跑约 13 分钟后开始周期性 429。
// 可用 RATE_LIMIT_MAX 环境变量覆盖。
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15分钟
  max: Number(process.env.RATE_LIMIT_MAX) || 5000,
  message: '请求过于频繁，请稍后再试'
})
app.use('/api/', limiter)

// 静态文件
app.use('/uploads', express.static(join(__dirname, '../uploads')))

// 健康检查
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() })
})

// API路由
app.use('/api', apiRoutes)

// 错误处理
app.use(errorHandler)

// 404处理
app.use((req, res) => {
  res.status(404).json({
    success: false,
    message: '请求的资源不存在'
  })
})

const PORT = process.env.PORT || 3001

// 数据库连接检查并启动服务器
async function startServer() {
  try {
    // 测试数据库连接
    await sequelize.authenticate()
    logger.info('数据库连接成功')

    // 同步数据库模型（开发环境）。
    // alter:true 会把新增列（如 fire_events.suppressed/evacuated/modelSteps）
    // 补进已有表 —— 普通.sync() 对已存在的表不做任何事，新列永远加不上。
    // alter 失败（如表结构漂移过大）只告警不退出：老库还能按旧列跑，
    // 只是丢"重启恢复/指令落库"这两项持久化能力。
    if (process.env.NODE_ENV === 'development') {
      try {
        await sequelize.sync({ alter: true })
        logger.info('数据库模型同步完成')
      } catch (e) {
        logger.warn('数据库结构同步失败（可运行 npm run init-db 重建）', { message: e.message })
      }
    }

    // 启动火灾演化引擎（常驻推演所有活跃火灾并写入 fire_data）
    startEngine()

    // 启动服务器
    app.listen(PORT, () => {
      logger.info(`后端服务启动成功，端口: ${PORT}`)
      logger.info(`环境: ${process.env.NODE_ENV || 'development'}`)
    })
  } catch (error) {
    logger.error('数据库连接失败:', {
      message: error.message,
      stack: error.stack
    })
    process.exit(1)
  }
}

startServer()

process.on('SIGTERM', () => { logger.info('收到 SIGTERM，退出'); process.exit(0) })
process.on('SIGINT', () => { logger.info('收到 SIGINT，退出'); process.exit(0) })

export default app
