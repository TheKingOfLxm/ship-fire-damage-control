import { DataTypes } from 'sequelize'
import sequelize from '../config/database.js'

const Ship = sequelize.define('Ship', {
  id: {
    type: DataTypes.INTEGER,
    primaryKey: true,
    autoIncrement: true
  },
  name: {
    type: DataTypes.STRING(100),
    allowNull: false,
    unique: true
  },
  type: {
    type: DataTypes.STRING(50),
    allowNull: false
  },
  length: {
    type: DataTypes.FLOAT,
    allowNull: false
  },
  width: {
    type: DataTypes.FLOAT,
    allowNull: false
  },
  height: {
    type: DataTypes.FLOAT,
    allowNull: false
  },
  displacement: {
    type: DataTypes.FLOAT,
    comment: '排水量（吨）'
  },
  modelPath: {
    type: DataTypes.STRING(255),
    comment: '3D模型路径'
  },
  status: {
    type: DataTypes.ENUM('active', 'maintenance', 'inactive'),
    defaultValue: 'active'
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
  tableName: 'ships',
  timestamps: true
})

export default Ship
