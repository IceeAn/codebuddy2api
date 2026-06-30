import { defineStore } from 'pinia';
import { ref } from 'vue';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration: number;
}

interface ToastTimer {
  timeoutId?: ReturnType<typeof setTimeout>;
  startedAt: number;
  remaining: number;
}

const DEFAULT_DURATION: Record<ToastType, number> = {
  success: 3000,
  info: 4000,
  warning: 5000,
  error: 5000,
};

const MAX_TOASTS = 5;

/**
 * - 可在非组件上下文（如 Vue Query 的 QueryCache/MutationCache onError）直接 import 调用
 * - 自动按类型设置默认时长，duration=0 表示不自动消失
 * - 最多保留 MAX_TOASTS 条，超出移除最早的
 */
export const useToastStore = defineStore('toast', () => {
  const toasts = ref<ToastItem[]>([]);
  const isPaused = ref(false);
  const timers = new Map<string, ToastTimer>();

  function clearTimer(id: string): void {
    const timer = timers.get(id);
    if (timer) {
      clearTimeout(timer.timeoutId);
      timers.delete(id);
    }
  }

  function startTimer(id: string, remaining: number): void {
    const timer = timers.get(id)!;
    timer.startedAt = Date.now();
    timer.remaining = remaining;
    timer.timeoutId = setTimeout(() => remove(id), remaining);
  }

  function push(type: ToastType, message: string, duration?: number): void {
    const id = crypto.randomUUID();
    const resolvedDuration = duration ?? DEFAULT_DURATION[type];
    toasts.value.push({ id, type, message, duration: resolvedDuration });

    if (toasts.value.length > MAX_TOASTS) {
      clearTimer(toasts.value.shift()!.id);
    }

    if (resolvedDuration > 0) {
      timers.set(id, { startedAt: Date.now(), remaining: resolvedDuration });
      if (!isPaused.value) {
        startTimer(id, resolvedDuration);
      }
    }
  }

  function remove(id: string): void {
    clearTimer(id);
    const index = toasts.value.findIndex((t) => t.id === id);
    if (index !== -1) {
      toasts.value.splice(index, 1);
    }
    if (toasts.value.length === 0) {
      isPaused.value = false;
    }
  }

  function clear(): void {
    timers.forEach((timer) => {
      clearTimeout(timer.timeoutId);
    });
    timers.clear();
    toasts.value = [];
    isPaused.value = false;
  }

  function pauseAll(): void {
    if (isPaused.value) return;

    isPaused.value = true;
    const pausedAt = Date.now();
    timers.forEach((timer) => {
      clearTimeout(timer.timeoutId);
      timer.timeoutId = undefined;
      timer.remaining = Math.max(0, timer.remaining - (pausedAt - timer.startedAt));
    });
  }

  function resumeAll(): void {
    if (!isPaused.value) return;

    isPaused.value = false;
    toasts.value
      .filter((toast) => toast.duration > 0)
      .forEach((toast) => {
        const timer = timers.get(toast.id)!;
        if (timer.remaining <= 0) {
          remove(toast.id);
          return;
        }
        startTimer(toast.id, timer.remaining);
      });
  }

  return { toasts, isPaused, push, remove, clear, pauseAll, resumeAll };
});
