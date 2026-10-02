import sequelize from '../config/database.js'
import { Sequelize } from 'sequelize'

// 导入模型
import Compartment from './Compartment.js'
import FireEvent from './FireEvent.js'
import FireData from './FireData.js'
import Alert from './Alert.js'
import Ship from './Ship.js'

// 定义关联关系
Ship.hasMany(Compartment, { foreignKey: 'shipId', as: 'compartments' })
Compartment.belongsTo(Ship, { foreignKey: 'shipId', as: 'ship' })

Compartment.hasMany(FireEvent, { foreignKey: 'compartmentId', as: 'fireEvents' })
FireEvent.belongsTo(Compartment, { foreignKey: 'compartmentId', as: 'compartment' })

Compartment.hasMany(FireData, { foreignKey: 'compartmentId', as: 'fireData' })
FireData.belongsTo(Compartment, { foreignKey: 'compartmentId', as: 'compartment' })

Compartment.hasMany(Alert, { foreignKey: 'compartmentId', as: 'alerts' })
Alert.belongsTo(Compartment, { foreignKey: 'compartmentId', as: 'compartment' })

const db = {
  sequelize,
  Sequelize,
  Ship,
  Compartment,
  FireEvent,
  FireData,
  Alert
}

export { sequelize }
export default db
