import { describe, expect, it } from 'vitest';
import {
  computeValidityPercent,
  describeCredentialStatus,
  describeServiceStatus,
} from '../utils/dashboardStatus';

/**
 * 接口加载失败时展示"加载失败"，避免把网络/鉴权错误误报为服务异常。
 */
describe('describeServiceStatus', () => {
  it('healthy + 无错误 时返回"运行中"', () => {
    expect(describeServiceStatus('healthy', false)).toBe('运行中');
  });

  it('healthy + 有错误 时返回"加载失败"', () => {
    expect(describeServiceStatus('healthy', true)).toBe('加载失败');
  });

  it('非 healthy + 无错误 时返回"异常"', () => {
    expect(describeServiceStatus('degraded', false)).toBe('异常');
    expect(describeServiceStatus(undefined, false)).toBe('异常');
  });

  it('非 healthy + 有错误 时返回"加载失败"', () => {
    expect(describeServiceStatus('degraded', true)).toBe('加载失败');
    expect(describeServiceStatus(undefined, true)).toBe('加载失败');
  });
});

describe('computeValidityPercent', () => {
  it('total 为 0 时返回 0（避免除零）', () => {
    expect(computeValidityPercent(0, 0)).toBe(0);
    expect(computeValidityPercent(5, 0)).toBe(0);
  });

  it('valid 等于 total 时返回 100', () => {
    expect(computeValidityPercent(10, 10)).toBe(100);
  });

  it('部分有效时返回向下取整的百分比', () => {
    expect(computeValidityPercent(3, 4)).toBe(75);
    expect(computeValidityPercent(1, 3)).toBe(33);
  });

  it('valid 超过 total 时钳制为 100', () => {
    expect(computeValidityPercent(11, 10)).toBe(100);
  });

  it('valid 为负数时钳制为 0', () => {
    expect(computeValidityPercent(-1, 10)).toBe(0);
  });
});

describe('describeCredentialStatus', () => {
  it.each([
    ['auto_rotation', '自动轮换已启用'],
    ['auto_rotation_disabled', '自动轮换已关闭'],
    ['no_credentials', '暂无凭证'],
    [undefined, '未加载'],
  ] as const)('将凭证状态 %s 显示为中文', (status, expected) => {
    expect(describeCredentialStatus(status)).toBe(expected);
  });
});
