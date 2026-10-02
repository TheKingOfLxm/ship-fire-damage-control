import { aiAPI } from '@/api'
import Logger from '@/utils/logger'

/**
 * AI智能分析服务
 * 通过后端API调用国内AI模型进行分析
 */

/**
 * 分析火灾情况
 * @param {Object} params - 分析参数
 * @param {number} params.compartmentId - 舱室ID
 * @param {string} params.compartmentName - 舱室名称
 * @param {string} params.fireType - 火灾类型
 * @param {number} params.temperature - 温度
 * @param {number} params.smoke - 烟雾浓度
 * @param {number} params.oxygen - 氧气浓度
 * @param {number} params.co - CO浓度
 * @param {string} params.provider - AI提供商 (glm/qwen)
 * @returns {Promise} 分析结果
 */
export async function analyzeFire(params) {
  const {
    compartmentId,
    compartmentName,
    fireType,
    temperature,
    smoke,
    oxygen,
    co,
    provider
  } = params

  try {
    Logger.info('开始AI火灾分析', { compartmentId, compartmentName, provider })

    const response = await aiAPI.analyzeFire({
      compartmentId,
      compartmentName,
      fireType,
      temperature,
      smoke,
      oxygen,
      co,
      provider
    })

    // 详细日志：响应结构
    Logger.info('后端响应', {
      status: response.status,
      hasData: !!response.data,
      success: response.data?.success,
      dataType: typeof response.data?.data,
      dataKeys: response.data?.data ? Object.keys(response.data.data) : null
    })

    if (response.data?.success && response.data?.data) {
      const result = response.data.data

      // 格式化返回结果
      // 后端返回的数据结构: {riskLevel, riskScore, fireStage, summary, threats, recommendations, warnings, provider, timestamp}
      const formatted = {
        compartmentId,
        compartmentName,
        timestamp: result.timestamp || new Date().toISOString(),
        riskLevel: result.riskLevel,
        riskScore: result.riskScore,
        fireStage: result.fireStage,
        summary: result.summary,
        threats: result.threats || [],
        recommendations: result.recommendations || [],
        warnings: result.warnings || [],
        provider: result.provider || 'unknown',
        sensorData: {
          temperature,
          smoke,
          oxygen,
          co
        }
      }

      Logger.info('AI火灾分析完成', {
        compartmentId,
        riskLevel: formatted.riskLevel,
        provider: formatted.provider
      })

      return formatted
    } else {
      const errorMsg = response.data?.message || 'AI分析失败'
      Logger.error('后端返回失败', { success: response.data?.success, message: errorMsg })
      throw new Error(errorMsg)
    }
  } catch (error) {
    Logger.error('AI火灾分析异常', { error: error.message, compartmentId })

    // 返回降级分析结果
    return getFallbackAnalysis(params)
  }
}

/**
 * 降级分析（当AI服务不可用时使用）
 */
function getFallbackAnalysis(data) {
  const { compartmentName, temperature, smoke, oxygen, co } = data

  // 风险等级计算
  let riskLevel = 'low'
  let riskScore = 25

  if (temperature > 800 || co > 1200 || oxygen < 10) {
    riskLevel = 'critical'
    riskScore = 95
  } else if (temperature > 500 || co > 500 || oxygen < 15) {
    riskLevel = 'high'
    riskScore = 75
  } else if (temperature > 100 || co > 50 || oxygen < 19) {
    riskLevel = 'medium'
    riskScore = 50
  }

  // 火灾阶段
  const fireStage = temperature > 300 ? '充分发展' : '初期'

  // 威胁分析
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

  const summary = `${compartmentName}当前${
    riskLevel === 'low' ? '相对安全' : '存在' + riskLevel + '风险'
  }，${recommendations[0] || '需持续监控'}`

  return {
    compartmentId: data.compartmentId,
    compartmentName,
    timestamp: new Date().toISOString(),
    analysis: {
      riskLevel,
      riskScore,
      fireStage,
      summary,
      threats,
      recommendations,
      warnings: warnings.length > 0 ? warnings : undefined
    },
    provider: 'fallback',
    sensorData: {
      temperature,
      smoke,
      oxygen,
      co
    }
  }
}

/**
 * 检查AI服务健康状态
 */
export async function checkAIHealth() {
  try {
    // 简单的健康检查：尝试调用一次分析
    const testResult = await aiAPI.analyzeFire({
      compartmentId: 0,
      compartmentName: '测试舱室',
      fireType: 'unknown',
      temperature: 25,
      smoke: 0,
      oxygen: 21,
      co: 0
    })

    return {
      healthy: true,
      provider: testResult.provider || 'unknown',
      message: 'AI服务正常'
    }
  } catch (error) {
    return {
      healthy: false,
      provider: 'none',
      message: error.message || 'AI服务不可用'
    }
  }
}

/**
 * 执行AI分析（兼容旧代码的别名）
 * @param {Object} compartment - 舱室信息
 * @param {Object} latest - 最新传感器数据
 * @param {string} provider - AI提供商 (可选)
 */
export async function performAiAnalysis(compartment, latest, provider) {
  return analyzeFire({
    compartmentId: compartment.id,
    compartmentName: compartment.name,
    fireType: compartment.fireType || 'unknown',
    temperature: latest.temperature,
    smoke: latest.smoke,
    oxygen: latest.oxygen,
    co: latest.co,
    provider
  })
}

export default {
  analyzeFire,
  performAiAnalysis,
  checkAIHealth
}
