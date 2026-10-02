// 舱室配置
export const COMPARTMENTS = {
  1: { id: 1, name: '机舱', riskLevel: 'high' },
  2: { id: 2, name: '货舱', riskLevel: 'medium' },
  3: { id: 3, name: '驾驶室', riskLevel: 'low' }
}

// 监测指标配置
export const MONITOR_INDICATORS = {
  TEMPERATURE: { name: '温度', unit: '°C', max: 200 },
  SMOKE: { name: '烟雾浓度', unit: 'ppm', max: 500 },
  OXYGEN: { name: '氧气浓度', unit: '%', max: 21 },
  CO: { name: '一氧化碳', unit: 'ppm', max: 200 }
}

// 警报阈值配置
export const ALERT_THRESHOLDS = {
  TEMPERATURE: { medium: 50, high: 70 },
  SMOKE: { medium: 150, high: 250 },
  OXYGEN: { medium: 19, high: 17 },
  CO: { medium: 40, high: 80 }
}

// 颜色配置
export const COLORS = {
  PRIMARY: '#0088ff',
  SUCCESS: '#52c41a',
  WARNING: '#faad14',
  DANGER: '#ff4d4f',
  INFO: '#1890ff',
  BACKGROUND: '#0a192f',
  PANEL_BG: 'rgba(80, 180, 240, 0.25)'
}

// 动画配置
export const ANIMATION = {
  DURATION: 300,
  EASING: 'ease-in-out'
}

// API配置
export const API_CONFIG = {
  TIMEOUT: 10000,
  RETRY_TIMES: 3,
  RETRY_DELAY: 1000
}

// 图表配置
export const CHART_CONFIG = {
  UPDATE_INTERVAL: 1000,
  MAX_DATA_POINTS: 6,
  MAX_ALERTS: 15
}

// 3D场景配置
export const THREE_CONFIG = {
  CAMERA: {
    FOV: 45,
    NEAR: 0.1,
    FAR: 1000,
    INITIAL_POSITION: [-4, 4, 4]
  },
  LIGHTING: {
    AMBIENT_INTENSITY: 1.0,
    DIRECTIONAL_INTENSITY: 1.0,
    FILL_INTENSITY: 0.5,
    SPOT_INTENSITY: 2.0
  },
  SHADOWS: {
    MAP_SIZE: 2048,
    CAMERA_NEAR: 0.5,
    CAMERA_FAR: 50
  }
}
