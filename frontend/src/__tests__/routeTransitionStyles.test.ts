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

describe('页面切换样式', () => {
  it('页面离场态不拉伸页面根节点高度', () => {
    const pageLeaveActiveRule = cssRuleBody('.page-leave-active');

    expect(pageLeaveActiveRule).toContain('position: absolute');
    expect(pageLeaveActiveRule).not.toContain('inset: 0');
    expect(pageLeaveActiveRule).not.toContain('bottom: 0');
  });
});

describe('表格加载遮罩过渡样式', () => {
  it('进入和离开都只过渡透明度', () => {
    const enterActiveRule = cssRuleBody('.c-data-table-loading-enter-active');
    const leaveActiveRule = cssRuleBody('.c-data-table-loading-leave-active');

    expect(enterActiveRule).toContain(
      'transition: opacity var(--duration-fast) var(--ease-out-quad)',
    );
    expect(leaveActiveRule).toContain(
      'transition: opacity var(--duration-fast) var(--ease-out-quad)',
    );
    expect(leaveActiveRule).toContain('pointer-events: none');
  });

  it('进入起点和离开终点透明度为零', () => {
    expect(cssRuleBody('.c-data-table-loading-enter-from')).toContain('opacity: 0');
    expect(cssRuleBody('.c-data-table-loading-leave-to')).toContain('opacity: 0');
  });
});

describe('表格加载指示器定位样式', () => {
  it('长列表相对视口居中，短列表改为容器内居中', () => {
    const loadingRule = cssRuleBody('.c-data-table-loading');
    const indicatorRule = cssRuleBody('.c-data-table-loading-indicator');
    const shortListRule = stylesCss.match(
      /@container\s*\(max-height:\s*50vh\)\s*\{\s*\.c-data-table-loading-indicator\s*\{([^}]*)}/,
    )?.[1];

    expect(loadingRule).toContain('container-type: size');
    expect(indicatorRule).toContain('position: sticky');
    expect(indicatorRule).toContain('top: calc(50vh - 0.875rem)');
    expect(shortListRule).toContain('position: absolute');
    expect(shortListRule).toContain('top: 50%');
    expect(shortListRule).toContain('transform: translateY(-50%)');
  });
});
