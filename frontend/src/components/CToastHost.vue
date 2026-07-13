<script setup lang="ts">
import type { Component } from 'vue';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from '@lucide/vue';
import { useToastStore, type ToastType } from '../stores/toast';

const toastStore = useToastStore();

const iconByType: Record<ToastType, Component> = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
};

const roleByType: Record<ToastType, 'status' | 'alert'> = {
  success: 'status',
  info: 'status',
  warning: 'alert',
  error: 'alert',
};

const titleByType: Record<ToastType, string> = {
  success: '已完成',
  info: '提示',
  warning: '注意',
  error: '操作失败',
};

const typeClasses: Record<ToastType, string> = {
  success: 'toast-success border-success-500/20',
  info: 'toast-info border-brand-500/20',
  warning: 'toast-warning border-warning-500/25',
  error: 'toast-error border-error-500/25',
};

const iconClasses: Record<ToastType, string> = {
  success: 'bg-success-500/12 text-tone-success',
  info: 'bg-brand-500/12 text-tone-brand',
  warning: 'bg-warning-500/14 text-tone-warning',
  error: 'bg-error-500/12 text-tone-error',
};

const accentClasses: Record<ToastType, string> = {
  success: 'bg-success-500',
  info: 'bg-brand-500',
  warning: 'bg-warning-500',
  error: 'bg-error-500',
};
</script>

<template>
  <Teleport to="body">
    <div
      :class="[
        'toast-host pointer-events-none fixed top-4 right-4 left-4 z-[100] md:top-6 md:right-6 md:left-auto',
        toastStore.isPaused ? 'toast-paused' : '',
      ]"
      aria-live="polite"
      aria-label="全局提示"
    >
      <TransitionGroup
        name="toast"
        tag="div"
        class="toast-list pointer-events-auto flex w-full max-w-[25rem] flex-col gap-2.5 md:w-[25rem]"
        @mouseenter="toastStore.pauseAll"
        @mouseleave="toastStore.resumeAll"
      >
        <div
          v-for="toast in toastStore.toasts"
          :key="toast.id"
          :role="roleByType[toast.type]"
          :class="[
            'toast-item pointer-events-auto relative overflow-hidden rounded-md border bg-surface/95 p-3.5 pr-3 text-text-strong backdrop-blur-xl',
            typeClasses[toast.type],
          ]"
        >
          <div class="flex items-start gap-3 pl-1">
            <div
              :class="[
                'toast-icon-shell grid h-8 w-8 shrink-0 place-items-center rounded-md',
                iconClasses[toast.type],
              ]"
            >
              <component :is="iconByType[toast.type]" :size="18" aria-hidden="true" />
            </div>
            <div class="min-w-0 flex-1 pt-0.5">
              <div class="toast-title text-xs leading-4 font-semibold text-text-strong">
                {{ titleByType[toast.type] }}
              </div>
              <div class="mt-0.5 text-sm leading-5 break-words text-text">
                {{ toast.message }}
              </div>
            </div>
            <button
              type="button"
              class="-mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-text"
              aria-label="关闭提示"
              @click="toastStore.remove(toast.id)"
            >
              <X :size="15" aria-hidden="true" />
            </button>
          </div>
          <div
            v-if="toast.duration > 0"
            class="toast-progress absolute right-0 bottom-0 left-0 h-0.5 overflow-hidden bg-surface-3"
          >
            <div
              :class="['toast-progress-bar h-full origin-center', accentClasses[toast.type]]"
              :style="{ '--toast-duration': `${toast.duration}ms` }"
            />
          </div>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
