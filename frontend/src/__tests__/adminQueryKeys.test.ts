import { describe, expect, it } from 'vitest';
import { adminQueryKeys } from '../utils/adminQueryKeys';

describe('adminQueryKeys', () => {
  it('所有用户数据查询键都以用户名隔离', () => {
    const alice = adminQueryKeys('alice');
    const bob = adminQueryKeys('bob');

    const aliceKeys = [
      alice.status,
      alice.credentials,
      alice.apiKeys,
      alice.settings,
      alice.playgroundModels,
      alice.statsOverview('today'),
      alice.statsRequests({ cursor: null }),
      alice.statsDimension('models', { search: '' }),
      alice.statsRequest(42),
    ];

    for (const key of aliceKeys) {
      expect(key.slice(0, 2)).toEqual(['admin', 'alice']);
      expect(key).not.toEqual(expect.arrayContaining(['bob']));
    }
    expect(bob.credentials).not.toEqual(alice.credentials);
    expect(bob.playgroundModels).not.toEqual(alice.playgroundModels);
  });
});
