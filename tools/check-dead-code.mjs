/**
 * 从 src/main.js 出发的模块可达性分析。
 * 输出：可达文件、孤立文件（含自身被谁引用）、循环引用警告。
 * 用法: node tools/check-dead-code.mjs
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, resolve, relative, join } from 'node:path'

const SRC = resolve('src')
const EXT = ['.js', '.vue', '.jsx']

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (EXT.some(e => p.endsWith(e))) out.push(p)
  }
  return out
}

const files = walk(SRC)
const set = new Set(files.map(f => f.replace(/\\/g, '/')))

function resolveSpec(fromFile, spec) {
  if (!spec.startsWith('.') && !spec.startsWith('@/')) return null
  const base = spec.startsWith('@/')
    ? join(SRC, spec.slice(2))
    : resolve(dirname(fromFile), spec)
  for (const cand of [base, ...EXT.map(e => base + e), ...EXT.map(e => join(base, 'index' + e))]) {
    const n = cand.replace(/\\/g, '/')
    if (set.has(n)) return n
  }
  return null
}

const edges = new Map()
const missing = []

for (const f of files) {
  const src = readFileSync(f, 'utf8')
  const deps = new Set()
  const re = /(?:^|[\s;{(])(?:import\s+(?:[\w*{},\s]+\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s+from\s+|import\()\s*['"]([^'"]+)['"]/g
  let m
  while ((m = re.exec(src))) {
    const spec = m[1]
    const r = resolveSpec(f, spec)
    if (r) deps.add(r)
    else if (spec.startsWith('.') || spec.startsWith('@/')) missing.push(`${relative(SRC, f)} -> ${spec}`)
  }
  edges.set(f.replace(/\\/g, '/'), deps)
}

// BFS from main.js
const root = join(SRC, 'main.js').replace(/\\/g, '/')
const reachable = new Set()
const queue = [root]
while (queue.length) {
  const cur = queue.shift()
  if (reachable.has(cur)) continue
  reachable.add(cur)
  for (const d of edges.get(cur) || []) if (!reachable.has(d)) queue.push(d)
}

// 反向引用
const referencedBy = new Map()
for (const [from, deps] of edges) {
  for (const d of deps) {
    if (!referencedBy.has(d)) referencedBy.set(d, [])
    referencedBy.get(d).push(from)
  }
}

const orphan = files.map(f => f.replace(/\\/g, '/')).filter(f => !reachable.has(f))

console.log('=== 入口可达 ===')
console.log(`main.js 可达 ${reachable.size} / ${files.length} 个模块\n`)

console.log('=== 孤立模块（不在运行路径上） ===')
const bySize = orphan
  .map(f => ({ f, size: statSync(f).size, refs: referencedBy.get(f)?.length || 0 }))
  .sort((a, b) => b.size - a.size)
let deadBytes = 0
for (const o of bySize) {
  deadBytes += o.size
  const refs = o.refs ? `被 ${o.refs} 处引用(孤立子图)` : '无引用'
  console.log(`  ${o.f.replace(SRC + '/', '').padEnd(46)} ${String(o.size).padStart(7)} B   ${refs}`)
}
console.log(`\n孤立模块合计 ${bySize.length} 个 / ${(deadBytes / 1024).toFixed(0)} KB`)

console.log('\n=== 无法解析的导入 ===')
if (!missing.length) console.log('  无')
else missing.forEach(x => console.log('  ' + x))

console.log('\n=== 循环引用 ===')
const cycles = []
const state = new Map()
function dfs(n, stack) {
  if (state.get(n) === 1) {
    cycles.push([...stack.slice(stack.indexOf(n)), n])
    return
  }
  if (state.get(n) === 2) return
  state.set(n, 1)
  for (const d of edges.get(n) || []) dfs(d, [...stack, d])
  state.set(n, 2)
}
dfs(root, [root])
if (!cycles.length) console.log('  无')
else cycles.forEach(c => console.log('  ' + c.map(x => x.replace(SRC + '/', '')).join(' → ')))
