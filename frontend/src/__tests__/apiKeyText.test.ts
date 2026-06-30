import { describe, expect, it } from 'vitest';
import { formatDeleteConfirm } from '../utils/apiKeyText';

describe('formatDeleteConfirm', () => {
  it('包含 Key 名称与失效提示', () => {
    const text = formatDeleteConfirm('my-bot-key');
    expect(text).toContain('my-bot-key');
    expect(text).toContain('将立即失效');
  });

  it('名称为空时仍给出有效提示', () => {
    const text = formatDeleteConfirm('');
    expect(text).toContain('API Key');
    expect(text).toContain('将立即失效');
  });
});
