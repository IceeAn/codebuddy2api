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

describe('按钮光标基础样式', () => {
  it('启用按钮统一显示 pointer', () => {
    expect(cssRuleBody('button:not(:disabled)')).toContain('cursor: pointer');
  });

  it('禁用按钮统一显示 not-allowed', () => {
    expect(cssRuleBody('button:disabled')).toContain('cursor: not-allowed');
  });
});
