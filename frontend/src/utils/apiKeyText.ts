export function formatDeleteConfirm(keyName: string): string {
  const display = keyName || 'API Key';
  return `确定删除 API Key "${display}"？使用该 Key 的客户端将立即失效`;
}
