/**
 * 复制 PyroSim 项目文件脚本
 * 用于将您的 .psm 文件复制到正确的目录
 */

const fs = require('fs')
const path = require('path')

// 您的 PyroSim 项目文件位置（请根据实际情况修改）
const SOURCE_DIRECTORIES = [
  'D:\\PyroSim\\projects',           // PyroSim 默认项目目录
  'C:\\Program Files\\PyroSim\\projects',
  'C:\\Users\\' + process.env.USERNAME + '\\Documents\\PyroSim',
  'D:\\',                            // D盘根目录
  'C:\\',                            // C盘根目录
  './',                              // 当前目录
]

const TARGET_DIR = './pyrosim-projects'

const PROJECT_FILES = [
  '电站间.psm',
  '机库.psm',
  '士兵住舱.psm',
  '灶炉间.psm',
  '主机舱.psm'
]

function findProjectFiles() {
  console.log('🔍 搜索 PyroSim 项目文件...\n')
  
  const foundFiles = []
  
  SOURCE_DIRECTORIES.forEach(sourceDir => {
    if (fs.existsSync(sourceDir)) {
      console.log(`📁 检查目录: ${sourceDir}`)
      
      PROJECT_FILES.forEach(fileName => {
        const filePath = path.join(sourceDir, fileName)
        if (fs.existsSync(filePath)) {
          foundFiles.push({
            name: fileName,
            source: filePath,
            size: fs.statSync(filePath).size,
            modified: fs.statSync(filePath).mtime
          })
          console.log(`  ✅ 找到: ${fileName}`)
        }
      })
    }
  })
  
  return foundFiles
}

function copyProjectFiles(foundFiles) {
  console.log('\n📋 开始复制项目文件...\n')
  
  // 确保目标目录存在
  if (!fs.existsSync(TARGET_DIR)) {
    fs.mkdirSync(TARGET_DIR, { recursive: true })
    console.log(`📁 创建目标目录: ${TARGET_DIR}`)
  }
  
  foundFiles.forEach(file => {
    const targetPath = path.join(TARGET_DIR, file.name)
    
    try {
      // 如果目标文件已存在，创建备份
      if (fs.existsSync(targetPath)) {
        const backupPath = path.join(TARGET_DIR, 'backup', file.name + '.' + Date.now())
        const backupDir = path.dirname(backupPath)
        
        if (!fs.existsSync(backupDir)) {
          fs.mkdirSync(backupDir, { recursive: true })
        }
        
        fs.copyFileSync(targetPath, backupPath)
        console.log(`💾 备份现有文件: ${file.name}`)
      }
      
      // 复制文件
      fs.copyFileSync(file.source, targetPath)
      console.log(`✅ 复制成功: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`)
      
    } catch (error) {
      console.error(`❌ 复制失败: ${file.name} - ${error.message}`)
    }
  })
}

function createProjectIndex() {
  console.log('\n📝 创建项目索引...')
  
  const projectIndex = {
    lastUpdated: new Date().toISOString(),
    projects: [],
    totalFiles: 0,
    totalSize: 0
  }
  
  PROJECT_FILES.forEach(fileName => {
    const filePath = path.join(TARGET_DIR, fileName)
    
    if (fs.existsSync(filePath)) {
      const stats = fs.statSync(filePath)
      const projectName = path.parse(fileName).name
      
      projectIndex.projects.push({
        name: projectName,
        file: fileName,
        size: stats.size,
        modified: stats.mtime.toISOString(),
        status: 'ready'
      })
      
      projectIndex.totalFiles++
      projectIndex.totalSize += stats.size
    }
  })
  
  fs.writeFileSync(
    path.join(TARGET_DIR, 'project-index.json'),
    JSON.stringify(projectIndex, null, 2)
  )
  
  console.log(`📊 项目统计: ${projectIndex.totalFiles} 个文件, ${(projectIndex.totalSize / 1024).toFixed(1)} KB`)
}

function main() {
  console.log('🚀 PyroSim 项目文件复制工具\n')
  
  try {
    const foundFiles = findProjectFiles()
    
    if (foundFiles.length === 0) {
      console.log('\n❌ 未找到任何 PyroSim 项目文件')
      console.log('\n📋 请检查以下位置是否有您的 .psm 文件:')
      SOURCE_DIRECTORIES.forEach(dir => {
        console.log(`  - ${dir}`)
      })
      console.log('\n💡 或者手动将 .psm 文件复制到 ./pyrosim-projects/ 目录')
      return
    }
    
    console.log(`\n📊 找到 ${foundFiles.length} 个项目文件`)
    
    copyProjectFiles(foundFiles)
    createProjectIndex()
    
    console.log('\n✅ 项目文件复制完成!')
    console.log('\n📋 下一步操作:')
    console.log('1. 运行: node pyrosim-real-integration.js')
    console.log('2. 或者运行: node pyrosim-backend-example.js (模拟模式)')
    console.log('3. 访问: http://localhost:8080')
    
  } catch (error) {
    console.error('❌ 操作失败:', error.message)
    process.exit(1)
  }
}

// 如果直接运行此脚本
if (require.main === module) {
  main()
}

module.exports = {
  findProjectFiles,
  copyProjectFiles,
  createProjectIndex
}
