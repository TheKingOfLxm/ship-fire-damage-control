<script setup>
import { useToast } from '@/composables/useToast'

const { items, dismiss } = useToast()

const ICONS = {
  success: '✓',
  error: '✕',
  warning: '!',
  info: 'i'
}
</script>

<template>
  <div class="toast-stack" role="region" aria-label="系统通知" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="t in items"
        :key="t.id"
        class="toast"
        :class="`toast--${t.type}`"
        role="status"
      >
        <span class="toast__icon" aria-hidden="true">{{ ICONS[t.type] }}</span>
        <div class="toast__body">
          <p class="toast__title">{{ t.title }}</p>
          <p v-if="t.desc" class="toast__desc">{{ t.desc }}</p>
        </div>
        <button class="toast__close" aria-label="关闭通知" @click="dismiss(t.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast__icon {
  flex: none;
  width: 20px;
  height: 20px;
  margin-top: 1px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.14);
}
.toast--success .toast__icon { background: rgba(16, 185, 129, 0.22); color: #6ee7b7; }
.toast--error   .toast__icon { background: rgba(239, 68, 68, 0.22);  color: #fca5a5; }
.toast--warning .toast__icon { background: rgba(245, 158, 11, 0.22); color: #fcd34d; }
.toast--info    .toast__icon { background: rgba(56, 189, 248, 0.22); color: #7dd3fc; }

.toast__close {
  flex: none;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-muted);
  font-size: 16px;
  line-height: 1;
  transition: color var(--transition-fast), background var(--transition-fast);
}
.toast__close:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.08); }
</style>
