import { describe, expect, it, vi } from 'vitest';

const BUSINESS_ROUTE_NAMES = [
  'dashboard',
  'stats',
  'credentials',
  'api-keys',
  'console',
  'usage-docs',
  'settings',
  'not-found',
] as const;

describe('路由懒加载', () => {
  it('所有页面组件都通过加载函数按需获取', async () => {
    vi.resetModules();
    const { default: freshRouter } = await import('../router');
    const routesByName = new Map(freshRouter.getRoutes().map((route) => [route.name, route]));

    for (const routeName of BUSINESS_ROUTE_NAMES) {
      const loadComponent = routesByName.get(routeName)?.components?.default;
      expect(loadComponent).toEqual(expect.any(Function));
      await (loadComponent as () => Promise<unknown>)();
    }
  });
});
