import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { toastMock, beforeUnmountCallbacks } = vi.hoisted(() => ({
  toastMock: {
    success: vi.fn<(message: string, duration?: number) => void>(),
    error: vi.fn<(message: string, duration?: number) => void>(),
    warning: vi.fn<(message: string, duration?: number) => void>(),
    info: vi.fn<(message: string, duration?: number) => void>(),
  },
  beforeUnmountCallbacks: [] as Array<() => void>,
}));

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue')>();
  return {
    ...actual,
    onBeforeUnmount: (callback: () => void) => beforeUnmountCallbacks.push(callback),
  };
});

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

import { ApiError } from '../api/client';
import { codebuddyOAuthApi } from '../api/admin';
import { useOAuthPolling } from '../composables/useOAuthPolling';

/**
 * 由于 onBeforeUnmount 在组件 setup 之外调用会打印警告但不报错，
 * 测试中通过触发 reset() 显式停止轮询，避免污染后续用例。
 */
describe('useOAuthPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    toastMock.info.mockReset();
    beforeUnmountCallbacks.length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('start() 成功后开始轮询，pollAuth 返回 access_token 时调用 onSuccess', async () => {
    const startAuth = vi
      .spyOn(codebuddyOAuthApi, 'startAuth')
      .mockResolvedValue({ verification_uri_complete: 'https://cb/auth', auth_state: 'state-1' });
    const pollAuth = vi
      .spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockResolvedValue({ access_token: 'tok', saved: true });
    const onSuccess = vi.fn<() => void>();

    const oauth = useOAuthPolling({ pollIntervalMs: 5000, onSuccess });
    await oauth.start();

    expect(startAuth).toHaveBeenCalledTimes(1);
    expect(pollAuth).toHaveBeenCalledWith('state-1', expect.any(AbortSignal));
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(oauth.polling.value).toBe(false);
    expect(toastMock.success).toHaveBeenCalledWith('认证成功');
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
    oauth.reset();
  });

  it('startAuth 缺少字段时显示错误且不开始轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({ message: '上游不可用' });
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(toastMock.error).toHaveBeenCalledWith('上游不可用');
    expect(oauth.polling.value).toBe(false);
    expect(pollAuth).not.toHaveBeenCalled();
  });

  it('startAuth 缺少字段且无 message 时使用默认错误', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(toastMock.error).toHaveBeenCalledWith('认证启动失败');
  });

  it('startAuth 的 AbortError 不显示错误', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockRejectedValue(
      Object.assign(new Error('aborted'), { name: 'AbortError' }),
    );

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('stop() 取消未完成的 startAuth 且忽略迟到响应', async () => {
    let capturedSignal: AbortSignal | undefined;
    let resolveStart: (value: {
      verification_uri_complete?: string;
      auth_state?: string;
    }) => void = () => {};
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockImplementation(
      (signal?: AbortSignal) =>
        new Promise((resolve) => {
          capturedSignal = signal;
          resolveStart = resolve;
        }),
    );
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    await Promise.resolve();

    oauth.stop();
    expect(capturedSignal?.aborted).toBe(true);

    resolveStart({ verification_uri_complete: 'https://cb/auth', auth_state: 'late-state' });
    await startPromise;

    expect(oauth.polling.value).toBe(false);
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
    expect(pollAuth).not.toHaveBeenCalled();
  });

  it('startAuth 的普通异常按类型显示错误', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth')
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce('bad');

    const oauth = useOAuthPolling();
    await oauth.start();
    expect(toastMock.error).toHaveBeenLastCalledWith('network');

    await oauth.start();
    expect(toastMock.error).toHaveBeenLastCalledWith('认证启动失败');
  });

  it('pollAuth 抛 ApiError authorization_pending 时继续轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-2',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(pending);

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    // 让立即执行的那次 poll 完成
    await vi.advanceTimersByTimeAsync(0);

    expect(oauth.polling.value).toBe(true);
    expect(pollAuth).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(pollAuth).toHaveBeenCalledTimes(2);
    expect(oauth.polling.value).toBe(true);
    expect(toastMock.error).not.toHaveBeenCalled();

    oauth.reset();
    await startPromise;
  });

  it('pollAuth 未返回 token 时保持轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-no-token',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(oauth.polling.value).toBe(true);
    oauth.reset();
  });

  it('stop() 后忽略未完成 pollAuth 的迟到成功响应', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-late-poll',
    });
    let resolvePoll: (value: { access_token: string; saved: boolean }) => void = () => {};
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePoll = resolve;
        }),
    );
    const onSuccess = vi.fn<() => void>();

    const oauth = useOAuthPolling({ pollIntervalMs: 5000, onSuccess });
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(oauth.polling.value).toBe(true);

    oauth.stop();
    resolvePoll({ access_token: 'late-token', saved: true });
    await startPromise;

    expect(oauth.polling.value).toBe(false);
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('已停止轮询时遗留的 poll 回调直接返回', async () => {
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-stopped',
    });
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    await oauth.start();
    const pollCallback = timeoutSpy.mock.calls.find((call) => call[1] === 5000)?.[0] as
      | (() => void)
      | undefined;
    expect(pollCallback).toBeTypeOf('function');
    const callsAfterImmediate = pollAuth.mock.calls.length;

    oauth.stop();
    pollCallback?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(pollAuth.mock.calls.length).toBe(callsAfterImmediate);
    oauth.reset();
    pollCallback?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(pollAuth.mock.calls.length).toBe(callsAfterImmediate);
  });

  it('pollAuth 抛 ApiError slow_down 时继续轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-3',
    });
    const slow = new ApiError(400, 'slow', { error: 'slow_down' });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(slow);

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(oauth.polling.value).toBe(true);

    await vi.advanceTimersByTimeAsync(5000);
    expect(oauth.polling.value).toBe(true);

    oauth.reset();
    await startPromise;
  });

  it('pollAuth 抛不可恢复 ApiError 时停止并提示', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-4',
    });
    const denied = new ApiError(400, 'denied', {
      error: 'access_denied',
      error_description: '用户拒绝授权',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(denied);

    const oauth = useOAuthPolling();
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(oauth.polling.value).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith('用户拒绝授权');
    oauth.reset();
    await startPromise;
  });

  it('不可恢复 ApiError 缺少 description 时使用异常消息', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-error',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'poll failed', { error: 'access_denied' }),
    );

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(toastMock.error).toHaveBeenCalledWith('poll failed');
  });

  it('pollAuth 的普通异常停止并按类型提示', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-normal-error',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce('bad');

    const oauth = useOAuthPolling();
    await oauth.start();
    expect(toastMock.error).toHaveBeenLastCalledWith('network');

    await oauth.start();
    expect(toastMock.error).toHaveBeenLastCalledWith('认证失败');
  });

  it('达到 maxAttempts 时停止并提示超时', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-5',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(pending);

    const oauth = useOAuthPolling({ pollIntervalMs: 1000, maxAttempts: 2 });
    const startPromise = oauth.start();
    // 立即执行的那次算 attempt 1
    await vi.advanceTimersByTimeAsync(0);
    expect(oauth.polling.value).toBe(true);

    // 推进周期触发 attempt 2，仍 pending
    await vi.advanceTimersByTimeAsync(1000);
    expect(oauth.polling.value).toBe(true);

    // 再推进触发 attempt 3 > maxAttempts，应超时停止
    await vi.advanceTimersByTimeAsync(1000);
    expect(oauth.polling.value).toBe(false);
    expect(toastMock.warning).toHaveBeenCalledWith('授权等待超时，请重试');
    oauth.reset();
    await startPromise;
  });

  it('stop() 清理所有 timer 且不再继续轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-6',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(pending);

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);
    const callsAfterImmediate = pollAuth.mock.calls.length;

    oauth.stop();

    await vi.advanceTimersByTimeAsync(20000);
    expect(pollAuth.mock.calls.length).toBe(callsAfterImmediate);
    expect(oauth.polling.value).toBe(false);
    expect(oauth.starting.value).toBe(false);
    oauth.reset();
    await startPromise;
  });

  it('组件卸载回调停止轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-unmount',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    await oauth.start();
    expect(oauth.polling.value).toBe(true);
    expect(beforeUnmountCallbacks).toHaveLength(1);

    beforeUnmountCallbacks[0]();
    expect(oauth.polling.value).toBe(false);
  });

  it('防竞态：starting 中再次调用 start() 直接返回', async () => {
    let resolveStart: (value: {
      verification_uri_complete?: string;
      auth_state?: string;
    }) => void = () => {};
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve;
        }),
    );
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    const first = oauth.start();
    expect(oauth.starting.value).toBe(true);

    const second = oauth.start();
    await Promise.resolve();
    expect(oauth.starting.value).toBe(true);

    resolveStart({ verification_uri_complete: 'https://cb/auth', auth_state: 's' });
    await first;
    await second;

    expect(codebuddyOAuthApi.startAuth).toHaveBeenCalledTimes(1);
    oauth.reset();
    pollAuth.mockClear();
  });

  it('防竞态：polling 中再次调用 start() 直接返回', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-7',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(pending);

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const first = oauth.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(oauth.polling.value).toBe(true);

    await oauth.start();
    expect(codebuddyOAuthApi.startAuth).toHaveBeenCalledTimes(1);

    oauth.reset();
    await first;
  });

  it('异步轮询串行执行，前一次未完成时不启动下一次请求', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'serial-state',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    const rejectPolls: Array<(error: unknown) => void> = [];
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectPolls.push(reject);
        }),
    );

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(pollAuth).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    const callsWhileFirstPending = pollAuth.mock.calls.length;

    rejectPolls[0](pending);
    await startPromise;
    await vi.advanceTimersByTimeAsync(5000);
    const callsAfterFirstCompletes = pollAuth.mock.calls.length;

    oauth.reset();
    rejectPolls.slice(1).forEach((reject) => reject(pending));
    await vi.advanceTimersByTimeAsync(0);

    expect(callsWhileFirstPending).toBe(1);
    expect(callsAfterFirstCompletes).toBe(2);
  });

  it('reset() 清理状态与计时', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-8',
    });
    const pending = new ApiError(400, 'pending', { error: 'authorization_pending' });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(pending);

    const oauth = useOAuthPolling({ pollIntervalMs: 5000 });
    const startPromise = oauth.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(oauth.elapsedSeconds.value).toBe(0);

    await vi.advanceTimersByTimeAsync(3000);
    expect(oauth.elapsedSeconds.value).toBe(3);

    oauth.reset();
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
    expect(oauth.elapsedSeconds.value).toBe(0);
    expect(oauth.polling.value).toBe(false);
    await startPromise;
  });
});
