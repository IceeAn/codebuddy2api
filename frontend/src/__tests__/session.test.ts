import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useSessionStore } from '../stores/session';
import { ApiError } from '../api/client';

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

  it('restore 超过 10 秒时中止底层请求并暴露可重试错误', async () => {
    vi.useFakeTimers();
    let receivedSignal: AbortSignal | undefined;
    vi.mocked(authApi.session).mockImplementation((signal?: AbortSignal) => {
      receivedSignal = signal;
      return new Promise((_, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    });

    const store = useSessionStore();
    const restorePromise = store.restore();

    vi.advanceTimersByTime(9999);
    expect(store.ready).toBe(false);

    vi.advanceTimersByTime(1);
    await restorePromise;

    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.restoreError).toBe('登录状态确认超时，请重试');
    expect(receivedSignal?.aborted).toBe(true);
    expect(store.restoring).toBe(false);
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
    expect(store.restoreError).toBe('');
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

  it('restore 断网或 5xx 时显示无法确认状态，而不是登录页', async () => {
    vi.mocked(authApi.session).mockRejectedValue(new Error('network'));

    const store = useSessionStore();
    await store.restore();

    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.source).toBe('');
    expect(store.restoreError).toBe('无法确认登录状态，请检查网络后重试');

    vi.mocked(authApi.session).mockRejectedValue(
      new ApiError(500, 'server error', undefined, false),
    );
    await store.restore();
    expect(store.restoreError).toBe('无法确认登录状态，请稍后重试');
  });

  it('restore 重试期间重新进入未就绪状态', async () => {
    const store = useSessionStore();
    vi.mocked(authApi.session).mockRejectedValueOnce(new Error('network'));
    await store.restore();
    expect(store.ready).toBe(true);
    expect(store.restoreError).not.toBe('');

    let resolveSession: ((value: Awaited<ReturnType<typeof authApi.session>>) => void) | undefined;
    vi.mocked(authApi.session).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
    );
    const retry = store.restore();

    expect(store.ready).toBe(false);
    expect(store.restoring).toBe(true);
    expect(store.restoreError).toBe('');

    resolveSession?.({ authenticated: true, username: 'admin' });
    await retry;
    expect(store.ready).toBe(true);
    expect(store.authenticated).toBe(true);
  });

  it('只有带认证边界标记的 401 才进入登录页', async () => {
    const store = useSessionStore();
    vi.mocked(authApi.session).mockRejectedValue(
      new ApiError(401, 'unauthorized', undefined, true),
    );
    await store.restore();
    expect(store.authenticated).toBe(false);
    expect(store.restoreError).toBe('');

    vi.mocked(authApi.session).mockRejectedValue(
      new ApiError(401, 'uncontrolled', undefined, false),
    );
    await store.restore();
    expect(store.restoreError).toBe('无法确认登录状态，请稍后重试');
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
      restoreError: '',
      restoring: false,
    });

    store.authenticated = true;
    store.username = 'alice';
    vi.mocked(authApi.logout).mockRejectedValue(new Error('network'));
    await expect(store.logout()).rejects.toThrow('network');
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.source).toBe('');
  });

  it('endLocalSession 不发网络请求并立即清空本地状态', () => {
    const store = useSessionStore();
    store.authenticated = true;
    store.username = 'alice';
    store.source = 'session_cookie';

    store.endLocalSession();

    expect(authApi.logout).not.toHaveBeenCalled();
    expect(store.authenticated).toBe(false);
    expect(store.username).toBe('');
    expect(store.source).toBe('');
    expect(store.restoreError).toBe('');
  });

  it('restore 进行中时忽略重复调用', async () => {
    let resolveSession: ((value: Awaited<ReturnType<typeof authApi.session>>) => void) | undefined;
    vi.mocked(authApi.session).mockReturnValue(
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
    );
    const store = useSessionStore();
    const first = store.restore();
    const second = store.restore();
    expect(authApi.session).toHaveBeenCalledOnce();

    resolveSession?.({ authenticated: true, username: 'admin' });
    await Promise.all([first, second]);
    expect(store.authenticated).toBe(true);
  });
});
