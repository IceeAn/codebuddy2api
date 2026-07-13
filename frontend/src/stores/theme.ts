import { defineStore } from 'pinia';
import { ref } from 'vue';

export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'admin-theme';
export const THEME_TRANSITION_MS = 520;
export const THEME_ICON_SWAP_DELAY_MS = 143;
let transitionTimer: number | undefined;

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>((localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'light');

  function apply(value: ThemeMode): void {
    mode.value = value;
    localStorage.setItem(STORAGE_KEY, value);
    document.documentElement.classList.toggle('dark', value === 'dark');
    document.documentElement.style.colorScheme = value;
  }

  function set(value: ThemeMode): void {
    const root = document.documentElement;

    window.clearTimeout(transitionTimer);
    root.classList.add('theme-transitioning');
    apply(value);
    transitionTimer = window.setTimeout(() => {
      root.classList.remove('theme-transitioning');
      transitionTimer = undefined;
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
