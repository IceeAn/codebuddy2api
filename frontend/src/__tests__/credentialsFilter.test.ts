import { describe, expect, it } from 'vitest';
import { filterCredentials } from '../utils/credentialsFilter';
import type { CredentialRecord } from '../types';

/**
 * 构造仅包含筛选所需字段的 CredentialRecord 测试桩。
 * 其余字段对筛选逻辑无影响，用部分字段 + as 简化测试构造。
 */
function makeCredential(id: string, isExpired: boolean): CredentialRecord {
  return {
    credential_id: id,
    filename: '',
    user_id: '',
    time_remaining_str: '',
    is_expired: isExpired,
    token_type: '',
    has_refresh_token: false,
    has_token: true,
    token_display: '',
  } as CredentialRecord;
}

describe('filterCredentials', () => {
  it('tab=all 返回全部凭证（保持原顺序）', () => {
    const list = [
      makeCredential('a', false),
      makeCredential('b', true),
      makeCredential('c', false),
    ];
    expect(filterCredentials(list, 'all')).toEqual(list);
  });

  it('tab=valid 仅返回未过期凭证', () => {
    const a = makeCredential('a', false);
    const b = makeCredential('b', true);
    const c = makeCredential('c', false);
    expect(filterCredentials([a, b, c], 'valid')).toEqual([a, c]);
  });

  it('tab=expired 仅返回已过期凭证', () => {
    const a = makeCredential('a', false);
    const b = makeCredential('b', true);
    const c = makeCredential('c', true);
    expect(filterCredentials([a, b, c], 'expired')).toEqual([b, c]);
  });

  it('空列表任意 tab 均返回空数组', () => {
    expect(filterCredentials([], 'all')).toEqual([]);
    expect(filterCredentials([], 'valid')).toEqual([]);
    expect(filterCredentials([], 'expired')).toEqual([]);
  });

  it('valid tab 下无可用凭证时返回空数组', () => {
    const list = [makeCredential('a', true), makeCredential('b', true)];
    expect(filterCredentials(list, 'valid')).toEqual([]);
  });

  it('expired tab 下无过期凭证时返回空数组', () => {
    const list = [makeCredential('a', false), makeCredential('b', false)];
    expect(filterCredentials(list, 'expired')).toEqual([]);
  });

  it('不修改原数组（返回新数组）', () => {
    const list = [makeCredential('a', false), makeCredential('b', true)];
    const snapshot = [...list];
    const result = filterCredentials(list, 'valid');
    expect(result).not.toBe(list);
    expect(list).toEqual(snapshot);
  });
});
