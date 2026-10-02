import { DataTypes } from 'sequelize'
import sequelize from '../config/database.js'

const Alert = sequelize.define('Alert', {
  id: {
    type: DataTypes.INTEGER,
    primaryKey: true,
    autoIncrement: true
  },
  compartmentId: {
    type: DataTypes.INTEGER,
    allowNull: true,
    references: {
      model: 'compartments',
      key: 'id'
    }
  },
  level: {
    type: DataTypes.ENUM('low', 'medium', 'high', 'critical'),
    allowNull: false
  },
  type: {
    type: DataTypes.ENUM('fire', 'smoke', 'temperature', 'gas', 'equipment', 'system'),
    allowNull: false
  },
  title: {
    type: DataTypes.STRING(200),
    allowNull: false
  },
  message: {
    type: DataTypes.TEXT,
    allowNull: false
  },
  status: {
    type: DataTypes.ENUM('active', 'acknowledged', 'resolved'),
    defaultValue: 'active'
  },
  acknowledgedAt: {
    type: DataTypes.DATE,
    allowNull: true
  },
  resolvedAt: {
    type: DataTypes.DATE,
    allowNull: true
  },
  metadata: {
    type: DataTypes.TEXT,
    defaultValue: '{}',
    get() {
      const rawValue = this.getDataValue('metadata')
      try {
        return rawValue ? JSON.parse(rawValue) : {}
      } catch (e) {
        return {}
      }
    },
    set(value) {
      this.setDataValue('metadata', JSON.stringify(value))
    }
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
  tableName: 'alerts',
  timestamps: true,
  indexes: [
    {
      fields: ['status', 'createdAt']
    },
    {
      fields: ['level', 'status']
    }
  ]
})

export default Alert
