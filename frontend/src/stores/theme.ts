import { defineStore } from 'pinia';
import { ref } from 'vue';

export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'admin-theme';
export const THEME_TRANSITION_MS = 520;
/** 图标切换延迟到主题过渡的最低对比点，避免亮暗图标在高反差阶段跳变。 */
const THEME_ICON_LOW_CONTRAST_TIME_RATIO = 0.27490147862248004;
export const THEME_ICON_SWAP_DELAY_MS = Math.round(
  THEME_TRANSITION_MS * THEME_ICON_LOW_CONTRAST_TIME_RATIO,
);
let transitionTimer: number | undefined;

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>((localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'light');

  function apply(value: ThemeMode): void {
    document.documentElement.classList.toggle('dark', value === 'dark');
    document.documentElement.style.colorScheme = value;
  }

  function set(value: ThemeMode): void {
    mode.value = value;
    localStorage.setItem(STORAGE_KEY, value);
    window.clearTimeout(transitionTimer);
    document.documentElement.classList.add('theme-transitioning');
    apply(value);
    transitionTimer = window.setTimeout(() => {
      document.documentElement.classList.remove('theme-transitioning');
    }, THEME_TRANSITION_MS);
  }

  function toggle(): void {
    set(mode.value === 'dark' ? 'light' : 'dark');
  }

  function init(): void {
    apply(mode.value);
  }

  return { mode, set, toggle, init };
});
