/**
 * PyroSim 项目文件设置脚本
 * 用于创建项目目录结构和复制项目文件
 */

const fs = require('fs')
const path = require('path')

// 项目文件配置
const PROJECT_FILES = [
  { name: '电站间', file: '电站间.psm', type: 'power' },
  { name: '机库', file: '机库.psm', type: 'hangar' },
  { name: '士兵住舱', file: '士兵住舱.psm', type: 'living' },
  { name: '灶炉间', file: '灶炉间.psm', type: 'galley' },
  { name: '主机舱', file: '主机舱.psm', type: 'engine' }
]

const DIRECTORIES = [
  './pyrosim-projects',
  './pyrosim-output',
  './pyrosim-projects/backup'
]

function createDirectories() {
  console.log('📁 创建项目目录...')
  
  DIRECTORIES.forEach(dir => {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true })
      console.log(`✅ 创建目录: ${dir}`)
    } else {
      console.log(`📁 目录已存在: ${dir}`)
    }
  })
}

function createProjectTemplate() {
  console.log('\n📝 创建项目文件模板...')
  
  PROJECT_FILES.forEach(project => {
    const filePath = path.join('./pyrosim-projects', project.file)
    
    if (!fs.existsSync(filePath)) {
      // 创建项目文件模板
      const template = `# PyroSim 项目文件: ${project.name}
# 类型: ${project.type}
# 创建时间: ${new Date().toISOString()}

# 请将您的实际 ${project.file} 文件复制到这个位置
# 或者修改 pyrosim-real-integration.js 中的路径配置

# 节点配置示例:
# ${project.type.toUpperCase()}_NODE_001
# ${project.type.toUpperCase()}_NODE_002
# ${project.type.toUpperCase()}_NODE_003

# 火灾模拟参数:
# 初始温度: 25°C
# 最大温度: 500°C
# 烟雾浓度: 0-100%
# 氧气浓度: 20.9%

# 注意: 这是一个模板文件，请替换为您的实际 .psm 文件
`
      
      fs.writeFileSync(filePath, template)
      console.log(`📝 创建模板: ${project.file}`)
    } else {
      console.log(`📄 文件已存在: ${project.file}`)
    }
  })
}

function createReadme() {
  console.log('\n📖 创建说明文档...')
  
  const readmeContent = `# PyroSim 项目文件说明

## 项目文件列表

${PROJECT_FILES.map((project, index) => 
  `${index + 1}. **${project.name}** (${project.file})
   - 类型: ${project.type}
   - 节点前缀: ${project.type.toUpperCase()}_NODE
   - 状态: ${fs.existsSync(path.join('./pyrosim-projects', project.file)) ? '✅ 已配置' : '❌ 需要添加'}`).join('\n')}

## 使用方法

1. **复制项目文件**
   - 将您的 .psm 文件复制到 ./pyrosim-projects/ 目录下
   - 确保文件名与配置中的一致

2. **启动服务**
   \`\`\`bash
   node pyrosim-real-integration.js
   \`\`\`

3. **访问控制面板**
   - HTTP API: http://localhost:8080
   - WebSocket: ws://localhost:8081

## 目录结构

\`\`\`
pyrosim-projects/
├── 电站间.psm          # 电站间项目文件
├── 机库.psm            # 机库项目文件
├── 士兵住舱.psm        # 士兵住舱项目文件
├── 灶炉间.psm          # 灶炉间项目文件
├── 主机舱.psm          # 主机舱项目文件
└── backup/             # 备份目录

pyrosim-output/
└── (PyroSim 输出文件)
\`\`\`

## 注意事项

- 确保 PyroSim 软件已正确安装
- 检查 pyrosim-real-integration.js 中的路径配置
- 如果没有 pyrosim-cli.exe，系统会使用模拟数据
- 所有项目文件都应该在 ./pyrosim-projects/ 目录下

## 故障排除

如果遇到问题，请检查：
1. 项目文件是否存在
2. PyroSim 软件路径是否正确
3. 文件权限是否足够
4. 网络端口是否被占用

生成时间: ${new Date().toISOString()}
`

  fs.writeFileSync('./pyrosim-projects/README.md', readmeContent)
  console.log('📖 创建说明文档: pyrosim-projects/README.md')
}

function main() {
  console.log('🚀 开始设置 PyroSim 项目文件...\n')
  
  try {
    createDirectories()
    createProjectTemplate()
    createReadme()
    
    console.log('\n✅ PyroSim 项目文件设置完成!')
    console.log('\n📋 下一步操作:')
    console.log('1. 将您的 .psm 文件复制到 ./pyrosim-projects/ 目录')
    console.log('2. 运行: node pyrosim-real-integration.js')
    console.log('3. 访问: http://localhost:8080')
    
  } catch (error) {
    console.error('❌ 设置失败:', error.message)
    process.exit(1)
  }
}

// 如果直接运行此脚本
if (require.main === module) {
  main()
}

module.exports = {
  PROJECT_FILES,
  createDirectories,
  createProjectTemplate,
  createReadme
}
