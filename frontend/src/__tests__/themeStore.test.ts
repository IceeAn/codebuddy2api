import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { THEME_ICON_SWAP_DELAY_MS, THEME_TRANSITION_MS, useThemeStore } from '../stores/theme';

const stylesCss = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf-8');

function cssRuleBody(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesCss.match(new RegExp(`(?:^|\\n)${escapedSelector}\\s*\\{([^}]*)\\}`));
  if (!match) {
    throw new Error(`找不到 CSS 规则：${selector}`);
  }
  return match[1];
}

describe('useThemeStore', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark', 'theme-transitioning');
    document.documentElement.style.colorScheme = '';
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    document.documentElement.classList.remove('dark', 'theme-transitioning');
    document.documentElement.style.colorScheme = '';
  });

  it('localStorage 无值时初始 mode 为 light', () => {
    const store = useThemeStore();
    expect(store.mode).toBe('light');
  });

  it('localStorage 存在 admin-theme=dark 时初始 mode 为 dark', () => {
    localStorage.setItem('admin-theme', 'dark');
    const store = useThemeStore();
    expect(store.mode).toBe('dark');
  });

  it("set('dark') 更新 mode、持久化、添加 .dark class、设置 colorScheme", () => {
    const store = useThemeStore();
    store.set('dark');

    expect(store.mode).toBe('dark');
    expect(localStorage.getItem('admin-theme')).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe('dark');
  });

  it('set() 添加主题过渡 class，并在 520ms 后移除', () => {
    vi.useFakeTimers();
    const store = useThemeStore();
    store.set('dark');

    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(true);

    vi.advanceTimersByTime(519);
    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(true);

    vi.advanceTimersByTime(1);
    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(false);
  });

  it('主题图标切换延迟匹配前景和背景的最低对比点', () => {
    expect(THEME_TRANSITION_MS).toBe(520);
    expect(THEME_ICON_SWAP_DELAY_MS).toBe(143);
  });

  it('正文和标题回退使用系统无衬线字体栈', () => {
    expect(stylesCss).toContain(
      "--font-sans: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
    );
    expect(stylesCss).toContain(
      "--font-display: 'Space Grotesk', ui-sans-serif, system-ui, sans-serif",
    );
  });

  it('等宽文本使用系统字体栈', () => {
    expect(stylesCss).toContain('--font-mono: ui-monospace, SFMono-Regular, Consolas, monospace');
  });

  it("set('light') 移除 .dark class 并设置 colorScheme 为 light", () => {
    const store = useThemeStore();
    store.set('dark');
    store.set('light');

    expect(store.mode).toBe('light');
    expect(localStorage.getItem('admin-theme')).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe('light');
  });

  it('toggle() 从 light 翻转到 dark，再翻转回 light', () => {
    const store = useThemeStore();
    expect(store.mode).toBe('light');

    store.toggle();
    expect(store.mode).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    store.toggle();
    expect(store.mode).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('init() 应用当前 mode 到 DOM（默认 light 不加 .dark）', () => {
    const store = useThemeStore();
    store.init();

    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe('light');
  });

  it('init() 应用 dark mode 时添加 .dark class', () => {
    localStorage.setItem('admin-theme', 'dark');
    const store = useThemeStore();
    store.init();

    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe('dark');
  });

  it('set 连续调用多次，DOM class 始终与最终 mode 一致（无残留）', () => {
    const store = useThemeStore();
    store.set('dark');
    store.set('light');
    store.set('dark');
    store.set('light');
    store.set('dark');

    expect(store.mode).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('admin-theme')).toBe('dark');
  });

  it('输入控件快速过渡只在非主题切换期间生效，避免覆盖主题慢速渐变', () => {
    const standaloneControlRule = stylesCss.match(/(?:^|\n)\.c-control-focus\s*\{([^}]*)}/);
    const normalControlTransition = cssRuleBody('html:not(.theme-transitioning) .c-control-focus');

    expect(standaloneControlRule?.[1] ?? '').not.toContain('transition-duration');
    expect(normalControlTransition).toContain('transition-duration: var(--duration-fast)');
  });

  it('通用焦点样式使用内缩实线与完整外柔光，并保留组件原阴影', () => {
    const rootRule = cssRuleBody(':root');
    const controlRule = cssRuleBody('.c-control-focus:focus');
    const focusVisibleRule = cssRuleBody(':focus-visible');
    const checkboxRule = cssRuleBody('.peer:focus-visible + .c-checkbox-box');

    expect(rootRule).toContain('--focus-ring-inner: oklch(0.58 0.21 277 / 0.7)');
    expect(controlRule).toContain('--tw-ring-shadow:');
    expect(controlRule).not.toContain('var(--focus-ring-inner)');
    expect(controlRule).toContain('var(--tw-shadow');
    expect(controlRule).not.toContain('box-shadow: 0 0 0 3px');
    expect(focusVisibleRule).toContain('outline: 1px solid var(--focus-ring-inner)');
    expect(focusVisibleRule).toContain('outline-offset: -1px');
    expect(focusVisibleRule).toContain('--tw-ring-shadow:');
    expect(focusVisibleRule).toContain('0 0 0 3px var(--focus-ring)');
    expect(focusVisibleRule).not.toContain('0 0 0 1px');
    expect(focusVisibleRule).toContain('var(--tw-shadow');
    expect(focusVisibleRule).not.toContain('box-shadow: 0 0 0 3px');
    expect(checkboxRule).toContain('box-shadow: 0 0 0 3px');
  });

  it('错误输入聚焦时保留错误色边框与焦点环', () => {
    const errorFocusRule = cssRuleBody(".c-control-focus[aria-invalid='true']:focus");

    expect(errorFocusRule).toContain('border-color: var(--color-error-500)');
    expect(errorFocusRule).toContain('--tw-ring-shadow:');
    expect(errorFocusRule).toContain('var(--focus-ring-error)');
  });

  it('列表操作按钮缩小并保留与表格线的纵向间距', () => {
    const buttonRule = cssRuleBody('.table-action-button');
    const groupRule = cssRuleBody('.table-action-group');

    expect(buttonRule).toContain('width: 1.75rem');
    expect(buttonRule).toContain('height: 1.75rem');
    expect(groupRule).toContain('padding-block: 0.125rem');
  });

  it('亮色主题侧边栏使用浅色导轨和品牌色交互 token', () => {
    const rootRule = cssRuleBody(':root');

    expect(rootRule).toContain('--rail: #ffffff');
    expect(rootRule).toContain('--rail-text: #334155');
    expect(rootRule).toContain('--rail-text-strong: #0f172a');
    expect(rootRule).toContain('--rail-muted: #64748b');
    expect(rootRule).toContain('--rail-border: #e2e8f0');
    expect(rootRule).toContain('--rail-hover: oklch(0.58 0.21 277 / 0.07)');
    expect(rootRule).toContain('--rail-active: oklch(0.58 0.21 277 / 0.12)');
    expect(rootRule).toContain('--rail-active-text: var(--color-brand-700)');
  });
});
