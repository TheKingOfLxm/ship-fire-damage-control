<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import MainView from './MainView.vue'
import UnifiedPanel from './UnifiedPanel.vue'
import ToastHost from '@/components/ToastHost.vue'
import { useFireStore } from '@/stores/fire'
import { provideOpsConsole } from '@/composables/useOpsConsole'
import { useToast } from '@/composables/useToast'
import Logger from '@/utils/logger'

const fireStore = useFireStore()
const toast = useToast()
/** 全局唯一状态容器：左右面板共享同一份读数/告警/预测 */
const ops = provideOpsConsole()

const busy = ref(false)
const panelOpen = ref({ left: false, right: false })

const selectedCompartment = computed(() => fireStore.selectedCompartment)
const isFireActive = computed(() => ops.fires[ops.selectedId.value]?.active === true)
// ops.fireIntensity 本身是 ref，必须取 .value；否则 computed 里包的是 ref 对象，
// 模板自动解包后传给 MainView 的就是一个 Ref，Number 校验直接失败。
const fireIntensity = computed(() => ops.fireIntensity.value)

/* ---------------- 舱室选择 ---------------- */
async function handleSelectCompartment(id, fromScene = false) {
  if (id == null) {
    fireStore.clearSelection()
    await ops.selectCompartment(null)
    return
  }
  fireStore.selectCompartment(id)
  await ops.selectCompartment(id)
  // 窄屏下只有从三维视图点进来才收起抽屉；面板内选择要留着，
  // 否则用户没法接着下发指令。
  if (fromScene && window.innerWidth <= 1100) {
    panelOpen.value = { left: false, right: false }
  }
}

function handleExitCompartment() {
  fireStore.clearSelection()
  ops.selectCompartment(null)
}

/* ---------------- 指令下发 ---------------- */
const TOAST_BY_ACTION = {
  start: { ok: 'success', fallback: 'warning' },
  stop: { ok: 'success', fallback: 'info' },
  suppress: { ok: 'success', fallback: 'info' },
  evacuate: { ok: 'success', fallback: 'info' },
  reset: { ok: 'success', fallback: 'info' }
}

async function send(action) {
  const id = selectedCompartment.value
  if (id == null) {
    toast.warning('请先选择舱室')
    return
  }
  if (busy.value) return
  busy.value = true
  try {
    const r = await ops.command(id, action)
    const level = r.via === 'fallback' ? TOAST_BY_ACTION[action].fallback : TOAST_BY_ACTION[action].ok
    if (r.ok) {
      toast[level](r.message)
      // 三维火焰强度跟随后端返回的火灾严重度
      if (r.via === 'backend') fireStore.setFireState(id, ops.fires[id]?.active === true)
    } else {
      toast.error(r.message)
    }
  } finally {
    busy.value = false
  }
}

const toggleFire = () => send(ops.fires[ops.selectedId.value]?.active ? 'stop' : 'start')
const suppressFire = () => send('suppress')
const evacuateCrew = () => send('evacuate')
const resetFire = () => send('reset')

/* ---------------- 生命周期 ---------------- */
onMounted(() => {
  ops.start()
  Logger.lifecycle('myIndex', 'mounted', '指控台启动')
})

onBeforeUnmount(() => {
  ops.stop()
})
</script>

<template>
  <div class="app-shell">
    <!-- 窄屏抽屉唤出：仅在 <=1100px 显示 -->
    <button
      class="drawer-toggle drawer-toggle--left"
      :aria-expanded="panelOpen.left"
      @click="panelOpen.left = !panelOpen.left"
    >
      <i class="fas fa-compass" aria-hidden="true"></i> 态势
    </button>
    <button
      class="drawer-toggle drawer-toggle--right"
      :aria-expanded="panelOpen.right"
      @click="panelOpen.right = !panelOpen.right"
    >
      控制 <i class="fas fa-sliders" aria-hidden="true"></i>
    </button>

    <MainView
      :selected-compartment="selectedCompartment"
      :is-fire-active="isFireActive"
      :fire-intensity="fireIntensity"
      @select-compartment="handleSelectCompartment"
      @exit-compartment="handleExitCompartment"
    />

    <!-- 左侧：态势 -->
    <UnifiedPanel
      side="left"
      panel-title="智能损管 · 态势总览"
      :visible-tabs="['ship', 'monitor', 'ai']"
      default-tab="monitor"
      :selected-compartment="selectedCompartment"
      :is-fire-active="isFireActive"
      :busy="busy"
      :is-open="panelOpen.left"
    />

    <!-- 右侧：控制 -->
    <UnifiedPanel
      side="right"
      panel-title="智能损管 · 控制台"
      :visible-tabs="['compartments', 'fire', 'prediction', 'alerts']"
      default-tab="compartments"
      :selected-compartment="selectedCompartment"
      :is-fire-active="isFireActive"
      :busy="busy"
      :is-open="panelOpen.right"
      @select-compartment="handleSelectCompartment"
      @toggle-fire="toggleFire"
      @suppress-fire="suppressFire"
      @evacuate-crew="evacuateCrew"
      @reset-fire="resetFire"
    />

    <ToastHost />
  </div>
</template>

<style scoped>
/* 窄屏抽屉唤出按钮 —— 只在单栏断点下出现 */
.app-shell :deep(.drawer-toggle) {
  position: absolute;
  top: 12px;
  z-index: var(--z-drawer);
  display: none;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: var(--r-md);
  background: var(--bg-overlay);
  backdrop-filter: blur(12px);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}

@media (max-width: 1100px) {
  .app-shell :deep(.drawer-toggle--left) { left: 12px; display: inline-flex; }
  .app-shell :deep(.drawer-toggle--right) { right: 12px; display: inline-flex; }
}
</style>
