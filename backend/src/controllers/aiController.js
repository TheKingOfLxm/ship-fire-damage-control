import axios from 'axios'
import { asyncHandler } from '../middleware/errorHandler.js'
import logger from '../utils/logger.js'

const AI_PROVIDERS = {
  glm: {
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
    chatEndpoint: '/chat/completions'
  },
  qwen: {
    baseUrl: 'https://dashscope.aliyuncs.com/api/v1',
    model: 'qwen-turbo',
    chatEndpoint: '/services/aigc/text-generation/generation'
  }
}

// 获取AI密钥（支持备用密钥）
function getAIKeys() {
  return {
    glm: process.env.AI_API_KEY || '',
    qwen: process.env.AI_QWEN_API_KEY || ''
  }
}

const SYSTEM_PROMPT = `你是一个专业的船舶火灾监控与分析系统的AI助手。你的职责是根据传感器监测数据提供专业的火灾态势分析和处置建议。

【系统背景】
你工作在船舶火灾监控系统中，该系统通过分布在各舱室的传感器实时采集以下数据：
- 温度（°C）：正常范围 20-40°C，火灾可达800°C以上
- 烟雾浓度（%）：正常范围 0-5%，火灾可达100%以上
- 氧气浓度（%）：正常范围 19-21%，火灾可能降至10%以下
- CO浓度（ppm）：正常范围 0-10ppm，火灾可能超过1000ppm

【你的任务】
1. 根据传感器数据分析当前火灾态势，评估风险等级
2. 识别火灾发展阶段（初期、增长期、充分发展）
3. 分析潜在威胁（高温、浓烟、缺氧、中毒、爆炸等）
4. 提供专业的处置建议和决策支持
5. 在对话中，根据用户提供的问题提供针对性的回答

【风险等级标准】
- low（低风险）：所有参数在正常范围内
- medium（中等风险）：1-2个参数轻微超标
- high（高风险）：多个参数明显超标，火势在增长
- critical（危急）：多个参数严重超标，火势充分发展

【输出要求】
**分析接口**必须以JSON格式返回，包含以下字段：
- riskLevel: 风险等级
- riskScore: 风险分数（0-100）
- fireStage: 火灾阶段
- summary: 简要总结（1-2句话）
- threats: 威胁列表（数组）
- recommendations: 处置建议列表（数组）
- warnings: 警示信息（可选）

**对话接口**则相反：用自然语言直接回答，绝对不要输出 JSON、代码块或结构化数据。
值班人员要的是能照着做的指令，不是数据表格。必要时可以用简短的分点或编号列表。

请记住：你是船舶火灾监控系统的AI助手，你的分析必须基于真实的传感器数据，而不是假设或模拟。`

/** 对话用的系统提示：明确禁止 JSON，否则模型会把每句回答都写成结构化数据 */
const num = v => (Number.isFinite(Number(v)) ? Number(v) : 0)
const chatSystemPrompt = (ctx = {}) => `你是一个专业的船舶火灾监控与分析系统的AI助手，正在与值班损管人员实时对话。

【当前传感器数据】
- 舱室：${ctx.compartmentName || '未选择'}
- 温度：${num(ctx.temperature)}°C
- 烟雾：${num(ctx.smoke)}%
- 氧气：${num(ctx.oxygen)}%
- CO：${num(ctx.co)}ppm

【回答要求】
1. 用**自然语言**直接回答，像值班顾问一样说话
2. **绝对不要**输出 JSON、代码块、表格或任何结构化数据
3. 需要分步操作时用简短编号列表，每条一句话说清楚
4. 优先给可立即执行的指令，再解释原因
5. 数据不足以判断时直说缺什么，不要编造
6. 回答控制在 200 字以内，除非用户明确要求展开`

export const analyzeFire = asyncHandler(async (req, res) => {
  const { compartmentId, compartmentName, fireType, temperature, smoke, oxygen, co } = req.body

  // 用户选择使用的模型
  const selectedProvider = req.body.provider || process.env.AI_PROVIDER || 'glm'
  const apiKeys = getAIKeys()

  // 构建完整的分析提示词
  const analysisPrompt = buildFireAnalysisPrompt({ compartmentName, fireType, temperature, smoke, oxygen, co })

  let lastError = null
  let result = null

  // 尝试使用用户选择的提供商
  if (apiKeys[selectedProvider]) {
    result = await tryAIProvider(selectedProvider, apiKeys[selectedProvider], analysisPrompt, { compartmentName, fireType, temperature, smoke, oxygen, co })
  }

  // 如果选择的提供商失败，尝试其他可用的提供商
  if (!result && apiKeys.glm && selectedProvider !== 'glm') {
    logger.info(`${selectedProvider}失败，尝试使用GLM`)
    result = await tryAIProvider('glm', apiKeys.glm, analysisPrompt, { compartmentName, fireType, temperature, smoke, oxygen, co })
  }

  if (!result && apiKeys.qwen && selectedProvider !== 'qwen') {
    logger.info('GLM失败，尝试使用通义千问')
    result = await tryAIProvider('qwen', apiKeys.qwen, analysisPrompt, { compartmentName, fireType, temperature, smoke, oxygen, co })
  }

  if (!result) {
    // 所有AI提供商都失败，使用降级分析
    logger.warn('所有AI提供商调用失败，使用降级分析')
    result = getFallbackAnalysis({ compartmentName, fireType, temperature, smoke, oxygen, co })
  }

  logger.info('AI火灾分析完成', { compartmentId, provider: result.provider || 'fallback' })

  res.json({
    success: true,
    data: result
  })
})

async function tryAIProvider(provider, apiKey, prompt, sensorData) {
  if (!apiKey) {
    logger.warn(`${provider} API密钥未配置`)
    return null
  }

  const config = AI_PROVIDERS[provider]
  const headers = {
    'Authorization': `Bearer ${apiKey}`,
    'Content-Type': 'application/json; charset=utf-8'
  }

  try {
    let response

    if (provider === 'glm') {
      // 智谱AI调用
      response = await axios.post(
        `${config.baseUrl}${config.chatEndpoint}`,
        {
          model: config.model,
          messages: [
            {
              role: 'system',
              content: SYSTEM_PROMPT
            },
            {
              role: 'user',
              content: prompt
            }
          ],
          temperature: 0.7,
          max_tokens: 2000
        },
        { headers, timeout: 30000 }
      )

      logger.info(`GLM API响应成功`, { status: response.status })

      const aiResult = response.data.choices[0].message.content
      logger.info('GLM响应内容', { aiText: aiResult?.substring(0, 1000) }) // 记录前1000字符

      const analysisResult = parseAIResponse(aiResult, sensorData)
      analysisResult.provider = 'glm'
      return analysisResult

    } else if (provider === 'qwen') {
      // 通义千问调用
      response = await axios.post(
        `${config.baseUrl}${config.chatEndpoint}`,
        {
          model: config.model,
          input: {
            messages: [
              {
                role: 'system',
                content: SYSTEM_PROMPT
              },
              {
                role: 'user',
                content: prompt
              }
            ]
          },
          parameters: {
            temperature: 0.7,
            max_tokens: 2000,
            result_format: 'message'
          }
        },
        { headers, timeout: 30000 }
      )

      logger.info(`Qwen API响应成功`, { status: response.status })

      const aiResult = response.data.output?.choices?.[0]?.message?.content
        || response.data.output?.text
        || ''
      logger.info('Qwen响应内容', { aiText: aiResult?.substring(0, 1000) }) // 记录前1000字符

      const analysisResult = parseAIResponse(aiResult, sensorData)
      analysisResult.provider = 'qwen'
      return analysisResult
    }
  } catch (error) {
    logger.error(`${provider} AI调用失败`, {
      error: error.message,
      status: error.response?.status,
      data: error.response?.data
    })
    return null
  }
}

function buildFireAnalysisPrompt(data) {
  const { compartmentName, fireType, temperature, smoke, oxygen, co } = data

  return `请分析以下船舶火灾情况：

【位置信息】
舱室：${compartmentName}
火灾类型：${fireType || '未知'}

【当前监测数据】
温度：${temperature}°C
烟雾浓度：${smoke}%
氧气浓度：${oxygen}%
CO浓度：${co}ppm

请提供以下分析，以JSON格式返回：

1. 【火灾评估】
   - riskLevel: 火势等级（low/medium/high/critical）
   - riskScore: 风险分数（0-100）
   - fireStage: 火灾阶段

2. 【威胁分析】
   - threats: 威胁列表数组

3. 【处置建议】
   - recommendations: 建议列表数组

4. 【警示信息】
   - warnings: 警示列表数组（如有）

5. 【总结】
   - summary: 简要总结

返回格式示例：
{
  "riskLevel": "high",
  "riskScore": 75,
  "fireStage": "增长期",
  "summary": "简要总结",
  "threats": ["威胁1", "威胁2"],
  "recommendations": ["建议1", "建议2"],
  "warnings": ["警示1"]
}`
}

function parseAIResponse(aiText, sensorData) {
  logger.info('开始解析AI响应', { aiTextLength: aiText?.length })

  try {
    // 方法1: 尝试直接解析整个响应
    let jsonStr = null
    let parsed = null

    // 尝试多种JSON提取方式
    const patterns = [
      // Markdown代码块
      /```(?:json)?\s*(\{[\s\S]*?\})\s*```/,
      /```(?:json)?\s*([\s\S]*?)```/,
      // 直接的JSON对象（非贪婪匹配，找到第一个完整的JSON）
      /\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/,
      // 宽松匹配（最后尝试）
      /\{[\s\S]*\}/
    ]

    for (const pattern of patterns) {
      const match = aiText.match(pattern)
      if (match) {
        jsonStr = match[1] || match[0] // 优先捕获组，否则使用完整匹配
        try {
          parsed = JSON.parse(jsonStr)
          logger.info('JSON解析成功', { pattern: pattern.toString() })
          break
        } catch (e) {
          logger.warn('JSON解析失败，尝试下一个模式', { error: e.message })
          continue
        }
      }
    }

    if (parsed && typeof parsed === 'object') {
      // 确保必要的字段存在
      const result = {
        riskLevel: parsed.riskLevel || 'medium',
        riskScore: parsed.riskScore || 50,
        fireStage: parsed.fireStage || '未知',
        summary: parsed.summary || 'AI分析完成',
        threats: Array.isArray(parsed.threats) ? parsed.threats : [],
        recommendations: Array.isArray(parsed.recommendations) ? parsed.recommendations : [],
        warnings: Array.isArray(parsed.warnings) ? parsed.warnings : [],
        timestamp: new Date().toISOString(),
        provider: parsed.provider || 'ai'
      }

      // 根据传感器数据验证并调整风险等级
      const actualRisk = calculateActualRisk(sensorData)
      result.riskBreakdown = riskBreakdown(sensorData)
      if (actualRisk !== result.riskLevel) {
        logger.info('根据传感器数据调整风险等级', { from: result.riskLevel, to: actualRisk })
        result.riskLevel = actualRisk
        result.riskScore = RISK_SCORE[actualRisk]
      }
      // 阶段同样以实测为准：模型可能把氧气 8% 的舱说成"初期"
      if (actualRisk === 'critical' && !/充分|fully/i.test(result.fireStage || '')) {
        result.fireStage = '充分发展'
      }

      logger.info('AI解析完成', { riskLevel: result.riskLevel, riskScore: result.riskScore })
      return result
    }
  } catch (e) {
    logger.error('解析AI响应失败，使用降级方案', { error: e.message, stack: e.stack, aiText: aiText?.substring(0, 500) })
  }

  // 降级方案：返回基础分析
  logger.warn('使用降级分析方案')
  return getFallbackAnalysis(sensorData)
}

/**
 * 按**全部四项**指标综合评定风险等级。
 *
 * 旧实现是 if 串联、只看温度：
 *   if (temperature > 800) critical; if (temperature > 500) high; ...
 * 结果 440℃ / 烟雾100% / 氧气8% / CO485ppm 这种明显会致死的组合被判成 medium ——
 * 氧气 8% 已经低于人能生存的极限，温度没到 500 不能作为"降级"的依据。
 * 损管系统的风险等级必须**取最严重的单项**，不能被某一项的温度掩盖。
 */
const RISK_SCORE = { low: 25, medium: 50, high: 75, critical: 95 }

/** 单项指标的严重度：0=正常 1=警戒 2=危险 3=危急 */
function severity(t, smoke, oxygen, co) {
  let s = 0
  // 温度
  if (t >= 800) s = Math.max(s, 3)
  else if (t >= 500) s = Math.max(s, 2)
  else if (t >= 200) s = Math.max(s, 1)
  // 烟雾（遮挡与能见度）
  if (smoke >= 80) s = Math.max(s, 3)
  else if (smoke >= 45) s = Math.max(s, 2)
  else if (smoke >= 20) s = Math.max(s, 1)
  // 氧气（人可生存的下限约 16%）
  if (oxygen <= 12) s = Math.max(s, 3)
  else if (oxygen <= 16) s = Math.max(s, 2)
  else if (oxygen <= 19) s = Math.max(s, 1)
  // 一氧化碳（>150ppm 长时间暴露即危及生命）
  if (co >= 500) s = Math.max(s, 3)
  else if (co >= 150) s = Math.max(s, 2)
  else if (co >= 50) s = Math.max(s, 1)
  return s
}

function calculateActualRisk(data) {
  const { temperature, smoke, oxygen, co } = data
  const s = severity(temperature, smoke, oxygen, co)
  return ['low', 'medium', 'high', 'critical'][s]
}

/** 各项指标的严重度明细，供界面逐项展示而不是只给一个总分 */
function riskBreakdown(data) {
  const { temperature, smoke, oxygen, co } = data
  return {
    temperature: severity(temperature, 0, 21, 0),
    smoke: severity(20, smoke, 21, 0),
    oxygen: severity(20, 0, oxygen, 0),
    co: severity(20, 0, 21, co)
  }
}

function getFallbackAnalysis(data) {
  const { compartmentName, temperature, smoke, oxygen, co } = data

  const riskLevel = calculateActualRisk(data)
  const riskScore = RISK_SCORE[riskLevel]

  const threats = []
  const recommendations = []
  const warnings = []

  if (temperature > 100) {
    threats.push('高温可能引燃周围可燃物')
    recommendations.push('尽快控制火源，防止蔓延')
    warnings.push('注意防止结构受热变形')
  }

  if (smoke > 20) {
    threats.push('浓烟影响视线和呼吸')
    recommendations.push('加强通风排烟')
    warnings.push('佩戴呼吸器进入')
  }

  if (oxygen < 19) {
    threats.push('氧气含量下降，有窒息风险')
    recommendations.push('确保人员撤离或佩戴供氧设备')
  }

  if (co > 50) {
    threats.push('CO浓度超标，中毒风险高')
    warnings.push('CO中毒无征兆，极度危险')
  }

  if (threats.length === 0) {
    threats.push('当前环境相对安全')
    recommendations.push('持续监控，保持警惕')
  }

  return {
    riskLevel,
    riskScore,
    riskBreakdown: riskBreakdown(data),
    // 阶段判断不能只看温度：氧气耗尽 + 高烟温同样是充分发展期
    fireStage: (temperature > 300 || (oxygen <= 16 && smoke >= 45))
      ? '充分发展' : (temperature > 100 || smoke >= 20 ? '增长期' : '初期'),
    summary: `${compartmentName}当前${riskLevel === 'low' ? '相对安全' : '存在' + riskLevel + '风险'}，${recommendations[0] || '需持续监控'}`,
    threats,
    recommendations,
    warnings: warnings.length > 0 ? warnings : undefined,
    provider: 'fallback',
    timestamp: new Date().toISOString()
  }
}

// 获取可用模型列表
export const getModels = asyncHandler(async (req, res) => {
  const models = [
    {
      id: 'glm',
      name: '智谱 GLM',
      provider: 'glm',
      description: '智谱AI大语言模型'
    },
    {
      id: 'qwen',
      name: '通义千问',
      provider: 'qwen',
      description: '阿里云通义千问大语言模型'
    }
  ]

  res.json({
    success: true,
    data: models
  })
})

// AI对话接口（支持自动降级）
export const chat = asyncHandler(async (req, res) => {
  const { model, messages, context } = req.body

  if (!model || !messages || !Array.isArray(messages)) {
    return res.status(400).json({
      success: false,
      message: '参数错误：需要model和messages参数'
    })
  }

  const apiKeys = getAIKeys()
  const preferredProvider = model === 'qwen' ? 'qwen' : 'glm'

  const systemMessage = {
    role: 'system',
    content: chatSystemPrompt(context)
  }

  const formattedMessages = [systemMessage, ...messages.map(m => ({
    role: m.role === 'assistant' ? 'assistant' : m.role,
    content: m.content
  }))]

  // 尝试顺序：先选首选，再试另一个
  const tryOrder = [preferredProvider]
  if (preferredProvider === 'glm' && apiKeys.qwen) tryOrder.push('qwen')
  if (preferredProvider === 'qwen' && apiKeys.glm) tryOrder.push('glm')

  for (const provider of tryOrder) {
    const apiKey = apiKeys[provider]
    if (!apiKey) continue

    try {
      const reply = await callChatProvider(provider, apiKey, formattedMessages)
      if (reply) {
        logger.info('AI对话完成', { provider, messageCount: messages.length })
        return res.json({
          success: true,
          data: { reply, provider, model }
        })
      }
    } catch (err) {
      logger.warn(`${provider} 对话失败，尝试降级`, { error: err.message })
    }
  }

  res.status(500).json({
    success: false,
    message: '所有AI服务均不可用'
  })
})

async function callChatProvider(provider, apiKey, formattedMessages) {
  const config = AI_PROVIDERS[provider]
  const headers = {
    'Authorization': `Bearer ${apiKey}`,
    'Content-Type': 'application/json; charset=utf-8'
  }

  let response

  if (provider === 'glm') {
    response = await axios.post(
      `${config.baseUrl}${config.chatEndpoint}`,
      {
        model: config.model,
        messages: formattedMessages,
        temperature: 0.7,
        max_tokens: 2000
      },
      { headers, timeout: 30000 }
    )
    return response.data.choices[0].message.content
  } else if (provider === 'qwen') {
    response = await axios.post(
      `${config.baseUrl}${config.chatEndpoint}`,
      {
        model: config.model,
        input: { messages: formattedMessages },
        parameters: {
          temperature: 0.7,
          max_tokens: 2000,
          result_format: 'message'
        }
      },
      { headers, timeout: 30000 }
    )
    return response.data.output?.choices?.[0]?.message?.content
      || response.data.output?.text
      || ''
  }
  return ''
}

// AI对话流式接口（SSE）
// 前端要看到"逐字生成"的效果，就必须走流式；一次性返回拿不到中间过程。
// 事件类型：
//   stage  —— 处理阶段提示（组装上下文 / 调用模型 / 解析结果）
//   delta  —— 增量文本
//   done   —— 结束，附带 provider
//   error  —— 出错，附带可读原因
export const chatStream = asyncHandler(async (req, res) => {
  const { model, messages, context } = req.body

  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8')
  res.setHeader('Cache-Control', 'no-cache, no-transform')
  res.setHeader('Connection', 'keep-alive')
  // 关掉 nginx 缓冲，否则 SSE 会被攒着一次性吐出来
  res.setHeader('X-Accel-Buffering', 'no')
  res.flushHeaders?.()

  const send = (event, data) => {
    res.write(`event: ${event}\n`)
    res.write(`data: ${JSON.stringify(data)}\n\n`)
  }

  if (!model || !messages || !Array.isArray(messages)) {
    send('error', { message: '参数错误：需要 model 和 messages' })
    return res.end()
  }

  const apiKeys = getAIKeys()
  const preferredProvider = model === 'qwen' ? 'qwen' : 'glm'
  const tryOrder = [preferredProvider]
  if (preferredProvider === 'glm' && apiKeys.qwen) tryOrder.push('qwen')
  if (preferredProvider === 'qwen' && apiKeys.glm) tryOrder.push('glm')

  send('stage', { text: '正在汇总传感器数据与历史对话' })

  const systemMessage = {
    role: 'system',
    content: chatSystemPrompt(context)
  }
  const formattedMessages = [systemMessage, ...messages.map(m => ({
    role: m.role === 'assistant' ? 'assistant' : m.role,
    content: m.content
  }))]

  let lastErr = null
  for (const provider of tryOrder) {
    const apiKey = apiKeys[provider]
    if (!apiKey) continue
    try {
      send('stage', { text: `正在调用 ${provider === 'glm' ? '智谱 GLM' : '通义千问'} 生成回复` })
      const full = await streamChatProvider(provider, apiKey, formattedMessages, (delta) => {
        send('delta', { text: delta })
      })
      send('done', { provider, chars: full.length })
      return res.end()
    } catch (err) {
      lastErr = err
      logger.warn(`${provider} 流式对话失败，尝试降级`, { error: err.message })
    }
  }
  send('error', { message: lastErr?.message || '所有AI服务均不可用' })
  res.end()
})

/**
 * 调 GLM/通义的流式接口，把增量文本逐块回调出去。
 * 非流式可用时降级为一次性返回，保证功能不挂。
 */
async function streamChatProvider(provider, apiKey, messages, onDelta) {
  const config = AI_PROVIDERS[provider]
  const headers = {
    'Authorization': `Bearer ${apiKey}`,
    'Content-Type': 'application/json; charset=utf-8'
  }

  if (provider === 'glm') {
    const response = await axios.post(
      `${config.baseUrl}${config.chatEndpoint}`,
      {
        model: config.model,
        messages,
        stream: true,
        temperature: 0.7,
        max_tokens: 2000
      },
      { headers, timeout: 60000, responseType: 'stream', maxRedirects: 0 }
    )
    return consumeSse(response.data, (obj) => {
      const d = obj?.choices?.[0]?.delta?.content
      if (d) onDelta(d)
    })
  }

  // 通义的流式协议与 GLM 不同，这里先走非流式再整段吐出，
  // 保证功能可用；GLM 是默认提供商，流式体验优先保证它。
  const response = await axios.post(
    `${config.baseUrl}${config.chatEndpoint}`,
    {
      model: config.model,
      input: { messages },
      parameters: { temperature: 0.7, max_tokens: 2000, result_format: 'message' }
    },
    { headers, timeout: 60000 }
  )
  const text = response.data.output?.choices?.[0]?.message?.content
    || response.data.output?.text || ''
  if (text) onDelta(text)
  return text
}

/** 逐行解析 SSE 流，转成对象后交给 onMessage */
function consumeSse(stream, onMessage) {
  return new Promise((resolve, reject) => {
    let buffer = ''
    let full = ''
    stream.on('data', (chunk) => {
      buffer += chunk.toString('utf-8')
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const block of parts) {
        for (const line of block.split('\n')) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw || raw === '[DONE]') continue
          try {
            const obj = JSON.parse(raw)
            onMessage(obj)
            const d = obj?.choices?.[0]?.delta?.content
            if (d) full += d
          } catch {
            /* 心跳等非 JSON 行忽略 */
          }
        }
      }
    })
    stream.on('end', () => resolve(full))
    stream.on('error', reject)
  })
}

export default {
  analyzeFire,
  getModels,
  chat,
  chatStream
}
