/**
 * 知识图谱服务
 * 用于存储和查询船舶火灾相关的实体关系和知识
 * 优化AI分析的上下文信息
 */

/**
 * 知识图谱数据结构
 */
class KnowledgeGraph {
  constructor() {
    // 实体存储
    this.entities = new Map()
    
    // 关系存储：Map<sourceId, Map<relationType, Set<targetId>>>
    this.relations = new Map()
    
    // 反向关系索引：Map<targetId, Map<relationType, Set<sourceId>>>
    this.reverseRelations = new Map()
    
    // 初始化知识图谱
    this.initialize()
  }

  /**
   * 初始化知识图谱
   */
  initialize() {
    // 1. 定义舱室实体
    const compartments = [
      { id: 'compartment_1', name: '主机舱', type: 'engine', area: 120, location: '底部', risk: 0.8 },
      { id: 'compartment_2', name: '辅机舱', type: 'engine', area: 80, location: '底部', risk: 0.7 },
      { id: 'compartment_3', name: '货舱A区', type: 'cargo', area: 150, location: '中部', risk: 0.5 },
      { id: 'compartment_4', name: '货舱B区', type: 'cargo', area: 150, location: '中部', risk: 0.5 },
      { id: 'compartment_5', name: '货舱C区', type: 'cargo', area: 150, location: '中部', risk: 0.5 },
      { id: 'compartment_6', name: '驾驶室', type: 'electrical', area: 40, location: '顶部', risk: 0.3 },
      { id: 'compartment_7', name: '生活区', type: 'electrical', area: 60, location: '中部', risk: 0.4 },
      { id: 'compartment_8', name: '燃油舱', type: 'fuel', area: 100, location: '底部', risk: 0.9 },
      { id: 'compartment_9', name: '电站间', type: 'electrical', area: 50, location: '中部', risk: 0.6 },
      { id: 'compartment_10', name: '机库', type: 'hangar', area: 200, location: '中部', risk: 0.5 },
      { id: 'compartment_11', name: '士兵住舱', type: 'living', area: 80, location: '中部', risk: 0.4 },
      { id: 'compartment_12', name: '灶炉间', type: 'galley', area: 30, location: '中部', risk: 0.7 }
    ]

    compartments.forEach(comp => {
      this.addEntity('compartment', comp.id, comp)
    })

    // 2. 定义设备实体
    const equipment = [
      { id: 'equipment_1', name: '主发动机', type: 'engine', risk: 0.9, location: 'compartment_1' },
      { id: 'equipment_2', name: '发电机', type: 'generator', risk: 0.8, location: 'compartment_1' },
      { id: 'equipment_3', name: '燃油系统', type: 'fuel_system', risk: 0.9, location: 'compartment_1' },
      { id: 'equipment_4', name: '辅助发电机', type: 'generator', risk: 0.7, location: 'compartment_2' },
      { id: 'equipment_5', name: '空压机', type: 'compressor', risk: 0.6, location: 'compartment_2' },
      { id: 'equipment_6', name: '锅炉', type: 'boiler', risk: 0.8, location: 'compartment_2' },
      { id: 'equipment_7', name: '燃油泵', type: 'pump', risk: 0.9, location: 'compartment_8' },
      { id: 'equipment_8', name: '导航系统', type: 'navigation', risk: 0.4, location: 'compartment_6' },
      { id: 'equipment_9', name: '通信设备', type: 'communication', risk: 0.3, location: 'compartment_6' },
      { id: 'equipment_10', name: '厨房设备', type: 'kitchen', risk: 0.7, location: 'compartment_7' },
      { id: 'equipment_11', name: '集装箱', type: 'container', risk: 0.5, location: 'compartment_3' },
      { id: 'equipment_12', name: '通风系统', type: 'ventilation', risk: 0.4, location: 'compartment_3' }
    ]

    equipment.forEach(eq => {
      this.addEntity('equipment', eq.id, eq)
      // 建立设备-舱室关系
      this.addRelation(eq.location, 'contains', eq.id)
    })

    // 3. 定义火灾类型实体
    const fireTypes = [
      { id: 'fire_electrical', name: '电气火灾', intensity: 0.8, spreadRate: 0.3, temperature: 600, smokeLevel: 0.6 },
      { id: 'fire_fuel', name: '燃油火灾', intensity: 1.0, spreadRate: 0.8, temperature: 800, smokeLevel: 0.9 },
      { id: 'fire_cargo', name: '货物火灾', intensity: 0.7, spreadRate: 0.5, temperature: 500, smokeLevel: 0.7 },
      { id: 'fire_engine', name: '发动机火灾', intensity: 0.9, spreadRate: 0.4, temperature: 700, smokeLevel: 0.8 },
      { id: 'fire_kitchen', name: '厨房火灾', intensity: 0.7, spreadRate: 0.6, temperature: 550, smokeLevel: 0.7 }
    ]

    fireTypes.forEach(ft => {
      this.addEntity('fireType', ft.id, ft)
    })

    // 4. 定义灭火方法实体
    const suppressions = [
      { id: 'suppression_co2', name: 'CO2', effectiveness: 0.9, suitableFor: ['fire_electrical', 'fire_engine', 'fire_fuel'], sideEffects: ['需要通风'] },
      { id: 'suppression_foam', name: '泡沫', effectiveness: 0.8, suitableFor: ['fire_fuel', 'fire_engine'], sideEffects: ['需要清理'] },
      { id: 'suppression_water', name: '水雾', effectiveness: 0.7, suitableFor: ['fire_cargo', 'fire_kitchen'], sideEffects: ['可能加剧浸水'] },
      { id: 'suppression_powder', name: '干粉', effectiveness: 0.85, suitableFor: ['fire_electrical', 'fire_fuel'], sideEffects: ['需要清理'] }
    ]

    suppressions.forEach(sup => {
      this.addEntity('suppression', sup.id, sup)
      // 建立灭火方法-火灾类型关系
      sup.suitableFor.forEach(fireTypeId => {
        this.addRelation(sup.id, 'canSuppress', fireTypeId)
      })
    })

    // 5. 建立设备-火灾类型关系
    const equipmentFireRelations = [
      ['equipment_1', 'fire_engine'], // 主发动机 -> 发动机火灾
      ['equipment_2', 'fire_electrical'], // 发电机 -> 电气火灾
      ['equipment_3', 'fire_fuel'], // 燃油系统 -> 燃油火灾
      ['equipment_4', 'fire_electrical'], // 辅助发电机 -> 电气火灾
      ['equipment_5', 'fire_electrical'], // 空压机 -> 电气火灾
      ['equipment_6', 'fire_engine'], // 锅炉 -> 发动机火灾
      ['equipment_7', 'fire_fuel'], // 燃油泵 -> 燃油火灾
      ['equipment_8', 'fire_electrical'], // 导航系统 -> 电气火灾
      ['equipment_9', 'fire_electrical'], // 通信设备 -> 电气火灾
      ['equipment_10', 'fire_kitchen'], // 厨房设备 -> 厨房火灾
      ['equipment_11', 'fire_cargo'] // 集装箱 -> 货物火灾
    ]

    equipmentFireRelations.forEach(([equipmentId, fireTypeId]) => {
      this.addRelation(equipmentId, 'canCause', fireTypeId)
    })

    // 6. 建立舱室-灭火方法关系
    const compartmentSuppressionRelations = [
      ['compartment_1', 'suppression_co2'], // 主机舱 -> CO2
      ['compartment_1', 'suppression_foam'], // 主机舱 -> 泡沫
      ['compartment_2', 'suppression_co2'], // 辅机舱 -> CO2
      ['compartment_3', 'suppression_water'], // 货舱A区 -> 水雾
      ['compartment_4', 'suppression_water'], // 货舱B区 -> 水雾
      ['compartment_5', 'suppression_water'], // 货舱C区 -> 水雾
      ['compartment_6', 'suppression_co2'], // 驾驶室 -> CO2
      ['compartment_6', 'suppression_powder'], // 驾驶室 -> 干粉
      ['compartment_7', 'suppression_powder'], // 生活区 -> 干粉
      ['compartment_8', 'suppression_foam'], // 燃油舱 -> 泡沫
      ['compartment_9', 'suppression_co2'], // 电站间 -> CO2
      ['compartment_9', 'suppression_powder'], // 电站间 -> 干粉
      ['compartment_12', 'suppression_water'] // 灶炉间 -> 水雾
    ]

    compartmentSuppressionRelations.forEach(([compartmentId, suppressionId]) => {
      this.addRelation(compartmentId, 'hasSuppression', suppressionId)
    })

    // 7. 建立舱室相邻关系（影响传播）
    const adjacentCompartments = [
      ['compartment_1', 'compartment_2'], // 主机舱 <-> 辅机舱
      ['compartment_1', 'compartment_8'], // 主机舱 <-> 燃油舱
      ['compartment_2', 'compartment_9'], // 辅机舱 <-> 电站间
      ['compartment_3', 'compartment_4'], // 货舱A区 <-> 货舱B区
      ['compartment_4', 'compartment_5'], // 货舱B区 <-> 货舱C区
      ['compartment_6', 'compartment_7'], // 驾驶室 <-> 生活区
      ['compartment_7', 'compartment_12'] // 生活区 <-> 灶炉间
    ]

    adjacentCompartments.forEach(([comp1, comp2]) => {
      this.addRelation(comp1, 'adjacentTo', comp2, { bidirectional: true })
    })

    console.log('✅ 知识图谱初始化完成', {
      entities: this.entities.size,
      relations: this.relations.size
    })
  }

  /**
   * 添加实体
   */
  addEntity(type, id, properties) {
    const entity = {
      type,
      id,
      ...properties,
      createdAt: Date.now()
    }
    this.entities.set(id, entity)
    return entity
  }

  /**
   * 获取实体
   */
  getEntity(id) {
    return this.entities.get(id)
  }

  /**
   * 添加关系
   */
  addRelation(sourceId, relationType, targetId, options = {}) {
    // 正向关系
    if (!this.relations.has(sourceId)) {
      this.relations.set(sourceId, new Map())
    }
    if (!this.relations.get(sourceId).has(relationType)) {
      this.relations.get(sourceId).set(relationType, new Set())
    }
    this.relations.get(sourceId).get(relationType).add(targetId)

    // 双向关系
    if (options.bidirectional) {
      if (!this.relations.has(targetId)) {
        this.relations.set(targetId, new Map())
      }
      if (!this.relations.get(targetId).has(relationType)) {
        this.relations.get(targetId).set(relationType, new Set())
      }
      this.relations.get(targetId).get(relationType).add(sourceId)
    }

    // 反向关系索引
    if (!this.reverseRelations.has(targetId)) {
      this.reverseRelations.set(targetId, new Map())
    }
    if (!this.reverseRelations.get(targetId).has(relationType)) {
      this.reverseRelations.get(targetId).set(relationType, new Set())
    }
    this.reverseRelations.get(targetId).get(relationType).add(sourceId)
  }

  /**
   * 查询关系
   */
  getRelations(sourceId, relationType = null) {
    const sourceRelations = this.relations.get(sourceId)
    if (!sourceRelations) return new Map()

    if (relationType) {
      const targets = sourceRelations.get(relationType)
      return targets ? new Map([[relationType, targets]]) : new Map()
    }
    return sourceRelations
  }

  /**
   * 查询反向关系
   */
  getReverseRelations(targetId, relationType = null) {
    const reverseRels = this.reverseRelations.get(targetId)
    if (!reverseRels) return new Map()

    if (relationType) {
      const sources = reverseRels.get(relationType)
      return sources ? new Map([[relationType, sources]]) : new Map()
    }
    return reverseRels
  }

  /**
   * 查询相邻实体（通过关系）
   */
  findRelatedEntities(entityId, relationTypes = [], maxDepth = 2, visited = new Set()) {
    if (maxDepth <= 0 || visited.has(entityId)) {
      return []
    }

    visited.add(entityId)
    const results = []

    const relations = this.getRelations(entityId)
    relations.forEach((targets, relType) => {
      if (relationTypes.length === 0 || relationTypes.includes(relType)) {
        targets.forEach(targetId => {
          const entity = this.getEntity(targetId)
          if (entity) {
            results.push({
              entity,
              relation: relType,
              depth: 1
            })

            // 递归查询（限制深度）
            if (maxDepth > 1) {
              const nested = this.findRelatedEntities(targetId, relationTypes, maxDepth - 1, new Set(visited))
              nested.forEach(item => {
                results.push({
                  ...item,
                  depth: item.depth + 1
                })
              })
            }
          }
        })
      }
    })

    return results
  }

  /**
   * 根据舱室ID获取完整上下文信息
   */
  getCompartmentContext(compartmentId) {
    // 转换为知识图谱ID格式（compartment_X）
    let kgId
    if (typeof compartmentId === 'number') {
      kgId = `compartment_${compartmentId}`
    } else if (compartmentId.startsWith('compartment_')) {
      kgId = compartmentId
    } else {
      // 尝试从字符串中提取数字
      const numMatch = compartmentId.match(/\d+/)
      kgId = numMatch ? `compartment_${numMatch[0]}` : `compartment_${compartmentId}`
    }
    
    const compartment = this.entities.get(kgId)
    if (!compartment) {
      console.warn(`⚠️ 舱室 ${compartmentId} 不在知识图谱中`)
      return null
    }

    const context = {
      compartment,
      equipment: [],
      fireTypes: [],
      suppressions: [],
      adjacentCompartments: [],
      riskAnalysis: {
        baseRisk: compartment.risk || 0.5,
        potentialFireTypes: [],
        recommendedSuppressions: []
      }
    }

    // 查询设备
    const equipmentRelations = this.getRelations(kgId, 'contains')
    if (equipmentRelations && equipmentRelations.get('contains')) {
      equipmentRelations.get('contains').forEach(eqId => {
        const eq = this.getEntity(eqId)
        if (eq) {
          context.equipment.push(eq)
          
          // 查询设备可能导致的火灾类型
          const fireRelations = this.getRelations(eqId, 'canCause')
          if (fireRelations && fireRelations.get('canCause')) {
            fireRelations.get('canCause').forEach(fireTypeId => {
              const fireType = this.getEntity(fireTypeId)
              if (fireType && !context.fireTypes.find(ft => ft.id === fireTypeId)) {
                context.fireTypes.push(fireType)
              }
            })
          }
        }
      })
    }

    // 查询灭火方法
    const suppressionRelations = this.getRelations(kgId, 'hasSuppression')
    if (suppressionRelations && suppressionRelations.get('hasSuppression')) {
      suppressionRelations.get('hasSuppression').forEach(supId => {
        const sup = this.getEntity(supId)
        if (sup) {
          context.suppressions.push(sup)
        }
      })
    }

    // 查询相邻舱室
    const adjacentRelations = this.getRelations(kgId, 'adjacentTo')
    if (adjacentRelations && adjacentRelations.get('adjacentTo')) {
      adjacentRelations.get('adjacentTo').forEach(adjId => {
        const adj = this.getEntity(adjId)
        if (adj) {
          context.adjacentCompartments.push(adj)
        }
      })
    }

    // 风险分析
    context.riskAnalysis.potentialFireTypes = context.fireTypes.map(ft => ({
      type: ft.name,
      risk: ft.intensity * compartment.risk,
      intensity: ft.intensity,
      spreadRate: ft.spreadRate
    }))

    context.riskAnalysis.recommendedSuppressions = context.suppressions.map(sup => ({
      name: sup.name,
      effectiveness: sup.effectiveness,
      sideEffects: sup.sideEffects || []
    }))

    return context
  }

  /**
   * 推理：根据传感器数据推断可能的火灾类型
   */
  inferFireType(compartmentId, sensorData) {
    // 统一处理compartmentId格式
    let kgId
    if (typeof compartmentId === 'number') {
      kgId = `compartment_${compartmentId}`
    } else if (compartmentId.startsWith('compartment_')) {
      kgId = compartmentId
    } else {
      const numMatch = compartmentId.match(/\d+/)
      kgId = numMatch ? `compartment_${numMatch[0]}` : `compartment_${compartmentId}`
    }
    
    const context = this.getCompartmentContext(kgId)
    if (!context) return null

    const { temperature, smoke, co } = sensorData
    
    // 根据传感器数据和设备类型推理
    const scores = context.fireTypes.map(ft => {
      let score = 0
      
      // 温度匹配度
      const tempDiff = Math.abs(temperature - ft.temperature)
      if (tempDiff < 100) score += 3
      else if (tempDiff < 200) score += 2
      else if (tempDiff < 300) score += 1
      
      // 烟雾匹配度
      const smokeDiff = Math.abs(smoke - ft.smokeLevel * 100)
      if (smokeDiff < 10) score += 2
      else if (smokeDiff < 20) score += 1
      
      // CO匹配度（燃油和发动机火灾CO高）
      if (ft.id.includes('fuel') || ft.id.includes('engine')) {
        if (co > 50) score += 2
        else if (co > 20) score += 1
      }
      
      return { fireType: ft, score }
    })

    // 排序，返回最可能的火灾类型
    scores.sort((a, b) => b.score - a.score)
    return scores[0]?.fireType || null
  }

  /**
   * 推理：评估影响范围
   */
  assessImpactScope(compartmentId, fireType) {
    // 统一处理compartmentId格式
    let kgId
    if (typeof compartmentId === 'number') {
      kgId = `compartment_${compartmentId}`
    } else if (compartmentId.startsWith('compartment_')) {
      kgId = compartmentId
    } else {
      const numMatch = compartmentId.match(/\d+/)
      kgId = numMatch ? `compartment_${numMatch[0]}` : `compartment_${compartmentId}`
    }
    
    const context = this.getCompartmentContext(kgId)
    if (!context) return []

    const impactScope = []
    
    // 评估相邻舱室风险
    context.adjacentCompartments.forEach(adj => {
      const risk = adj.risk * 0.5 // 相邻舱室风险降低50%
      impactScope.push({
        compartment: adj,
        risk,
        threat: `${adj.name}可能受到${context.compartment.name}火灾影响`,
        recommendedActions: [
          `监控${adj.name}的温度和烟雾浓度`,
          `准备${adj.name}的防火措施`
        ]
      })
    })

    // 评估特定设备的风险
    context.equipment.forEach(eq => {
      if (eq.risk > 0.7) {
        impactScope.push({
          equipment: eq,
          risk: eq.risk,
          threat: `${eq.name}在${context.compartment.name}中，火灾可能导致设备损坏`,
          recommendedActions: [
            `优先保护${eq.name}`,
            `检查${eq.name}的运行状态`
          ]
        })
      }
    })

    // 调用方传了火源类型就一并带进结果，评估结论要能追溯到"是哪类火"
    if (fireType) {
      impactScope.forEach(item => { item.fireType = fireType })
    }

    return impactScope
  }

  /**
   * 生成知识图谱增强的分析提示
   */
  generateEnhancedPrompt(compartmentId, sensorData) {
    // 统一处理compartmentId格式
    let kgId
    if (typeof compartmentId === 'number') {
      kgId = `compartment_${compartmentId}`
    } else if (compartmentId.startsWith('compartment_')) {
      kgId = compartmentId
    } else {
      const numMatch = compartmentId.match(/\d+/)
      kgId = numMatch ? `compartment_${numMatch[0]}` : `compartment_${compartmentId}`
    }
    
    const context = this.getCompartmentContext(kgId)
    if (!context) return ''

    const inferredFireType = this.inferFireType(kgId, sensorData)
    const impactScope = this.assessImpactScope(kgId, inferredFireType)

    let prompt = '\n## 知识图谱增强信息\n'

    // 舱室基本信息
    prompt += '**舱室知识：**\n'
    prompt += `- 舱室类型：${context.compartment.type}\n`
    prompt += `- 基础风险等级：${(context.compartment.risk * 100).toFixed(0)}%\n`
    prompt += `- 舱室位置：${context.compartment.location}\n`
    prompt += `- 舱室面积：${context.compartment.area}平方米\n\n`

    // 设备信息
    if (context.equipment.length > 0) {
      prompt += '**舱室设备：**\n'
      context.equipment.forEach(eq => {
        prompt += `- ${eq.name}（类型：${eq.type}，风险等级：${(eq.risk * 100).toFixed(0)}%）\n`
      })
      prompt += '\n'
    }

    // 可能的火灾类型
    if (context.fireTypes.length > 0) {
      prompt += '**可能的火灾类型：**\n'
      context.fireTypes.forEach(ft => {
        prompt += `- ${ft.name}（强度：${(ft.intensity * 100).toFixed(0)}%，蔓延速度：${(ft.spreadRate * 100).toFixed(0)}%，典型温度：${ft.temperature}℃，典型烟雾：${(ft.smokeLevel * 100).toFixed(0)}%）\n`
      })
      prompt += '\n'
    }

    // 推理的火灾类型
    if (inferredFireType) {
      prompt += `**推断的火灾类型：**基于当前传感器数据，最可能的火灾类型是${inferredFireType.name}。\n\n`
    }

    // 可用的灭火方法
    if (context.suppressions.length > 0) {
      prompt += '**可用灭火方法：**\n'
      context.suppressions.forEach(sup => {
        prompt += `- ${sup.name}（效果：${(sup.effectiveness * 100).toFixed(0)}%`
        if (sup.sideEffects && sup.sideEffects.length > 0) {
          prompt += `，副作用：${sup.sideEffects.join('、')}`
        }
        prompt += '）\n'
      })
      prompt += '\n'
    }

    // 相邻舱室
    if (context.adjacentCompartments.length > 0) {
      prompt += '**相邻舱室（可能受影响）：**\n'
      context.adjacentCompartments.forEach(adj => {
        prompt += `- ${adj.name}（风险等级：${(adj.risk * 100).toFixed(0)}%）\n`
      })
      prompt += '\n'
    }

    // 影响范围评估
    if (impactScope.length > 0) {
      prompt += '**影响范围评估：**\n'
      impactScope.forEach(impact => {
        if (impact.compartment) {
          prompt += `- ${impact.threat}（风险：${(impact.risk * 100).toFixed(0)}%）\n`
        } else if (impact.equipment) {
          prompt += `- ${impact.threat}（风险：${(impact.risk * 100).toFixed(0)}%）\n`
        }
      })
      prompt += '\n'
    }

    // 建议
    prompt += '**知识图谱建议：**\n'
    prompt += `- 根据舱室类型和位置，${context.compartment.location === '底部' ? '底部舱室浸水风险高，需要同时考虑火灾和浸水双重威胁。' : ''}\n`
    prompt += `- 优先使用知识图谱推荐的灭火方法：${context.suppressions.map(s => s.name).join('、')}。\n`
    if (inferredFireType) {
      prompt += `- 根据推断的火灾类型（${inferredFireType.name}），建议使用${context.suppressions.filter(s => s.suitableFor && s.suitableFor.includes(inferredFireType.id)).map(s => s.name).join('、') || '适当的'}灭火方法。\n`
    }
    if (context.adjacentCompartments.length > 0) {
      prompt += `- 需要监控相邻舱室（${context.adjacentCompartments.map(c => c.name).join('、')}）的状态，防止火灾蔓延。\n`
    }

    return prompt
  }
}

// 创建全局知识图谱实例
const knowledgeGraph = new KnowledgeGraph()

/**
 * 导出知识图谱服务
 */
export default {
  /**
   * 获取舱室上下文
   */
  getCompartmentContext(compartmentId) {
    return knowledgeGraph.getCompartmentContext(compartmentId)
  },

  /**
   * 推理火灾类型
   */
  inferFireType(compartmentId, sensorData) {
    return knowledgeGraph.inferFireType(compartmentId, sensorData)
  },

  /**
   * 评估影响范围
   */
  assessImpactScope(compartmentId, fireType) {
    return knowledgeGraph.assessImpactScope(compartmentId, fireType)
  },

  /**
   * 生成增强的AI分析提示
   */
  generateEnhancedPrompt(compartmentId, sensorData) {
    return knowledgeGraph.generateEnhancedPrompt(compartmentId, sensorData)
  },

  /**
   * 查询实体
   */
  getEntity(id) {
    return knowledgeGraph.getEntity(id)
  },

  /**
   * 查询关系
   */
  getRelations(entityId, relationType) {
    return knowledgeGraph.getRelations(entityId, relationType)
  },

  /**
   * 获取知识图谱统计
   */
  getStats() {
    return {
      entities: knowledgeGraph.entities.size,
      relations: knowledgeGraph.relations.size
    }
  }
}

