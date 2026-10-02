import { DataTypes } from 'sequelize'
import sequelize from '../config/database.js'

const FireData = sequelize.define('FireData', {
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
  eventId: {
    type: DataTypes.INTEGER,
    allowNull: true,
    references: {
      model: 'fire_events',
      key: 'id'
    }
  },
  temperature: {
    type: DataTypes.FLOAT,
    allowNull: false,
    comment: '温度（°C）'
  },
  smoke: {
    type: DataTypes.FLOAT,
    allowNull: false,
    comment: '烟雾浓度（%）'
  },
  oxygen: {
    type: DataTypes.FLOAT,
    allowNull: false,
    comment: '氧气浓度（%）'
  },
  co: {
    type: DataTypes.FLOAT,
    allowNull: false,
    comment: 'CO浓度（ppm）'
  },
  co2: {
    type: DataTypes.FLOAT,
    allowNull: false,
    defaultValue: 400,
    comment: 'CO2浓度（ppm）'
  },
  timestamp: {
    type: DataTypes.DATE,
    allowNull: false,
    defaultValue: DataTypes.NOW
  },
  source: {
    type: DataTypes.ENUM('sensor', 'simulation', 'prediction'),
    defaultValue: 'sensor'
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
  tableName: 'fire_data',
  timestamps: true,
  indexes: [
    {
      fields: ['compartmentId', 'timestamp']
    },
    {
      fields: ['eventId']
    }
  ]
})

export default FireData
