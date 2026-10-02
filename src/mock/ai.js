import Mock from 'mockjs'
import { shipInfo } from './ship.js'
import { performAiAnalysis } from '../services/aiService.js'

// 大模型智能分析：基于舱室参数和实时数据综合分析
Mock.mock(/\/api\/ai\/analyze/, 'post', function(options) {
  console.log('🎯 Mock拦截到请求:', options.url)
  const body = JSON.parse(options.body || '{}')
  const {
    compartmentId = null,
    latest = { temperature: 48, smoke: 20, oxygen: 20.9, co: 5 },
    history = []
  } = body

  // 获取舱室信息
  const compartment = shipInfo.compartments.find(c => c.id === compartmentId)
  if (!compartment) {
    const errorResult = {
      code: 400,
      message: '舱室不存在',
      data: null
    }
    console.log('📤 Mock返回错误:', errorResult)
    return errorResult
  }

  // 使用Promise包装异步AI调用，只使用AI返回的数据
  const promise = new Promise((resolve) => {
    // 只使用真实AI分析，不使用Mock数据
    performAiAnalysis(compartment, latest, history)
      .then(analysis => {
        if (analysis) {
          console.log(`✅ AI智能分析完成 - 舱室 ${compartment.name}:`, analysis)
          const result = {
            code: 200,
            data: analysis,
            message: 'success'
          }
          console.log('📤 Mock返回AI数据:', JSON.stringify(result, null, 2))
          resolve(result)
        } else {
          // AI返回null，返回错误
          const errorResult = {
            code: 500,
            message: 'AI分析返回空结果，请检查API配置',
            data: null
          }
          console.error('❌ AI分析失败:', errorResult)
          resolve(errorResult) // 使用resolve而不是reject，避免axios错误
        }
      })
      .catch(aiError => {
        console.error('❌ AI服务调用失败:', aiError)
        const errorResult = {
          code: 500,
          message: `AI服务调用失败: ${aiError.message}`,
          data: null
        }
        console.error('📤 Mock返回错误:', errorResult)
        resolve(errorResult) // 使用resolve而不是reject，避免axios错误
      })
  })
  
  console.log('📤 Mock返回Promise:', promise)
  return promise
})

// 大模型智能分析函数（已停用，仅使用AI返回的数据）
// eslint-disable-next-line no-unused-vars
const performIntelligentAnalysis = (compartment, latest, history) => {
  const { name } = compartment
  const { temperature, smoke, oxygen, co } = latest

  // 分析当前数据趋势
  const trend = analyzeTrends(history, latest)
  
  // 基于舱室特性评估风险
  const riskAssessment = assessCompartmentRisk(compartment, latest, trend)
  
  // 生成智能建议
  const recommendations = generateRecommendations(compartment, latest, riskAssessment, trend)
  
  // 计算综合风险等级
  const overallRisk = calculateOverallRisk(riskAssessment, trend)

  return {
    compartmentId: compartment.id,
    compartmentName: name,
    risk: overallRisk,
    summary: generateSummary(compartment, latest),
    advice: recommendations,
    indicators: {
      temperature: temperature,
      smoke: smoke,
      oxygen: oxygen,
      co: co
    },
    analysis: {
      compartmentRisk: riskAssessment,
      trend: trend,
      fireSuppression: compartment.fireSuppression,
      equipment: compartment.equipment,
      area: compartment.area
    }
  }
}

// 分析数据趋势
const analyzeTrends = (history, latest) => {
  if (history.length < 2) return { temp: 'stable', smoke: 'stable', oxygen: 'stable', co: 'stable' }
  
  const temps = history.map(h => Number(h.temperature)).filter(v => !Number.isNaN(v))
  const smokes = history.map(h => Number(h.smoke)).filter(v => !Number.isNaN(v))
  const oxygens = history.map(h => Number(h.oxygen)).filter(v => !Number.isNaN(v))
  const cos = history.map(h => Number(h.co)).filter(v => !Number.isNaN(v))
  
  const analyzeDirection = (values, current) => {
    if (values.length < 3) return 'stable'
    const recent = values.slice(-3)
    const trend = (recent[recent.length - 1] - recent[0]) / recent.length
    const currentTrend = current - recent[recent.length - 1]
    
    if (Math.abs(trend) < 0.1 && Math.abs(currentTrend) < 0.1) return 'stable'
    return trend > 0 || currentTrend > 0 ? 'rising' : 'falling'
  }
  
  return {
    temp: analyzeDirection(temps, Number(latest.temperature)),
    smoke: analyzeDirection(smokes, Number(latest.smoke)),
    oxygen: analyzeDirection(oxygens, Number(latest.oxygen)),
    co: analyzeDirection(cos, Number(latest.co))
  }
}

// 评估舱室风险
const assessCompartmentRisk = (compartment, latest, trend) => {
  const { riskLevel, equipment, area } = compartment
  const { temperature, smoke, oxygen, co } = latest
  
  let riskScore = 0
  const riskFactors = []
  
  // 基础风险等级
  switch (riskLevel) {
  case 'high': riskScore += 0.3; riskFactors.push('高风险舱室'); break
  case 'medium': riskScore += 0.2; riskFactors.push('中等风险舱室'); break
  case 'low': riskScore += 0.1; riskFactors.push('低风险舱室'); break
  }
  
  // 当前数值风险
  if (temperature > 100) { riskScore += 0.3; riskFactors.push('高温') }
  if (smoke > 100) { riskScore += 0.25; riskFactors.push('高烟雾') }
  if (oxygen < 19) { riskScore += 0.2; riskFactors.push('低氧') }
  if (co > 50) { riskScore += 0.25; riskFactors.push('高CO') }
  
  // 趋势风险
  if (trend.temp === 'rising') { riskScore += 0.1; riskFactors.push('温度上升') }
  if (trend.smoke === 'rising') { riskScore += 0.1; riskFactors.push('烟雾增加') }
  if (trend.oxygen === 'falling') { riskScore += 0.1; riskFactors.push('氧气下降') }
  if (trend.co === 'rising') { riskScore += 0.1; riskFactors.push('CO上升') }
  
  // 设备风险
  const highRiskEquipment = ['主发动机', '发电机', '燃油系统', '锅炉']
  const hasHighRiskEquipment = equipment.some(eq => highRiskEquipment.some(he => eq.includes(he)))
  if (hasHighRiskEquipment) { riskScore += 0.15; riskFactors.push('高风险设备') }
  
  // 舱室面积影响
  if (area > 1000) { riskScore += 0.05; riskFactors.push('大面积舱室') }
  
  return {
    score: Math.min(1, riskScore),
    factors: riskFactors,
    level: riskScore > 0.7 ? 'high' : riskScore > 0.4 ? 'medium' : 'low'
  }
}

// 生成智能建议 - 基于损管任务分配流程
const generateRecommendations = (compartment, latest, riskAssessment) => {
  const recommendations = []
  const compartmentName = compartment.name
  
  // 根据舱室类型生成具体的任务分配建议
  
  if (compartmentName.includes('主机舱') || compartmentName.includes('机舱')) {
    // 主机舱燃油泄漏火灾 - 任务分配
    recommendations.push('🚨 决策与任务分配（16-30秒）：')
    recommendations.push('1️⃣ 灭火组（3人）：从甲板T1→主机舱T8，长度80米，预计120秒')
    recommendations.push('2️⃣ 堵漏组（2人）：从甲板T2→泄漏点T9，长度60米，预计90秒')
    recommendations.push('3️⃣ 医疗组（1人）：疏散2名非必要人员，路径T10→T3节点')
    recommendations.push('⚡ 协同执行（31秒-15分钟）：')
    recommendations.push('📌 灭火组：使用CO₂灭火器喷射（角度45°、压力0.6MPa）')
    recommendations.push('📌 堵漏组：先塞木塞再用锤子敲实，覆盖防水布')
    recommendations.push('📌 医疗组：引导撤离，避开掉落燃烧物（10分钟内完成）')
    
  } else if (compartmentName.includes('士兵住舱') || compartmentName.includes('住舱')) {
    // 士兵住舱电器短路火灾 - 任务分配
    recommendations.push('🚨 决策与任务分配（16-30秒）：')
    recommendations.push('1️⃣ 灭火组（2人）：携带干粉灭火器，路径住舱入口T1→火源T2')
    recommendations.push('2️⃣ 电工组（1人）：负责切断电源，路径T1→T2（长度20米，预计30秒）')
    recommendations.push('⚡ 协同执行（31秒-8分钟）：')
    recommendations.push('📌 电工：先切断住舱电源（操作同步至所有控制端）')
    recommendations.push('📌 灭火组：喷射干粉灭火器，烟雾减少70%，能见度5→10米')
    recommendations.push('📌 评估：任务完成时间6分钟，路径规划无拥堵')
    
  } else if (compartmentName.includes('机库')) {
    // 机库舰载机燃油泄漏火灾 - 任务分配
    recommendations.push('🚨 决策与任务分配（16-30秒）：')
    recommendations.push('1️⃣ 灭火组（5人）：2人用泡沫、3人用CO₂，分区域喷射')
    recommendations.push('2️⃣ 冷却组（3人）：携带冷却水枪，对油箱喷水降温')
    recommendations.push('3️⃣ 疏散组（2人）：引导3名人员撤离机库')
    recommendations.push('⚡ 协同执行（31秒-20分钟）：')
    recommendations.push('📌 冷却组：先对油箱喷水（温度700→500℃）')
    recommendations.push('📌 灭火组：泡沫覆盖机翼、CO₂覆盖油箱')
    recommendations.push('📌 疏散组：引导撤离，爆炸风险降至10%')
    
  } else if (compartmentName.includes('电站')) {
    // 电站间电器火灾 - 任务分配
    recommendations.push('🚨 决策与任务分配（16-30秒）：')
    recommendations.push('1️⃣ 断电组（1人）：切断总电源，停止配电柜运行')
    recommendations.push('2️⃣ 灭火组（2人）：使用气体灭火系统，路径T1→配电区T2')
    recommendations.push('3️⃣ 监控组（1人）：实时监测UPS系统和电池组状态')
    recommendations.push('⚡ 协同执行：')
    recommendations.push('📌 断电组：先切断总电源，防止电弧扩大')
    recommendations.push('📌 灭火组：启动气体灭火系统（CO₂），保护配电设备')
    recommendations.push('📌 监控组：实时监测温度下降，确认灭火效果')
    
  } else if (compartmentName.includes('灶炉')) {
    // 灶炉间燃气火灾 - 任务分配
    recommendations.push('🚨 决策与任务分配（16-30秒）：')
    recommendations.push('1️⃣ 燃气组（1人）：立即切断燃气供应')
    recommendations.push('2️⃣ 灭火组（2人）：使用干粉灭火器，处理油脂火灾')
    recommendations.push('3️⃣ 通风组（1人）：开启排烟系统，降低烟雾浓度')
    recommendations.push('⚡ 协同执行：')
    recommendations.push('📌 燃气组：关闭燃气总阀门，防止继续泄漏')
    recommendations.push('📌 灭火组：喷射干粉，覆盖灶台和油烟机')
    recommendations.push('📌 通风组：启动排烟，降低温度和烟雾')
    
  } else {
    // 通用任务分配
    if (riskAssessment.level === 'high') {
      recommendations.push('🚨 立即启动应急响应：')
      recommendations.push('1️⃣ 灭火组：使用合适灭火器（CO₂/泡沫/干粉）')
      recommendations.push('2️⃣ 疏散组：引导人员撤离，确保通道畅通')
      recommendations.push('3️⃣ 监控组：实时监测温度、烟雾、气体浓度')
    } else {
      recommendations.push('⚠️ 预防措施：')
      recommendations.push('✅ 加强监控频率')
      recommendations.push('✅ 准备应急物资')
      recommendations.push('✅ 安排人员值守')
    }
  }
  
  return recommendations
}

// 计算综合风险等级
const calculateOverallRisk = (riskAssessment, trend) => {
  let riskLevel = riskAssessment.level
  
  // 如果趋势显示风险快速上升，提升风险等级
  const criticalTrends = [trend.temp, trend.smoke, trend.co].filter(t => t === 'rising').length
  if (criticalTrends >= 2 && riskLevel === 'medium') riskLevel = 'high'
  if (criticalTrends >= 1 && riskLevel === 'low') riskLevel = 'medium'
  
  return riskLevel
}

// 生成智能摘要 - 基于损管流程
const generateSummary = (compartment, latest) => {
  const { name } = compartment
  const { temperature, smoke, co } = latest
  
  // 根据舱室名称确定场景类型和详情
  let sceneInfo = ''
  let prediction = ''
  
  if (name.includes('主机舱') || name.includes('机舱')) {
    // 主机舱燃油泄漏火灾（极高风险场景）
    const riskZone = temperature > 400 ? '极高风险区' : temperature > 250 ? '高风险区' : '中等风险区'
    const tempDesc = temperature > 500 ? '700℃' : temperature > 350 ? '550℃' : '350℃'
    
    sceneInfo = '场景类型：主机舱燃油泄漏火灾\n' +
      '火源位置：主机舱中部（坐标 X=10m,Y=5m,Z=1m），热释放速率 ' + (temperature / 2).toFixed(1) + 'kW/m²\n' +
      '初始风险：火灾发生约5分钟后，主机舱中部为' + riskZone + '（温度' + tempDesc + '、CO浓度' + (co / 1000).toFixed(4) + 'mol/mol）'
    
    prediction = '态势预判：LSTM模型预测未来15秒' + riskZone + '将扩大至' + (temperature > 400 ? '8米' : '5米') + '范围（温度可达' + (Number(tempDesc) + 50) + '℃）、CO浓度将升至' + ((co + 30) / 1000).toFixed(4) + 'mol/mol，燃油泄漏点将向主机蔓延（速度0.3m/s）'
    
  } else if (name.includes('士兵住舱') || name.includes('住舱')) {
    // 士兵住舱电器短路火灾（中高风险场景）
    const riskZone = temperature > 250 ? '高风险区' : '中风险区'
    
    sceneInfo = '场景类型：士兵住舱电器短路火灾\n' +
      '火源位置：住舱储物柜（坐标 X=4m,Y=2m,Z=1.5m），热释放速率 ' + (temperature * 1.5).toFixed(1) + 'kW/m²\n' +
      '初始风险：火灾发生约3分钟后，储物柜周边为' + riskZone + '（温度' + temperature + '℃、CO浓度' + (co / 1000).toFixed(4) + 'mol/mol），住舱通道为中风险区（温度' + Math.min(180, temperature * 0.6) + '℃）'
    
    prediction = '态势预判：LSTM模型预测未来15秒' + riskZone + '将扩大至' + (temperature > 250 ? '4米' : '3米') + '（温度' + (temperature + 50) + '℃）、烟雾将遮挡通道' + (smoke > 50 ? '70%' : '50%')
    
  } else if (name.includes('机库')) {
    // 机库舰载机燃油泄漏火灾（复杂风险场景）
    const riskZone = temperature > 500 ? '极高风险区' : temperature > 250 ? '高风险区' : '中风险区'
    
    sceneInfo = '场景类型：机库舰载机燃油泄漏火灾\n' +
      '火源位置：机库舰载机机翼（坐标 X=20m,Y=8m,Z=3m），热释放速率 ' + (temperature * 2.5).toFixed(1) + 'kW/m²\n' +
      '初始风险：火灾发生约8分钟后，舰载机周边为' + riskZone + '（温度' + temperature + '℃、CO浓度' + (co / 1000).toFixed(4) + 'mol/mol）'
    
    prediction = '态势预判：LSTM模型预测未来15秒' + riskZone + '将扩大至' + (temperature > 500 ? '12米' : '8米') + '（温度' + (temperature + 100) + '℃）、燃油油箱有爆炸风险（概率' + (co > 80 ? '60%' : '30%') + '）'
    
  } else if (name.includes('电站')) {
    // 电站间电器火灾
    const riskZone = temperature > 300 ? '高风险区' : '中风险区'
    
    sceneInfo = '场景类型：电站间电器短路火灾\n' +
      '火源位置：电站间配电柜（坐标 X=8m,Y=4m,Z=2m），热释放速率 ' + (temperature * 2).toFixed(1) + 'kW/m²\n' +
      '初始风险：电站间配电区域为' + riskZone + '（温度' + temperature + '℃、CO浓度' + (co / 1000).toFixed(4) + 'mol/mol）'
    
    prediction = '态势预判：LSTM模型预测未来15秒' + riskZone + '将扩大至' + (temperature > 300 ? '5米' : '3米') + '（温度' + (temperature + 80) + '℃）、电弧将引燃附近线路（概率40%）'
    
  } else if (name.includes('灶炉')) {
    // 灶炉间燃气火灾
    const riskZone = temperature > 350 ? '高风险区' : '中风险区'
    
    sceneInfo = '场景类型：灶炉间燃气泄漏火灾\n' +
      '火源位置：灶炉间（坐标 X=6m,Y=3m,Z=1.5m），热释放速率 ' + (temperature * 1.8).toFixed(1) + 'kW/m²\n' +
      '初始风险：灶炉间为' + riskZone + '（温度' + temperature + '℃、烟雾浓度' + smoke + 'ppm）'
    
    prediction = '态势预判：LSTM模型预测未来15秒' + riskZone + '将扩大至' + (temperature > 350 ? '4米' : '2.5米') + '（温度' + (temperature + 60) + '℃）、高温油烟可能点燃附近物品'
    
  } else {
    // 通用场景
    sceneInfo = name + '发生火灾，当前温度为' + temperature + '℃、烟雾' + smoke + 'ppm、CO浓度' + co + 'ppm'
    prediction = '态势预判：温度上升中，风险区域扩大'
  }
  
  let summary = sceneInfo + '\n\n' + prediction + '\n\n损管需求：'
  
  // 根据舱室和风险等级添加损管需求
  if (name.includes('主机舱')) {
    summary += '快速灭火、封堵燃油泄漏点、疏散舱内非必要人员'
  } else if (name.includes('士兵住舱')) {
    summary += '扑灭电器火灾、切断电源、清理通道烟雾'
  } else if (name.includes('机库')) {
    summary += '扑灭大火、冷却舰载机、防止燃油爆炸、疏散机库人员'
  } else if (name.includes('电站')) {
    summary += '切断电源、扑灭电器火灾、保护配电设备、疏散人员'
  } else if (name.includes('灶炉')) {
    summary += '切断燃气供应、扑灭火灾、通风排烟、保护人员'
  } else {
    summary += '快速灭火、疏散人员、保护重要设备'
  }
  
  return summary
}

// 模型列表
Mock.mock('/api/ai/models', 'get', () => {
  return {
    code: 200,
    data: [
      { id: 'gpt-4o-mini', name: 'GPT-4o Mini', capability: ['chat','analysis','simulate'] },
      { id: 'glm-4-air', name: 'GLM-4 Air', capability: ['chat','analysis'] },
      { id: 'qwen-turbo', name: '通义千问 Turbo', capability: ['chat','analysis','simulate'] }
    ],
    message: 'success'
  }
})

// 聊天接口（简化回声+规则增强）
Mock.mock('/api/ai/chat', 'post', (options) => {
  try {
    const body = JSON.parse(options.body || '{}')
    const { model = 'gpt-4o-mini', messages = [], context = {} } = body
    const last = messages[messages.length - 1]?.content || ''
    const prefix = `[${model}] `
    let reply = prefix + '已收到：' + last
    if (context?.telemetry) {
      const t = context.telemetry
      reply += `。当前温度${t.temperature?.toFixed?.(1) ?? t.temperature}℃、烟雾${t.smoke?.toFixed?.(1) ?? t.smoke}ppm、氧气${t.oxygen?.toFixed?.(1) ?? t.oxygen}%、CO ${t.co?.toFixed?.(1) ?? t.co}ppm。`
    }
    return { code: 200, data: { reply }, message: 'success' }
  } catch (e) {
    return { code: 500, message: e.message || 'chat error' }
  }
})

// 根据当前参数生成未来30个点的温度/烟雾/氧气/CO曲线（简化物理+随机扰动）
Mock.mock('/api/ai/simulate', 'post', (options) => {
  try {
    const body = JSON.parse(options.body || '{}')
    const { latest = { temperature: 48, smoke: 20, oxygen: 20.9, co: 5 }, duration = 300, step = 10 } = body
    const n = Math.max(1, Math.min(300, Math.floor(duration / step)))
    const series = { time: [], temperature: [], smoke: [], oxygen: [], co: [] }
    let temp = Number(latest.temperature || 48)
    let smoke = Number(latest.smoke || 20)
    let oxygen = Number(latest.oxygen || 20.9)
    let co = Number(latest.co || 5)
    for (let i = 1; i <= n; i++) {
      const t = i * step
      // 简化：若温度>100 继续上升趋向 700，否则缓升
      const tempTarget = temp > 100 ? 700 : 200
      temp = temp + (tempTarget - temp) * 0.08 + (Math.random() - 0.5) * 3
      smoke = smoke + (500 - smoke) * 0.06 + (Math.random() - 0.5) * 5
      oxygen = Math.max(12, oxygen - 0.03 * (temp / 200) + (Math.random() - 0.5) * 0.02)
      co = co + (200 - co) * 0.05 + (Math.random() - 0.5) * 2
      series.time.push(t)
      series.temperature.push(Number(temp.toFixed(1)))
      series.smoke.push(Number(smoke.toFixed(1)))
      series.oxygen.push(Number(oxygen.toFixed(2)))
      series.co.push(Number(co.toFixed(1)))
    }
    return { code: 200, data: series, message: 'success' }
  } catch (e) {
    return { code: 500, message: e.message || 'simulate error' }
  }
})


