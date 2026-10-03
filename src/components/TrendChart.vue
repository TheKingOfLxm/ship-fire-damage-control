<template>
  <div class="trend">
    <!-- 指标开关 -->
    <div class="trend__legend">
      <button
        v-for="m in METRICS"
        :key="m.key"
        class="trend__chip"
        :class="{ 'is-off': !visible[m.key] }"
        :style="{ '--chip': m.color }"
        :aria-pressed="visible[m.key]"
        @click="toggle(m.key)"
      >
        <span class="trend__chip-dot" aria-hidden="true"></span>
        {{ m.short }}
        <span v-if="visible[m.key]" class="trend__chip-val">{{ lastValue(m.key) }}</span>
      </button>
    </div>

    <div v-if="!hasData" class="empty" style="padding: var(--sp-6) 0">
      <p class="empty__hint">正在采集样本，积累约 10 个数据点后开始绘制趋势曲线。</p>
    </div>

    <!--
      小倍数图：每个指标独立 Y 轴。
      之前四条曲线共用一个按满量程（800℃/100%/25%/500ppm）归一的坐标，
      500℃ 只占 62% 高度，曲线看上去几乎是平的 —— 变化根本看不出来。
      各自按实际数据范围自适应之后，趋势才可读。
    -->
    <div v-else class="trend__panes">
      <section
        v-for="m in activeMetrics"
        :key="m.key"
        class="pane"
        :aria-label="`${m.label}趋势`"
      >
        <header class="pane__head">
          <span class="pane__name" :style="{ color: m.color }">{{ m.label }}</span>
          <span class="pane__now">
            {{ lastValue(m.key) }}<small>{{ m.unit }}</small>
          </span>
          <span class="pane__delta" :class="deltaClass(m.key)">{{ deltaText(m.key) }}</span>
        </header>

        <div class="pane__plot">
          <svg
            :viewBox="`0 0 ${PANE_W} ${PANE_H}`"
            preserveAspectRatio="none"
            role="img"
            :aria-label="`${m.label}实测与预测曲线，当前 ${lastValue(m.key)}${m.unit}`"
          >
            <!-- 预测区间底色：让人一眼分出哪段是模型外推的 -->
            <rect
              v-if="predCount"
              class="pane__predzone"
              :x="predZoneX"
              y="0"
              :width="PANE_W - predZoneX"
              :height="PANE_H"
            />
            <!-- 超出训练时域/模型可信段的部分再加深一档，避免被误读为模型输出 -->
            <rect
              v-if="predCount && basisSwitchX != null"
              class="pane__oodzone"
              :x="basisSwitchX"
              y="0"
              :width="PANE_W - basisSwitchX"
              :height="PANE_H"
            />
            <!-- 警戒 / 危险阈值线 -->
            <line
              v-if="scaleOf(m.key).warnY != null"
              class="pane__thr pane__thr--warn"
              x1="0" :x2="PANE_W" :y1="scaleOf(m.key).warnY" :y2="scaleOf(m.key).warnY"
            />
            <line
              v-if="scaleOf(m.key).critY != null"
              class="pane__thr pane__thr--crit"
              x1="0" :x2="PANE_W" :y1="scaleOf(m.key).critY" :y2="scaleOf(m.key).critY"
            />
            <!-- 基线 -->
            <line class="pane__axis" x1="0" :x2="PANE_W" y1="0" :y2="0" />

            <!-- 实测曲线 -->
            <polyline
              v-if="measuredPts(m.key).length > 1"
              class="pane__line"
              :style="{ stroke: m.color }"
              :points="toPoints(measuredPts(m.key))"
            />
            <!-- 预测曲线（虚线，接在实测末点之后；超出单次可靠时域的段透明度更低一档） -->
            <polyline
              v-for="(seg, si) in predSegments(m.key)"
              :key="si"
              class="pane__line pane__line--pred"
              :class="{ 'pane__line--ex': seg.extrapolated }"
              :style="{ stroke: m.color }"
              :points="toPoints(seg.pts)"
            />
            <!-- 末点 -->
            <circle
              v-if="lastMeasured(m.key)"
              class="pane__dot"
              :style="{ fill: m.color }"
              :cx="lastMeasured(m.key).x"
              :cy="lastMeasured(m.key).y"
              r="2.2"
            />
          </svg>

          <!-- Y 轴刻度：上下各标一个，够看出量级和变化 -->
          <span class="pane__y pane__y--top">{{ scaleOf(m.key).maxLabel }}</span>
          <span class="pane__y pane__y--bot">{{ scaleOf(m.key).minLabel }}</span>
        </div>
      </section>
    </div>

    <!-- X 轴时间刻度 -->
    <div v-if="hasData" class="trend__xaxis">
      <span class="t-num">{{ timeStart }}s</span>
      <span class="trend__xmid">
        实线=实测 · 虚线={{ predCaption }}<template v-if="predCount"> · {{ predCount }} 点</template><template v-if="hasExtrapolated"> · 浅色段=超出可靠时域</template><template v-if="derivedVisible"> · 烟雾/氧气为推导值</template>
      </span>
      <span class="t-num">现在</span>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'

const props = defineProps({
  series: { type: Array, default: () => [] },
  predictions: { type: Array, default: () => [] },
  /** 'lstm' = 模型预测；'extrapolation' = 变化率外推（可信度不同，必须如实区分） */
  predictionSource: { type: String, default: null },
  /** 模型训练轨迹的总时长（秒）。超过它的预测属于分布外外推，要单独标出来。 */
  trainedHorizonSeconds: { type: Number, default: null },
  /**
   * 预测点里 basis 从 'lstm' 变为其它（如 'growth'）的时刻（秒）。
   * 服务端会在自回归收敛到极限环时给出这个切分点。
   */
  lstmSeconds: { type: Number, default: null }
})

const PANE_W = 320
const PANE_H = 54
const TOP = 4
const BOT = 4

const METRICS = [
  { key: 'temperature', short: '温度', label: '温度', unit: '℃', color: '#f97316', max: 800, warn: 200, crit: 400, digits: 0 },
  { key: 'smoke', short: '烟雾', label: '烟雾', unit: '%', color: '#a78bfa', max: 100, warn: 20, crit: 45, digits: 0 },
  { key: 'oxygen', short: '氧气', label: '氧气', unit: '%', color: '#38bdf8', max: 25, warn: 19, crit: 16, digits: 1, invert: true },
  { key: 'co', short: 'CO', label: '一氧化碳', unit: 'ppm', color: '#facc15', max: 500, warn: 50, crit: 150, digits: 0 },
  { key: 'co2', short: 'CO₂', label: '二氧化碳', unit: 'ppm', color: '#34d399', max: 60000, warn: 15000, crit: 50000, digits: 0 }
]

const visible = reactive({ temperature: true, smoke: true, oxygen: false, co: true, co2: true })

const toggle = key => {
  visible[key] = !visible[key]
}

const activeMetrics = computed(() => METRICS.filter(m => visible[m.key]))
const hasData = computed(() => props.series.length > 1)
const predCount = computed(() => props.predictions.length)

/** 脚注跟随真实来源，不把外推伪装成模型预测 */
const PRED_CAPTION = {
  lstm: 'LSTM 模型预测',
  extrapolation: '变化率外推（非模型预测）'
}
const predCaption = computed(() => PRED_CAPTION[props.predictionSource] || '预测')

/* ------------------------------------------------------------------ */
/* Y 轴：按本指标自己的实际数据范围自适应                            */
/* ------------------------------------------------------------------ */
function scaleOf(key) {
  const m = METRICS.find(x => x.key === key)
  const vals = []
  props.series.forEach(d => { const v = Number(d[key]); if (Number.isFinite(v)) vals.push(v) })
  props.predictions.forEach(d => {
    const v = Number(d[key] ?? d[`${key}Level`])
    if (Number.isFinite(v)) vals.push(v)
  })
  if (!vals.length) {
    return { min: 0, max: m.max, minY: PANE_H - BOT, maxY: TOP, minLabel: '0', maxLabel: String(m.max), warnY: null, critY: null }
  }
  let lo = Math.min(...vals)
  let hi = Math.max(...vals)
  // 阈值也要纳入范围，否则警戒线会画到框外或挤到边角
  if (Number.isFinite(m.warn)) { lo = Math.min(lo, m.warn); hi = Math.max(hi, m.warn) }
  if (Number.isFinite(m.crit)) { lo = Math.min(lo, m.crit); hi = Math.max(hi, m.crit) }
  if (hi - lo < 1e-6) { lo -= 1; hi += 1 }
  const pad = (hi - lo) * 0.12
  lo -= pad
  hi += pad

  const toY = v => BOT + (1 - (v - lo) / (hi - lo)) * (PANE_H - TOP - BOT)
  const fmt = v => {
    const r = Number(v).toFixed(m.digits)
    return m.digits === 0 ? Math.round(Number(v)).toLocaleString('zh-CN') : r
  }
  return {
    min: lo, max: hi, toY,
    minLabel: fmt(lo), maxLabel: fmt(hi),
    warnY: Number.isFinite(m.warn) && m.warn >= lo && m.warn <= hi ? toY(m.warn) : null,
    critY: Number.isFinite(m.crit) && m.crit >= lo && m.crit <= hi ? toY(m.crit) : null
  }
}

const plotW = computed(() => PANE_W * 0.78)   // 无预测时右侧留白

/* ------------------------------------------------------------------ */
/* X 轴：历史与预测共用一条真实时间轴                                   */
/* ------------------------------------------------------------------ */
/** ISO 字符串/数字统一转毫秒（后端 /history 给的是 ISO 字符串） */
const toMs = v => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const n = Number(v)
    if (Number.isFinite(n)) return n
    const t = Date.parse(v)
    return Number.isFinite(t) ? t : null
  }
  return null
}

const timeAnchor = computed(() => {
  const s = props.series
  const n = s.length
  const a = n ? toMs(s[0]?.timestamp) : null
  const b = n ? toMs(s[n - 1]?.timestamp) : null
  if (a != null && b != null && b > a) return { t0: a, t1: b, hasTs: true }
  // timestamp 缺失时按 1s/点等间隔估算
  return { t0: 0, t1: Math.max(1, (n - 1) * 1000), hasTs: false }
})

/** 时间域（秒）：历史跨度 + 预测跨度。有预测时两者按真实秒数摊满画布，
 *  900 秒预测对 240 秒历史不再是等距错位的 1:1 排布。 */
const timeDomain = computed(() => {
  const { t0, t1, hasTs } = timeAnchor.value
  const histSpan = hasTs ? Math.max(1, (t1 - t0) / 1000) : Math.max(1, props.series.length - 1)
  const predSpan = props.predictions.length
    ? Math.max(1, ...props.predictions.map(p => Number(p.time) || 0))
    : 0
  return { histSpan, predSpan, total: histSpan + predSpan }
})

/** 历史区宽度：有预测时按真实时间比例分摊 */
const histW = computed(() =>
  timeDomain.value.predSpan > 0
    ? PANE_W * timeDomain.value.histSpan / timeDomain.value.total
    : plotW.value)

const xOfMs = ms =>
  ((ms - timeAnchor.value.t0) / 1000 / timeDomain.value.total) * PANE_W

const measuredPts = key => {
  const s = scaleOf(key)
  const n = props.series.length
  const { hasTs } = timeAnchor.value
  return props.series.map((d, i) => {
    const v = Number(d[key])
    let x
    if (hasTs) {
      const ms = toMs(d.timestamp)
      x = ms != null ? xOfMs(ms) : (i / Math.max(1, n - 1)) * histW.value
    } else {
      x = (i / Math.max(1, n - 1)) * histW.value
    }
    return { x, y: s.toY(Number.isFinite(v) ? v : s.min) }
  })
}

const lastMeasured = key => {
  const p = measuredPts(key)
  return p.length ? p[p.length - 1] : null
}

const predPts = key => {
  if (!props.predictions.length) return []
  const s = scaleOf(key)
  const anchor = lastMeasured(key)
  if (!anchor) return []
  const { total } = timeDomain.value
  return props.predictions.map(p => {
    const v = Number(p[key] ?? p[`${key}Level`])
    const t = Number(p.time) || 0
    return {
      x: Math.min(PANE_W, histW.value + (t / total) * PANE_W),
      y: s.toY(Number.isFinite(v) ? v : s.min),
      extrapolated: !!p.extrapolated
    }
  })
}

/** 预测点按 extrapolated 标记分段：超出可靠时域的段透明度更低。
 *  段间共享端点坐标，折线视觉上连续。 */
const predSegments = key => {
  const pts = predPts(key)
  if (!pts.length) return []
  const anchor = lastMeasured(key)
  const segs = []
  let cur = { extrapolated: !!pts[0].extrapolated, pts: anchor ? [anchor] : [] }
  for (const p of pts) {
    if (!!p.extrapolated !== cur.extrapolated) {
      cur.pts.push(p)
      segs.push(cur)
      cur = { extrapolated: !!p.extrapolated, pts: [p] }
    } else {
      cur.pts.push(p)
    }
  }
  segs.push(cur)
  return segs.filter(s => s.pts.length > 1)
}

const hasExtrapolated = computed(() => props.predictions.some(p => p.extrapolated))
/** 烟雾/氧气是推导值（模型只输出温度/CO/CO₂），可见时要如实标注 */
const derivedVisible = computed(() => visible.smoke || visible.oxygen)

/** 预测区起点（最后一个实测点） */
const predZoneX = computed(() => lastMeasured('temperature')?.x ?? histW.value)

/**
 * 模型段 / 增长律段的分界位置（画在图上的 x）。
 * 优先用 basis 字段的实际切分，其次退回"训练时域"这个粗界。
 */
const basisSwitchX = computed(() => {
  if (!props.predictions.length) return null
  const pts = predPts('temperature')
  if (!pts.length) return null

  // ① basis 字段明确标了切分点
  const cut = props.lstmSeconds
  if (Number.isFinite(cut) && cut > 0) {
    const first = props.predictions[0]
    const step = Number(first?.time) > 0 ? Number(first.time) : null
    if (step != null) {
      const idx = props.predictions.findIndex(p => Number(p.time) >= cut)
      if (idx >= 0) return pts[idx]?.x ?? null
    }
  }
  // ② 退而求其次：用预测点里第一个 basis !== 'lstm' 的位置
  const idx = props.predictions.findIndex(p => p.basis && p.basis !== 'lstm')
  if (idx >= 0) return pts[idx]?.x ?? null
  // ③ 最后才用训练时域
  const h = props.trainedHorizonSeconds
  if (!Number.isFinite(h) || h <= 0) return null
  const first = props.predictions[0]
  const step = Number(first?.time) > 0 ? Number(first.time) : null
  if (step == null) return null
  const j = props.predictions.findIndex(p => Number(p.time) > h)
  return j >= 0 ? (pts[j]?.x ?? null) : null
})

const toPoints = pts => pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')

/* ------------------------------------------------------------------ */
/* 数值与变化量                                                        */
/* ------------------------------------------------------------------ */
const fmtVal = (key, v) => {
  const m = METRICS.find(x => x.key === key)
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return m.digits === 0 ? Math.round(n).toLocaleString('zh-CN') : n.toFixed(m.digits)
}

const lastValue = key => {
  const d = props.series[props.series.length - 1]
  return d ? fmtVal(key, d[key]) : '--'
}

/** 与窗口起点的变化量 —— 这才是"数据在变化"最直接的证据 */
const deltaOf = key => {
  if (props.series.length < 2) return null
  const a = Number(props.series[0][key])
  const b = Number(props.series[props.series.length - 1][key])
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null
  return b - a
}

const deltaText = key => {
  const d = deltaOf(key)
  if (d === null) return ''
  const m = METRICS.find(x => x.key === key)
  const sign = d > 0 ? '+' : ''
  const abs = Math.abs(d)
  const txt = m.digits === 0 ? Math.round(abs).toLocaleString('zh-CN') : abs.toFixed(m.digits)
  return `${sign}${txt}${m.unit}`
}

const deltaClass = key => {
  const d = deltaOf(key)
  if (d === null || Math.abs(d) < 1e-9) return 'is-flat'
  const m = METRICS.find(x => x.key === key)
  // 氧气下降才是恶化
  const bad = m.invert ? d < 0 : d > 0
  return bad ? 'is-bad' : 'is-good'
}

/** X 轴起点相对现在的秒数 */
const timeStart = computed(() => -Math.round(timeDomain.value.histSpan))
</script>

<style scoped>
.trend { display: flex; flex-direction: column; gap: var(--sp-2); }

.trend__legend { display: flex; flex-wrap: wrap; gap: var(--sp-1); }
.trend__chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: opacity var(--transition-fast), border-color var(--transition-fast);
}
.trend__chip.is-off { opacity: 0.4; }
.trend__chip:hover { border-color: rgba(148, 197, 255, 0.28); }
.trend__chip-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--chip); flex: none;
}
.trend__chip-val {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  font-weight: 600;
}

.trend__panes { display: flex; flex-direction: column; gap: var(--sp-2); }

.pane {
  background: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  padding: 6px 8px 4px;
}

.pane__head {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  margin-bottom: 2px;
}
.pane__name { font-size: var(--fs-xs); font-weight: 600; }
.pane__now {
  margin-left: auto;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--fs-sm);
  font-weight: 700;
  color: var(--text-primary);
}
.pane__now small { font-size: var(--fs-xs); font-weight: 500; opacity: 0.7; margin-left: 2px; }
.pane__delta {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--fs-xs);
  min-width: 62px;
  text-align: right;
}
.pane__delta.is-bad { color: var(--c-danger); }
.pane__delta.is-good { color: var(--c-success); }
.pane__delta.is-flat { color: var(--text-muted); }

.pane__plot { position: relative; }
.pane__plot svg { width: 100%; height: 54px; display: block; overflow: visible; }

.pane__predzone { fill: rgba(148, 197, 255, 0.06); }
.pane__oodzone { fill: rgba(239, 68, 68, 0.07); }
.pane__axis { stroke: var(--border-subtle); stroke-width: 1; }
.pane__thr { stroke-width: 1; stroke-dasharray: 2 3; }
.pane__thr--warn { stroke: rgba(245, 158, 11, 0.45); }
.pane__thr--crit { stroke: rgba(239, 68, 68, 0.5); }

.pane__line {
  fill: none;
  stroke-width: 1.6;
  stroke-linejoin: round;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}
.pane__line--pred { stroke-dasharray: 3 3; stroke-width: 1.3; opacity: 0.9; }
/* 超出单次可靠时域的累积外推段：更浅、更细，与模型输出区分 */
.pane__line--ex { opacity: 0.45; stroke-width: 1.1; }
.pane__dot { vector-effect: non-scaling-stroke; }

.pane__y {
  position: absolute;
  right: 0;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 9px;
  color: var(--text-muted);
  background: rgba(10, 20, 35, 0.72);
  padding: 0 3px;
  border-radius: 3px;
  pointer-events: none;
}
.pane__y--top { top: -2px; }
.pane__y--bot { bottom: -2px; }

.trend__xaxis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.trend__xmid { text-align: center; }
</style>
