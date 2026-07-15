import { beforeEach, describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import router from '../router';
import { useSessionStore } from '../stores/session';

describe('router', () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    const session = useSessionStore();
    session.ready = true;
    await router.replace('/dashboard');
  });

  it('注册所有管理台路由并处理根路径重定向', async () => {
    expect(
      router
        .getRoutes()
        .map((route) => route.name)
        .filter(Boolean),
    ).toEqual(
      expect.arrayContaining([
        'dashboard',
        'stats',
        'credentials',
        'api-keys',
        'console',
        'api-docs',
        'settings',
        'not-found',
      ]),
    );

    await router.push('/');
    expect(router.currentRoute.value.path).toBe('/dashboard');
    expect(document.title).toBe('总览 · CodeBuddy2API');
  });

  it('restore 未完成时不取消导航，避免首次路由保持空白', async () => {
    const session = useSessionStore();
    session.ready = false;

    await router.push('/settings');
    expect(router.currentRoute.value.name).toBe('settings');

    session.ready = true;
    await router.push('/api-keys');
    expect(router.currentRoute.value.name).toBe('api-keys');
  });

  it('未知 hash 路由显示 404 页面并更新浏览器标题', async () => {
    await router.push('/missing/path');
    expect(router.currentRoute.value.name).toBe('not-found');
    expect(document.title).toBe('页面不存在 · CodeBuddy2API');

    await router.push('/settings');
    expect(document.title).toBe('设置 · CodeBuddy2API');
  });

  it('没有标题元数据的临时路由使用管理台标题', async () => {
    const removeRoute = router.addRoute({
      path: '/untitled-test',
      name: 'untitled-test',
      component: { template: '<div />' },
    });
    await router.push('/untitled-test');
    expect(document.title).toBe('管理台 · CodeBuddy2API');
    removeRoute();
  });

  it('导航被守卫取消时保留当前页面标题', async () => {
    const removeGuard = router.beforeEach((to) => (to.name === 'settings' ? false : undefined));

    try {
      const failure = await router.push('/settings');

      expect(failure).toBeDefined();
      expect(router.currentRoute.value.name).toBe('dashboard');
      expect(document.title).toBe('总览 · CodeBuddy2API');
    } finally {
      removeGuard();
    }
  });
});
