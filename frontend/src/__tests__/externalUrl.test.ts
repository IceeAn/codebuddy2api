import { describe, expect, it } from 'vitest';
import { normalizeExternalHttpUrl } from '../utils/externalUrl';

describe('normalizeExternalHttpUrl', () => {
  it('规范化允许的绝对 HTTP 和 HTTPS URL', () => {
    expect(normalizeExternalHttpUrl('https://EXAMPLE.com/auth?state=1')).toBe(
      'https://example.com/auth?state=1',
    );
    expect(normalizeExternalHttpUrl('http://127.0.0.1:8080/auth')).toBe(
      'http://127.0.0.1:8080/auth',
    );
  });

  it.each([
    null,
    '',
    ' https://example.com/auth',
    'not a url',
    'ftp://example.com/auth',
    'https://user:password@example.com/auth',
    'https://example.com/\nunsafe',
  ])('拒绝非白名单认证 URL：%s', (value) => {
    expect(normalizeExternalHttpUrl(value)).toBeNull();
  });
});
