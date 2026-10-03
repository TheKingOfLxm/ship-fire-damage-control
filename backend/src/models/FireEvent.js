import { DataTypes } from 'sequelize'
import sequelize from '../config/database.js'

const FireEvent = sequelize.define('FireEvent', {
  id: {
    type: DataTypes.INTEGER,
    primaryKey: true,
    autoIncrement: true
  },
  compartmentId: {
    type: DataTypes.INTEGER,
    allowNull: false,
    references: {
      model: 'compartments',
      key: 'id'
    }
  },
  type: {
    // 火灾起因（不是舱室类型）。舱室类型到起因的映射见
    // config/compartments.json 的 fireCause 字段。
    type: DataTypes.ENUM(
      'electrical', 'fuel', 'engine', 'cargo',
      'kitchen', 'fabric', 'structural', 'unknown'
    ),
    allowNull: false
  },
  status: {
    type: DataTypes.ENUM('active', 'suppressed', 'extinguished'),
    defaultValue: 'active'
  },
  intensity: {
    type: DataTypes.FLOAT,
    defaultValue: 0.5,
    comment: '火灾强度 0-1'
  },
  spreadRate: {
    type: DataTypes.FLOAT,
    defaultValue: 0.1,
    comment: '蔓延速率'
  },
  startTime: {
    type: DataTypes.DATE,
    allowNull: false
  },
  endTime: {
    type: DataTypes.DATE,
    allowNull: true
  },
  suppressedAt: {
    type: DataTypes.DATE,
    allowNull: true
  },
  // 灭火/疏散指令与已推进步数 —— 落库是为了后端重启后能还原火场状态
  // （谁在灭火、已烧多久），而不是重启后"从零重烧 + 指令静默丢失"。
  suppressed: {
    type: DataTypes.BOOLEAN,
    defaultValue: false,
    comment: '灭火系统已启动'
  },
  evacuated: {
    type: DataTypes.BOOLEAN,
    defaultValue: false,
    comment: '人员已疏散'
  },
  modelSteps: {
    type: DataTypes.INTEGER,
    defaultValue: 0,
    comment: '已推进的模型步数（重启恢复用）'
  },
  suppressedMethod: {
    type: DataTypes.ENUM('automatic', 'manual'),
    allowNull: true
  },
  cause: {
    type: DataTypes.TEXT,
    allowNull: true
  },
  description: {
    type: DataTypes.TEXT,
    allowNull: true
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
  tableName: 'fire_events',
  timestamps: true
})

export default FireEvent
