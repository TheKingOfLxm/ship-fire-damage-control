import Mock from 'mockjs'

// 警报阈值配置
const alertThresholds = {
  temperature: {
    medium: 50,
    high: 70
  },
  smoke: {
    medium: 150,
    high: 250
  },
  oxygen: {
    medium: 19,
    high: 17
  },
  co: {
    medium: 40,
    high: 80
  }
}

// 生成警报信息
const generateAlert = (compartmentId, type, value) => {
  const compartments = {
    1: '机舱',
    2: '货舱',
    3: '驾驶室'
  }

  const level = value >= alertThresholds[type].high ? 'high' : 'medium'
  const messages = {
    temperature: {
      high: '温度过高，存在严重火灾风险',
      medium: '温度异常，需要密切关注'
    },
    smoke: {
      high: '烟雾浓度严重超标，存在重大火灾隐患',
      medium: '烟雾浓度异常，建议检查'
    },
    oxygen: {
      high: '氧气浓度严重不足，存在窒息风险',
      medium: '氧气浓度偏低，需要注意'
    },
    co: {
      high: '一氧化碳浓度严重超标，存在中毒风险',
      medium: '一氧化碳浓度异常，建议通风'
    }
  }

  return {
    id: Mock.Random.guid(),
    level,
    type,
    value,
    compartment: compartments[compartmentId],
    message: messages[type][level],
    timestamp: new Date().toISOString()
  }
}

// 获取实时警报
Mock.mock(/\/api\/alerts\/realtime\?compartmentId=\d+/, 'get', options => {
  const compartmentId = parseInt(options.url.match(/compartmentId=(\d+)/)[1])
  const alerts = []

  // 随机生成1-3个警报
  const alertCount = Mock.Random.integer(1, 3)
  const types = ['temperature', 'smoke', 'oxygen', 'co']

  for (let i = 0; i < alertCount; i++) {
    const type = types[Mock.Random.integer(0, 3)]
    const value = Mock.Random.float(alertThresholds[type].medium, alertThresholds[type].high * 1.2)
    alerts.push(generateAlert(compartmentId, type, value))
  }

  return {
    code: 200,
    data: alerts,
    message: 'success'
  }
})

// 获取历史警报
Mock.mock(/\/api\/alerts\/history\?compartmentId=\d+/, 'get', options => {
  const compartmentId = parseInt(options.url.match(/compartmentId=(\d+)/)[1])
  const historyAlerts = []

  // 生成最近10条警报记录
  for (let i = 0; i < 10; i++) {
    const type = ['temperature', 'smoke', 'oxygen', 'co'][Mock.Random.integer(0, 3)]
    const value = Mock.Random.float(alertThresholds[type].medium, alertThresholds[type].high * 1.2)
    const alert = generateAlert(compartmentId, type, value)
    alert.timestamp = new Date(Date.now() - i * 60000).toISOString() // 每分钟一条记录
    historyAlerts.push(alert)
  }

  return {
    code: 200,
    data: historyAlerts,
    message: 'success'
  }
})

// 处理警报
Mock.mock(/\/api\/alerts\/handle/, 'post', options => {
  const { alertId, action } = JSON.parse(options.body)

  return {
    code: 200,
    data: {
      alertId,
      action,
      status: 'handled',
      handleTime: new Date().toISOString()
    },
    message: 'success'
  }
})
