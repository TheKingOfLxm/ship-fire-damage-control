import Mock from 'mockjs'

// 船舶基本信息
export const shipInfo = {
  id: 'COSCO-2024-001',
  name: '中远海运星辰号',
  type: '集装箱货轮',
  tonnage: 85000,
  buildYear: 2022,
  status: '航行中',
  currentLocation: {
    latitude: 31.2304,
    longitude: 121.4737,
    port: '上海港',
    destination: '洛杉矶港'
  },
  crew: {
    total: 25,
    captain: '张海峰',
    chiefEngineer: '李明',
    safetyOfficer: '王强'
  },
  specifications: {
    length: 366,
    width: 51,
    height: 30,
    draft: 15.5,
    speed: 22.5,
    fuelCapacity: 5000,
    cargoCapacity: 8500
  },
  compartments: [
    {
      id: 1,
      name: '电站间',
      location: '船体中部',
      riskLevel: 'high',
      baseTemp: 35,
      baseSmoke: 25,
      baseOxygen: 20.8,
      baseCO: 6,
      equipment: ['主发电机', '配电柜', 'UPS系统', '电池组'],
      area: 120,
      maxOccupancy: 3,
      fireSuppression: ['气体灭火系统', 'CO2系统'],
      sensors: {
        temperature: 5,
        smoke: 6,
        oxygen: 3,
        co: 4,
        pressure: 2
      }
    },
    {
      id: 2,
      name: '机库',
      location: '船体尾部上部',
      riskLevel: 'high',
      baseTemp: 30,
      baseSmoke: 30,
      baseOxygen: 20.8,
      baseCO: 8,
      equipment: ['直升机', '航空燃油系统', '起降平台'],
      area: 350,
      maxOccupancy: 6,
      fireSuppression: ['泡沫系统', '干粉灭火器'],
      sensors: {
        temperature: 6,
        smoke: 8,
        oxygen: 4,
        co: 5,
        pressure: 3
      }
    },
    {
      id: 3,
      name: '士兵住舱',
      location: '船体中上部',
      riskLevel: 'low',
      baseTemp: 22,
      baseSmoke: 10,
      baseOxygen: 21.0,
      baseCO: 2,
      equipment: ['床铺', '储物柜', '照明系统'],
      area: 180,
      maxOccupancy: 20,
      fireSuppression: ['喷淋系统', '手动灭火器'],
      sensors: {
        temperature: 4,
        smoke: 6,
        oxygen: 3,
        co: 3,
        pressure: 2
      }
    },
    {
      id: 4,
      name: '灶炉间',
      location: '生活区',
      riskLevel: 'medium',
      baseTemp: 32,
      baseSmoke: 20,
      baseOxygen: 20.9,
      baseCO: 5,
      equipment: ['燃气灶', '油烟机', '冰箱', '微波炉'],
      area: 25,
      maxOccupancy: 3,
      fireSuppression: ['干粉灭火器', '灭火毯'],
      sensors: {
        temperature: 3,
        smoke: 5,
        oxygen: 2,
        co: 3,
        pressure: 1
      }
    },
    {
      id: 5,
      name: '主机舱',
      location: '船体后部',
      riskLevel: 'high',
      baseTemp: 52,
      baseSmoke: 35,
      baseOxygen: 20.7,
      baseCO: 12,
      equipment: ['柴油主机', '曲轴系统', '增压器', '冷却系统'],
      area: 320,
      maxOccupancy: 10,
      fireSuppression: ['CO2系统', '泡沫系统', '手动灭火器'],
      sensors: {
        temperature: 8,
        smoke: 10,
        oxygen: 5,
        co: 7,
        pressure: 5
      }
    }
  ]
}

// 获取船舶基本信息
Mock.mock(/\/api\/ship\/info/, 'get', options => {
  console.log('Mock.js intercepted URL:', options.url)
  return {
    code: 200,
    data: shipInfo,
    message: 'success'
  }
})

// 获取舱室列表
Mock.mock(/\/api\/ship\/compartments/, 'get', () => {
  return {
    code: 200,
    data: shipInfo.compartments,
    message: 'success'
  }
})

// 获取舱室详情
Mock.mock(/\/api\/ship\/compartments\/\d+/, 'get', options => {
  const id = parseInt(options.url.match(/\/compartments\/(\d+)/)[1])
  const compartment = shipInfo.compartments.find(c => c.id === id)

  return {
    code: 200,
    data: compartment,
    message: 'success'
  }
})

// 获取风险分布数据
Mock.mock(/\/api\/ship\/risk-distribution/, 'get', () => {
  return {
    code: 200,
    data: {
      high: 3,
      medium: 3,
      low: 2,
      total: 8
    },
    message: 'success'
  }
})

// 获取所有舱室实时数据 (此接口仍由 Mock.js 动态生成)
Mock.mock(/\/api\/ship\/compartments\/realtime/, 'get', () => {
  const realtimeData = shipInfo.compartments.map(compartment => {
    const elapsedTime = Mock.Random.integer(1, 100) // 模拟火灾持续时间
    const growth = Math.min(1, Math.log(1 + elapsedTime * 0.1) / Math.log(10))

    // 根据舱室类型设置不同的参数
    const params = {
      1: {
        // 机舱
        maxTemp: 180,
        maxSmoke: 450,
        minOxygen: 15,
        maxCO: 200,
        tempGrowth: 0.8,
        smokeGrowth: 0.6
      },
      2: {
        // 货舱
        maxTemp: 150,
        maxSmoke: 500,
        minOxygen: 16,
        maxCO: 150,
        tempGrowth: 0.5,
        smokeGrowth: 0.8
      },
      3: {
        // 驾驶室
        maxTemp: 100,
        maxSmoke: 300,
        minOxygen: 17,
        maxCO: 100,
        tempGrowth: 0.3,
        smokeGrowth: 0.4
      }
    }[compartment.id]

    // 计算当前值
    const calculateValue = (base, max, growth, fluctuation = 0.1) => {
      const value = base + (max - base) * growth
      const randomFluctuation = (Math.random() - 0.5) * 2 * fluctuation * growth
      return Math.min(max, Math.max(base, value + randomFluctuation))
    }

    const calculateDecreasingValue = (base, min, growth, fluctuation = 0.1) => {
      const value = base - (base - min) * growth
      const randomFluctuation = (Math.random() - 0.5) * 2 * fluctuation * growth
      return Math.max(min, Math.min(base, value + randomFluctuation))
    }

    return {
      id: compartment.id,
      name: compartment.name,
      location: compartment.location,
      riskLevel: compartment.riskLevel,
      realtimeData: {
        temperature: calculateValue(
          compartment.baseTemp,
          params.maxTemp,
          growth * params.tempGrowth
        ),
        smoke: calculateValue(compartment.baseSmoke, params.maxSmoke, growth * params.smokeGrowth),
        oxygen: calculateDecreasingValue(compartment.baseOxygen, params.minOxygen, growth),
        co: calculateValue(compartment.baseCO, params.maxCO, growth)
      }
    }
  })

  return {
    code: 200,
    data: realtimeData,
    message: 'success'
  }
})
