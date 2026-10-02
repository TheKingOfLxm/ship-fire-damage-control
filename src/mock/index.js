import Mock from 'mockjs'
import './ship'
import './fire'
import './alerts'

// 设置全局延迟
Mock.setup({
  timeout: '200-600'
})

export default Mock
