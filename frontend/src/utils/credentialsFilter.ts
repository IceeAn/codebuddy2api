import type { CredentialRecord } from '../types';

export type CredentialFilterTab = 'all' | 'valid' | 'expired';

/**
 * 返回新数组，不修改原列表；排序由调用方负责。
 */
export function filterCredentials(
  list: CredentialRecord[],
  tab: CredentialFilterTab,
): CredentialRecord[] {
  if (tab === 'all') return [...list];
  if (tab === 'valid') return list.filter((item) => !item.is_expired);
  return list.filter((item) => item.is_expired);
}
