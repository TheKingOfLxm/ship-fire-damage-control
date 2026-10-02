import { DataTypes } from 'sequelize'
import sequelize from '../config/database.js'

const Compartment = sequelize.define('Compartment', {
  id: {
    type: DataTypes.INTEGER,
    primaryKey: true,
    autoIncrement: true
  },
  shipId: {
    type: DataTypes.INTEGER,
    allowNull: false,
    references: {
      model: 'ships',
      key: 'id'
    }
  },
  name: {
    type: DataTypes.STRING(50),
    allowNull: false
  },
  type: {
    type: DataTypes.ENUM('engine_room', 'power_station', 'hangar', 'living_quarters', 'galley', 'cargo_hold'),
    allowNull: false
  },
  // 位置信息（用于3D场景）
  positionX: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  positionY: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  positionZ: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  // 火灾位置偏移
  firePositionX: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  firePositionY: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  firePositionZ: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  // 相机偏移
  cameraOffsetX: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  cameraOffsetY: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  cameraOffsetZ: {
    type: DataTypes.FLOAT,
    defaultValue: 0
  },
  // 基础环境参数
  baseTemperature: {
    type: DataTypes.FLOAT,
    defaultValue: 25,
    comment: '基础温度（°C）'
  },
  baseSmoke: {
    type: DataTypes.FLOAT,
    defaultValue: 0,
    comment: '基础烟雾浓度（%）'
  },
  baseOxygen: {
    type: DataTypes.FLOAT,
    defaultValue: 21,
    comment: '基础氧气浓度（%）'
  },
  baseCO: {
    type: DataTypes.FLOAT,
    defaultValue: 0,
    comment: '基础CO浓度（ppm）'
  },
  // 模型路径
  modelPath: {
    type: DataTypes.STRING(255),
    comment: '舱室3D模型路径'
  },
  status: {
    type: DataTypes.ENUM('normal', 'warning', 'danger'),
    defaultValue: 'normal'
  },
  createdAt: {
    type: DataTypes.DATE,
    allowNull: false
  },
  updatedAt: {
    type: DataTypes.DATE,
    allowNull: false
  }
}, {
  tableName: 'compartments',
  timestamps: true
})

export default Compartment
