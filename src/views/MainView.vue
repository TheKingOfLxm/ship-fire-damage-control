<template>
  <div class="scene-root" ref="rootRef">
    <canvas ref="canvasRef" class="scene-canvas" aria-label="舰船三维态势视图"></canvas>

    <!-- ── 舱室标签（由 3D 锚点投影到屏幕） ── -->
    <div class="comp-markers" aria-hidden="false">
      <button
        v-for="lb in labels"
        v-show="lb.visible"
        :key="lb.id"
        class="comp-marker"
        :class="{
          'is-hover': lb.id === hoveredId,
          'is-active': lb.id === activeCompartment,
          'is-fire': fireStateOf(lb.id).active
        }"
        :style="{ left: lb.x + 'px', top: lb.y + 'px' }"
        :aria-label="`进入${lb.name}舱室`"
        @click="handleMarkerClick(lb.id)"
      >
        <span class="marker-dot" :class="{ 'dot--pulse': fireStateOf(lb.id).active }"></span>
        <span class="marker-name">{{ lb.name }}</span>
        <span class="marker-code">{{ lb.code }}</span>
        <span v-if="fireStateOf(lb.id).active" class="marker-sev">
          {{ Math.round((fireStateOf(lb.id).severity || 0) * 100) }}%
        </span>
      </button>
    </div>

    <!-- ── 顶部状态条 ── -->
    <header class="hud-top">
      <div class="hud-top__left">
        <div class="sys-state" :class="{ 'is-alarm': hasActiveFire }">
          <span class="sys-state__dot"></span>
          <span class="sys-state__text">{{ hasActiveFire ? `火灾告警 ${activeFireCount} 处` : '系统正常' }}</span>
        </div>
        <time class="hud-clock">{{ clockText }}</time>
      </div>

      <div class="hud-top__center">
        <h1 class="hud-title">{{ ship.name }}</h1>
        <p class="hud-subtitle">
          {{ ship.hullNumber }} · {{ ship.type }} · 全长 {{ ship.length }}m
        </p>
      </div>

      <div class="hud-top__right">
        <div class="fire-chip" :class="{ 'is-active': hasActiveFire }">
          <span class="fire-chip__dot"></span>
          {{ hasActiveFire ? `${activeFireCount} 舱起火` : '待命' }}
        </div>
      </div>
    </header>

    <!-- ── 视图控制 ── -->
    <div class="hud-controls">
      <button
        v-if="activeCompartment"
        class="ctl-btn ctl-btn--primary"
        @click="handleExit"
      >
        <i class="icon" aria-hidden="true">←</i>
        返回整船
      </button>
      <button class="ctl-btn" @click="resetView" title="重置视角 (R)">
        <i class="icon" aria-hidden="true">⟲</i>
        重置视角
      </button>
    </div>

    <!-- ── 悬停信息卡 ── -->
    <transition name="tip">
      <div v-if="hoverInfo" class="hud-tip" role="status">
        <header class="hud-tip__head">
          <span class="hud-tip__name">{{ hoverInfo.name }}</span>
          <span class="hud-tip__risk" :data-risk="hoverInfo.risk">{{ riskLabel(hoverInfo.risk) }}</span>
        </header>
        <p class="hud-tip__deck">{{ hoverInfo.deck }}</p>
        <p class="hud-tip__desc">{{ hoverInfo.description }}</p>
        <p class="hud-tip__hint">单击进入舱室</p>
      </div>
    </transition>

    <!-- ── 加载遮罩（真实进度） ── -->
    <div v-if="loading" class="overlay" role="status" aria-live="polite">
      <div class="overlay__card">
        <div class="overlay__ring" aria-hidden="true"></div>
        <p class="overlay__title">正在加载舰船模型</p>
        <div
          class="overlay__track"
          role="progressbar"
          :aria-valuenow="Math.round(progress * 100)"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <div class="overlay__bar" :style="{ width: Math.round(progress * 100) + '%' }"></div>
        </div>
        <p class="overlay__pct">{{ Math.round(progress * 100) }}%</p>
      </div>
    </div>

    <!-- ── 加载失败 ── -->
    <div v-else-if="loadError" class="overlay overlay--error" role="alert">
      <div class="overlay__card">
        <p class="overlay__title overlay__title--error">模型加载失败</p>
        <p class="overlay__msg">{{ loadError }}</p>
        <button class="ctl-btn ctl-btn--primary" @click="retry">重新加载</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useShipScene } from '@/composables/useShipScene'
import { useOpsConsole } from '@/composables/useOpsConsole'
import { COMPARTMENTS, SHIP, getCompartmentById, getRiskLevel } from '@/config/shipLayout'
import Logger from '@/utils/logger'

const ops = useOpsConsole()

const props = defineProps({
  selectedCompartment: { type: Number, default: null },
  isFireActive: { type: Boolean, default: false },
  fireIntensity: { type: Number, default: 0.4 }
})

const emit = defineEmits(['select-compartment', 'exit-compartment'])

const rootRef = ref(null)
const canvasRef = ref(null)
const clockText = ref('')

const ship = SHIP

const scene = useShipScene({
  canvasRef,
  containerRef: rootRef,
  onSelectCompartment: id => emit('select-compartment', id)
})

const { loading, progress, loadError, hoveredId, activeCompartment, labels, resetView } = scene

const hoverInfo = computed(() => (hoveredId.value ? getCompartmentById(hoveredId.value) : null))
const riskLabel = key => getRiskLevel(key).label

/* ---------------- 全舰火情 ----------------
 * isFireActive 只描述"当前选中舱"，未选中舱室时恒为 false。
 * 顶部状态条与 3D 标牌说的是全舰态势，必须看全部舱室，
 * 否则三个舱在烧、界面却写着"系统正常"。
 */
const fireStateOf = id => ops.fires[id] || { active: false, severity: 0 }
// activeFires 是 ref，只有模板里才会自动解包；这里在 JS 中必须显式 .value
const activeFireCount = computed(() => ops.activeFires.value.length)
const hasActiveFire = computed(() => activeFireCount.value > 0)

/* ---------------- 交互 ---------------- */
function handleMarkerClick(id) {
  emit('select-compartment', id, true)
}

function handleExit() {
  scene.exitCompartment()
  emit('exit-compartment')
}

function retry() {
  window.location.reload()
}

/**
 * 选中舱室 → 进入舱室视图
 *
 * 语义统一为「选中即进入」，与舱室标签上"单击进入舱室"的提示一致，
 * 也让右侧舱室列表与三维视图的点击行为完全相同。
 * 退出通过「返回整船」按钮或 Esc 键完成。
 */
watch(
  () => props.selectedCompartment,
  id => {
    if (id == null) {
      if (activeCompartment.value) handleExit()
      else scene.resetView()
      return
    }
    if (activeCompartment.value === id) return
    scene.enterCompartment(id)
  }
)

/**
 * 火灾状态与舱室选择合并监听。
 * 分开监听会有漏洞：进入舱室时若火灾早已处于激活态，
 * isFireActive 不会产生变化，导致场景里看不到火焰。
 */
watch(
  [() => props.selectedCompartment, () => props.isFireActive],
  ([id, active]) => {
    if (id == null) return
    scene.setFireState(id, active, props.fireIntensity)
  },
  { immediate: true }
)

watch(
  () => props.fireIntensity,
  v => scene.setFireIntensity(v)
)

/* ---------------- 时钟 ---------------- */
let clockTimer = null
function tick() {
  const d = new Date()
  clockText.value = d.toLocaleTimeString('zh-CN', { hour12: false })
}

/* ---------------- 键盘快捷键 ---------------- */
function onKey(e) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.key === 'r' || e.key === 'R') {
    resetView()
  } else if (e.key === 'Escape' && activeCompartment.value) {
    handleExit()
  } else if (e.key >= '1' && e.key <= String(COMPARTMENTS.length)) {
    const c = COMPARTMENTS[Number(e.key) - 1]
    if (c) emit('select-compartment', c.id)
  }
}

onMounted(() => {
  tick()
  clockTimer = setInterval(tick, 1000)
  window.addEventListener('keydown', onKey)
  Logger.lifecycle('MainView', 'mounted', '三维态势视图已挂载')
})

onBeforeUnmount(() => {
  clearInterval(clockTimer)
  window.removeEventListener('keydown', onKey)
})
</script>

<style scoped>
.scene-root {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #060d16;
}

.scene-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  cursor: grab;
}

.scene-canvas:active {
  cursor: grabbing;
}

/* ───────── 舱室标签 ───────── */
.comp-markers {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 20;
}

.comp-marker {
  position: absolute;
  transform: translate(-50%, -50%);
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px 5px 8px;
  border: 1px solid rgba(148, 197, 255, 0.35);
  border-radius: 999px;
  background: rgba(9, 20, 35, 0.78);
  backdrop-filter: blur(10px);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  pointer-events: auto;
  transition: background var(--transition-fast), border-color var(--transition-fast),
    color var(--transition-fast), transform var(--transition-fast);
}

.comp-marker:hover,
.comp-marker.is-hover {
  background: rgba(23, 48, 82, 0.92);
  border-color: var(--c-primary);
  color: var(--text-primary);
  transform: translate(-50%, -50%) scale(1.04);
}

.comp-marker.is-active {
  background: rgba(37, 99, 235, 0.28);
  border-color: var(--c-primary);
  color: #fff;
}

/* 起火舱室在 3D 上必须一眼可辨，不能和正常舱共用绿色圆点 */
.comp-marker.is-fire {
  border-color: var(--c-danger);
  background: rgba(127, 29, 29, 0.55);
  color: #fff;
}
.marker-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c-success);
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.18);
  flex: none;
}
.comp-marker.is-fire .marker-dot {
  background: var(--c-danger);
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.28);
}
.marker-dot.dot--pulse {
  animation: markerPulse 1.4s ease-in-out infinite;
}
@keyframes markerPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(1.35); }
}
.marker-sev {
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #fca5a5;
}

.marker-code {
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
  padding-left: 5px;
  border-left: 1px solid rgba(148, 197, 255, 0.22);
}

/* ───────── 顶部状态条 ───────── */
.hud-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 30;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 16px;
  padding: 14px 22px;
  background: linear-gradient(180deg, rgba(6, 13, 22, 0.88) 0%, rgba(6, 13, 22, 0) 100%);
  pointer-events: none;
}

.hud-top__left { display: flex; align-items: center; gap: 18px; }
.hud-top__right { display: flex; justify-content: flex-end; }
.hud-top__center { text-align: center; }

.sys-state {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid rgba(16, 185, 129, 0.28);
  background: rgba(16, 185, 129, 0.10);
  color: var(--c-success);
  font-size: 12px;
  font-weight: 600;
}

.sys-state.is-alarm {
  border-color: rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.12);
  color: var(--c-danger);
}

.sys-state__dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: currentColor;
  animation: hud-pulse 2s ease-in-out infinite;
}

.hud-clock {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
}

.hud-title {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--text-primary);
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.6);
}

.hud-subtitle {
  margin: 3px 0 0;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}

.fire-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 13px;
  border-radius: 999px;
  border: 1px solid var(--border-color);
  background: rgba(15, 23, 42, 0.7);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
}

.fire-chip.is-active {
  border-color: rgba(239, 68, 68, 0.45);
  background: rgba(239, 68, 68, 0.14);
  color: var(--c-danger);
}

.fire-chip__dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: currentColor;
}

/* ───────── 视图控制 ───────── */
.hud-controls {
  position: absolute;
  left: 50%;
  bottom: 22px;
  transform: translateX(-50%);
  z-index: var(--z-hud);
  display: flex;
  gap: var(--sp-2);
}

/* 控件外观完全交给设计系统的 .ctl-btn —— 不要在这里重定义同名类：
   scoped 选择器会带上 [data-v-xxx] 属性，特异性高于全局的
   .ctl-btn--primary，会把主色按钮的底色覆盖掉。 */
.ctl-btn { flex: none; }

/* ───────── 悬停信息卡 ───────── */
.hud-tip {
  position: absolute;
  left: 22px;
  bottom: 22px;
  z-index: 30;
  width: min(320px, calc(100% - 44px));
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius);
  background: rgba(9, 16, 28, 0.93);
  backdrop-filter: blur(16px);
  box-shadow: var(--shadow-lg);
}

.hud-tip__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.hud-tip__name { font-size: 15px; font-weight: 700; color: var(--text-primary); }

.hud-tip__risk {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}
.hud-tip__risk[data-risk='critical'] { background: rgba(239,68,68,.16); color: #fca5a5; }
.hud-tip__risk[data-risk='high']     { background: rgba(249,115,22,.16); color: #fdba74; }
.hud-tip__risk[data-risk='medium']   { background: rgba(234,179,8,.16);  color: #fde047; }
.hud-tip__risk[data-risk='low']      { background: rgba(34,197,94,.16);  color: #86efac; }

.hud-tip__deck { margin: 0 0 8px; font-size: 11px; color: var(--text-muted); }

.hud-tip__desc {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-secondary);
}

.hud-tip__hint {
  margin: 0;
  font-size: 11px;
  color: var(--c-primary);
  font-weight: 500;
}

.tip-enter-active, .tip-leave-active { transition: opacity .18s ease, transform .18s ease; }
.tip-enter-from, .tip-leave-to { opacity: 0; transform: translateY(6px); }

/* ───────── 遮罩 ───────── */
.overlay {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  background: radial-gradient(circle at 50% 45%, #10203a 0%, #060d16 70%);
}

.overlay__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 40px 52px;
  text-align: center;
}

.overlay__ring {
  width: 46px; height: 46px;
  border: 3px solid rgba(59, 130, 246, 0.2);
  border-top-color: var(--c-primary);
  border-radius: 50%;
  animation: hud-spin .9s linear infinite;
}

.overlay__title { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-primary); }
.overlay__title--error { color: var(--c-danger); }
.overlay__msg { margin: 0; font-size: 12px; color: var(--text-muted); max-width: 340px; line-height: 1.6; }

.overlay__track {
  width: 260px; height: 4px;
  border-radius: 999px;
  background: rgba(148, 197, 255, 0.14);
  overflow: hidden;
}

.overlay__bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #3b82f6, #22d3ee);
  transition: width .25s ease;
}

.overlay__pct {
  margin: 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

@keyframes hud-spin { to { transform: rotate(360deg); } }
@keyframes hud-pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }

@media (max-width: 860px) {
  .hud-top { grid-template-columns: 1fr; gap: 8px; padding: 12px 16px; }
  .hud-top__left, .hud-top__right { justify-content: center; }
  .hud-title { font-size: 16px; }
  .hud-tip { left: 16px; right: 16px; bottom: 74px; width: auto; }
}
</style>
