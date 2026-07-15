/** 为所有按系统用户隔离的管理台数据生成统一查询键。 */
export function adminQueryKeys(username: string) {
  const root = ['admin', username] as const;

  return {
    all: root,
    status: [...root, 'status'] as const,
    credentials: [...root, 'credentials'] as const,
    apiKeys: [...root, 'api-keys'] as const,
    settings: [...root, 'settings'] as const,
    playgroundModels: [...root, 'playground', 'models'] as const,
    statsOverview: (params: unknown) => [...root, 'stats', 'overview', params] as const,
    statsRequests: (params: unknown) => [...root, 'stats', 'requests', params] as const,
    statsDimension: (dimension: string, params: unknown) =>
      [...root, 'stats', 'dimensions', dimension, params] as const,
    statsRequest: (requestId: number) => [...root, 'stats', 'requests', requestId] as const,
  };
}
