import type { CredentialStatus } from '../types/admin';

/**
 * 查询失败时显示“加载失败”，避免和后端返回的非 healthy 状态混淆。
 */
export function describeServiceStatus(status: string | undefined, isError: boolean): string {
  if (isError) return '加载失败';
  if (status === 'healthy') return '运行中';
  return '异常';
}

export function computeValidityPercent(valid: number, total: number): number {
  if (total <= 0) return 0;
  if (valid < 0) return 0;
  if (valid >= total) return 100;
  return Math.floor((valid / total) * 100);
}

/** 将后端稳定的凭证状态枚举转换为管理台文案。 */
export function describeCredentialStatus(status: CredentialStatus | undefined): string {
  switch (status) {
    case 'auto_rotation':
      return '自动轮换已启用';
    case 'auto_rotation_disabled':
      return '自动轮换已关闭';
    case 'no_credentials':
      return '暂无凭证';
    case undefined:
      return '未加载';
  }
}
