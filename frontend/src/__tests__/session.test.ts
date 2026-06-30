import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useSessionStore } from '../stores/session';

vi.mock('../api/admin', () => ({
  authApi: {
    session: vi.fn<typeof authApi.session>(),
    login: vi.fn<typeof authApi.login>(),
    logout: vi.fn<typeof authApi.logout>(),
  },
}));

import { authApi } from '../api/admin';

describe('session store - restore 超时', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('restore 超过 10 秒时当作未认证处理', async () => {
    vi.useFakeTimers();
    vi.mocked(authApi.session).mockReturnValue(new Promise(() => {}));

    const store = useSessionStore();
    const restorePromise = store.restore();

    vi.advanceTimersByTime(9999);
    expect(store.ready).toBe(false);

    vi.advanceTimersByTime(1);
    await restorePromise;

    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
  });

  it('restore 正常返回时设置认证状态', async () => {
    vi.mocked(authApi.session).mockResolvedValue({
      authenticated: true,
      username: 'admin',
      source: 'session_cookie',
    });

    const store = useSessionStore();
    await store.restore();

    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(true);
    expect(store.username).toBe('admin');
    expect(store.source).toBe('session_cookie');
  });

  it('restore 缺少 source 时使用空字符串', async () => {
    vi.mocked(authApi.session).mockResolvedValue({
      authenticated: true,
      username: 'admin',
    });

    const store = useSessionStore();
    await store.restore();

    expect(store.source).toBe('');
  });

  it('restore 失败时当作未认证', async () => {
    vi.mocked(authApi.session).mockRejectedValue(new Error('network'));

    const store = useSessionStore();
    await store.restore();

    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.source).toBe('');
  });

  it('login 更新认证状态并为 source 提供默认值', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      authenticated: true,
      username: 'alice',
    });

    const store = useSessionStore();
    await store.login('alice', 'secret');

    expect(authApi.login).toHaveBeenCalledWith('alice', 'secret');
    expect(store.authenticated).toBe(true);
    expect(store.username).toBe('alice');
    expect(store.source).toBe('session_cookie');
  });

  it('login 保留服务端 source', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      authenticated: true,
      username: 'alice',
      source: 'custom',
    });

    const store = useSessionStore();
    await store.login('alice', 'secret');

    expect(store.source).toBe('custom');
  });

  it('logout 成功或失败都会清空本地状态', async () => {
    const store = useSessionStore();
    store.authenticated = true;
    store.username = 'alice';
    store.source = 'session_cookie';
    vi.mocked(authApi.logout).mockResolvedValue({ authenticated: false });

    await store.logout();
    expect(store.$state).toEqual({
      authenticated: false,
      username: '',
      source: '',
      ready: false,
    });

    store.authenticated = true;
    store.username = 'alice';
    vi.mocked(authApi.logout).mockRejectedValue(new Error('network'));
    await expect(store.logout()).rejects.toThrow('network');
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.source).toBe('');
  });
});
