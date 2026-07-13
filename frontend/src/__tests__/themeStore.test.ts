import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { THEME_ICON_SWAP_DELAY_MS, THEME_TRANSITION_MS, useThemeStore } from '../stores/theme';

const stylesCss = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf-8');

function vueSource(directory: string): string {
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.name !== '__tests__')
    .map((entry) => {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) return vueSource(path);
      return entry.name.endsWith('.vue') ? readFileSync(path, 'utf-8') : '';
    })
    .join('\n');
}

function cssRuleBody(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesCss.match(new RegExp(`(?:^|\\n)${escapedSelector}\\s*\\{([^}]*)\\}`));
  if (!match) {
    throw new Error(`找不到 CSS 规则：${selector}`);
  }
  return match[1];
}

function expectOklabMix(rule: string, name: string, light: string, dark: string): void {
  const escape = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  expect(rule).toMatch(
    new RegExp(
      `--${name}:\\s*color-mix\\(\\s*in oklab,\\s*${escape(light)},\\s*${escape(dark)}\\s+var\\(--theme-dark-weight\\)\\s*\\)`,
    ),
  );
}

describe('useThemeStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
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

  it('set() 保持主题过渡 class 520ms，连续切换会从最后一次重新计时', () => {
    const store = useThemeStore();
    store.set('dark');

    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(true);
    vi.advanceTimersByTime(300);
    store.set('light');
    vi.advanceTimersByTime(519);
    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(true);

    vi.advanceTimersByTime(1);
    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(false);
  });

  it('主题只注册一个根级进度变量，后代不会各自创建颜色动画', () => {
    const rootTransition = cssRuleBody('.theme-transitioning');
    const descendantTransition = cssRuleBody('.theme-transitioning *');

    expect(stylesCss).toContain('@property --theme-progress');
    expect(stylesCss).not.toContain('@property --bg');
    expect(stylesCss).not.toContain('@property --surface');
    expect(stylesCss).not.toContain('@property --segment-active');
    expect(stylesCss).not.toContain('::view-transition');
    expect(rootTransition).toContain('transition-property: --theme-progress');
    expect(rootTransition).not.toContain('--bg');
    expect(rootTransition).toContain('transition-duration: var(--duration-theme)');
    expect(descendantTransition).toContain('transition: none !important');
  });

  it('所有动画语义颜色都由同一个 Oklab 进度派生，并保留原有亮暗端点', () => {
    const rootRule = cssRuleBody(':root');
    const darkRule = cssRuleBody('.dark');

    expect(rootRule).toContain('--theme-progress: 0');
    expect(rootRule).toContain('--theme-dark-weight: calc(var(--theme-progress) * 100%)');
    expect(darkRule).toContain('--theme-progress: 1');
    expect(darkRule).not.toContain('--bg:');

    const endpoints: Array<[string, string, string]> = [
      ['bg', '#f8fafc', '#020617'],
      ['surface', '#ffffff', '#0f172a'],
      ['surface-2', '#f1f5f9', '#1e293b'],
      ['surface-3', '#e2e8f0', '#334155'],
      ['segment-active', '#ffffff', '#334155'],
      ['rail', '#ffffff', '#080b14'],
      ['rail-2', '#f8fafc', '#0f172a'],
      ['rail-text', '#334155', '#cbd5e1'],
      ['rail-text-strong', '#0f172a', '#ffffff'],
      ['rail-muted', '#64748b', '#94a3b8'],
      ['rail-border', '#e2e8f0', '#141720'],
      ['rail-hover', 'oklch(0.58 0.21 277 / 0.07)', 'oklch(1 0 0 / 0.05)'],
      ['rail-active', 'oklch(0.58 0.21 277 / 0.12)', 'oklch(0.58 0.21 277 / 0.15)'],
      ['rail-active-text', 'var(--color-brand-700)', '#ffffff'],
      ['rail-active-indicator', 'var(--color-brand-500)', 'var(--color-brand-400)'],
      ['text', '#334155', '#f1f5f9'],
      ['text-strong', '#0f172a', '#ffffff'],
      ['muted', '#64748b', '#94a3b8'],
      ['border', '#e2e8f0', '#243049'],
      ['border-strong', '#cbd5e1', '#475569'],
      ['overlay', 'oklch(0.15 0.02 260 / 0.55)', 'oklch(0.1 0.02 260 / 0.68)'],
      ['focus-ring', 'oklch(0.58 0.21 277 / 0.3)', 'oklch(0.68 0.17 277 / 0.36)'],
      ['focus-ring-error', 'oklch(0.63 0.24 13 / 0.3)', 'oklch(0.72 0.2 13 / 0.36)'],
      ['tone-brand', 'var(--color-brand-700)', 'var(--color-brand-300)'],
      ['tone-success', 'var(--color-success-600)', 'var(--color-success-400)'],
      ['tone-warning', 'var(--color-warning-600)', 'var(--color-warning-400)'],
      ['tone-error', 'var(--color-error-600)', 'var(--color-error-400)'],
      ['tone-accent', 'var(--color-accent-600)', 'var(--color-accent-400)'],
      ['stat-brand', 'var(--color-brand-600)', 'var(--color-brand-300)'],
      ['stat-brand-bg', 'oklch(0.58 0.21 277 / 0.15)', 'oklch(0.58 0.21 277 / 0.2)'],
      ['stat-success-bg', 'oklch(0.7 0.17 162 / 0.15)', 'oklch(0.7 0.17 162 / 0.2)'],
      ['stat-warning-bg', 'oklch(0.77 0.16 70 / 0.18)', 'oklch(0.77 0.16 70 / 0.2)'],
      ['stat-error-bg', 'oklch(0.63 0.24 13 / 0.15)', 'oklch(0.63 0.24 13 / 0.2)'],
      ['stat-accent-bg', 'oklch(0.72 0.14 200 / 0.15)', 'oklch(0.72 0.14 200 / 0.2)'],
      ['soft-brand', 'var(--color-brand-50)', 'oklch(0.58 0.21 277 / 0.15)'],
      ['soft-success', 'oklch(0.7 0.17 162 / 0.12)', 'oklch(0.7 0.17 162 / 0.15)'],
      ['soft-warning', 'oklch(0.77 0.16 70 / 0.15)', 'oklch(0.77 0.16 70 / 0.15)'],
      ['soft-error', 'oklch(0.63 0.24 13 / 0.12)', 'oklch(0.63 0.24 13 / 0.15)'],
      ['primary-action', 'var(--color-brand-600)', 'var(--color-brand-500)'],
      ['primary-action-hover', 'var(--color-brand-500)', 'var(--color-brand-400)'],
      ['switch-on', 'var(--color-brand-600)', 'var(--color-brand-500)'],
      ['switch-off', 'var(--color-slate-300)', 'var(--color-slate-600)'],
      ['tooltip', 'var(--color-slate-950)', 'var(--color-slate-700)'],
      ['tooltip-text', 'var(--color-slate-50)', 'var(--color-slate-100)'],
      ['control-error-border', 'var(--color-error-500)', 'var(--color-error-400)'],
      ['current-credential-bg', 'oklch(0.7 0.17 162 / 0.08)', 'oklch(0.7 0.17 162 / 0.1)'],
    ];
    for (const [name, light, dark] of endpoints) {
      expectOklabMix(rootRule, name, light, dark);
    }
    expect(rootRule).toContain('--current-credential-text: color-mix(');
    expect(rootRule).toContain('color-mix(in oklch, var(--color-success-600) 72%, #64748b)');
    expect(rootRule).toContain(
      'color-mix(in oklch, var(--color-success-400) 70%, #94a3b8) var(--theme-dark-weight)',
    );
  });

  it('组件和全局样式不再通过 dark 颜色分支绕过主题进度', () => {
    expect(vueSource(resolve(process.cwd(), 'src'))).not.toContain('dark:');
    expect(stylesCss).not.toContain('@custom-variant dark');
    expect(stylesCss).not.toMatch(/(?:^|\n)\.dark\s+[^{]+{/);
  });

  it('分段选择器继续使用固定语义变量，避免主题切换时改用另一颜色来源', () => {
    expect(stylesCss).toContain('--color-segment-active: var(--segment-active)');
  });

  it('主题与图标切换时序保持原有的 520ms 和 143ms', () => {
    expect(THEME_TRANSITION_MS).toBe(520);
    expect(THEME_ICON_SWAP_DELAY_MS).toBe(143);
    expect(stylesCss).toContain('--duration-theme: 520ms');
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

  it('输入控件始终保留快速局部过渡', () => {
    const standaloneControlRule = stylesCss.match(/(?:^|\n)\.c-control-focus\s*\{([^}]*)}/);
    const controlTransition = cssRuleBody('.c-control-focus');

    expect(standaloneControlRule?.[1] ?? '').toContain('transition-duration: var(--duration-fast)');
    expect(controlTransition).toContain('transition-duration: var(--duration-fast)');
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
    expect(checkboxRule).toContain('box-shadow: 0 0 0 3px var(--focus-ring)');
  });

  it('错误输入聚焦时保留错误色边框与焦点环', () => {
    const errorFocusRule = cssRuleBody(".c-control-focus[aria-invalid='true']:focus");

    expect(errorFocusRule).toContain('border-color: var(--control-error-border)');
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

    expect(rootRule).toContain('--rail: color-mix');
    expectOklabMix(rootRule, 'rail', '#ffffff', '#080b14');
    expectOklabMix(rootRule, 'rail-text', '#334155', '#cbd5e1');
    expectOklabMix(rootRule, 'rail-text-strong', '#0f172a', '#ffffff');
    expectOklabMix(rootRule, 'rail-muted', '#64748b', '#94a3b8');
    expectOklabMix(rootRule, 'rail-border', '#e2e8f0', '#141720');
    expect(rootRule).not.toMatch(/--rail-border:[^;]*\/\s*0\.05/);
    expectOklabMix(rootRule, 'rail-hover', 'oklch(0.58 0.21 277 / 0.07)', 'oklch(1 0 0 / 0.05)');
    expectOklabMix(
      rootRule,
      'rail-active',
      'oklch(0.58 0.21 277 / 0.12)',
      'oklch(0.58 0.21 277 / 0.15)',
    );
    expectOklabMix(rootRule, 'rail-active-text', 'var(--color-brand-700)', '#ffffff');
  });
});
