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
      expect.arrayContaining(['dashboard', 'credentials', 'api-keys', 'console', 'settings']),
    );

    await router.push('/');
    expect(router.currentRoute.value.path).toBe('/dashboard');
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
});
