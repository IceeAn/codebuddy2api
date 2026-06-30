import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const stylesCss = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf-8');

function cssRuleBody(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesCss.match(new RegExp(`(?:^|\\n)${escapedSelector}\\s*\\{([^}]*)\\}`));
  if (!match) {
    throw new Error(`找不到 CSS 规则：${selector}`);
  }
  return match[1];
}

describe('toast 动画样式', () => {
  it('toast 侧条使用跟随卡片圆角的 inset shadow，而不是独立矩形色块', () => {
    const itemRule = cssRuleBody('.toast-item');
    const successRule = cssRuleBody('.toast-success');
    const warningRule = cssRuleBody('.toast-warning');

    expect(itemRule).toContain('inset 3px 0 0 0 var(--toast-accent)');
    expect(itemRule).toContain('var(--shadow-toast)');
    expect(successRule).toContain('--toast-accent');
    expect(warningRule).toContain('--toast-accent');
  });

  it('进出场动画包含透明度、位移和缩放，不使用模糊', () => {
    const activeRule = stylesCss.match(
      /\.toast-enter-active,\s*\.toast-leave-active\s*\{([^}]*)\}/,
    )?.[1];
    const fromRule = cssRuleBody('.toast-enter-from');
    const leaveRule = cssRuleBody('.toast-leave-to');

    expect(activeRule).toBeDefined();
    expect(activeRule).toContain('opacity');
    expect(activeRule).toContain('transform');
    expect(activeRule).not.toContain('filter');
    expect(fromRule).toContain('translate3d');
    expect(fromRule).toContain('scale(0.96)');
    expect(fromRule).not.toContain('blur');
    expect(leaveRule).toContain('translate3d');
    expect(leaveRule).toContain('scale(0.98)');
    expect(leaveRule).not.toContain('blur');
  });

  it('列表重排和进度条动画存在', () => {
    expect(cssRuleBody('.toast-move')).toContain('transform');
    expect(stylesCss).toContain('@keyframes toast-progress');
    expect(stylesCss).toContain('scaleX(0)');
  });

  it('toast 暂停状态会冻结所有进度条动画', () => {
    const pausedRule = cssRuleBody('.toast-paused .toast-progress-bar');

    expect(pausedRule).toContain('animation-play-state: paused');
  });
});
