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
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.spyOn(codebuddyOAuthApi, 'cancelAuth').mockResolvedValue({ cancelled: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('start() 成功后开始轮询，pollAuth 严格返回 saved=true 时调用 onSuccess', async () => {
    const startAuth = vi
      .spyOn(codebuddyOAuthApi, 'startAuth')
      .mockResolvedValue({ verification_uri_complete: 'https://cb/auth', auth_state: 'state-1' });
    const pollAuth = vi
      .spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockResolvedValue({ saved: true, message: '认证成功' });
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

  it('saved 不为 true 时快速失败且绝不误报成功', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-invalid-success',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({ saved: false });
    vi.spyOn(codebuddyOAuthApi, 'cancelAuth').mockResolvedValue({ cancelled: true });

    const onSuccess = vi.fn<() => void>();
    const oauth = useOAuthPolling({ onSuccess });
    await oauth.start();

    expect(onSuccess).not.toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith('认证响应无效，请重新认证');
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
  });

  it('点击开始时同步预打开窗口，拿到认证链接后自动跳转', async () => {
    const popup = {
      closed: false,
      close: vi.fn<() => void>(),
      location: { href: 'about:blank' },
      opener: window,
    } as unknown as Window;
    vi.mocked(window.open).mockReturnValue(popup);
    let resolveStart: (value: {
      verification_uri_complete: string;
      auth_state: string;
    }) => void = () => {};
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockImplementation(
      () => new Promise((resolve) => (resolveStart = resolve)),
    );
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );

    const oauth = useOAuthPolling();
    const startPromise = oauth.start();

    expect(window.open).toHaveBeenCalledWith('about:blank', '_blank');
    resolveStart({ verification_uri_complete: 'https://cb/auth', auth_state: 'state-popup' });
    await startPromise;

    expect(popup.location.href).toBe('https://cb/auth');
    expect(popup.opener).toBeNull();
    expect(oauth.manualOpenRequired.value).toBe(false);
    oauth.reset();
  });

  it.each([
    'javascript:alert(document.domain)',
    'data:text/html,unsafe',
    '/relative/auth',
    'https://user:password@cb.example/auth',
    'https://cb.example/\nunsafe',
  ])('拒绝危险认证链接且不导航预打开窗口：%s', async (unsafeUrl) => {
    const popup = {
      closed: false,
      close: vi.fn<() => void>(),
      location: { href: 'about:blank' },
      opener: window,
    } as unknown as Window;
    vi.mocked(window.open).mockReturnValue(popup);
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: unsafeUrl,
      auth_state: 'unsafe-state',
    });
    const pollAuth = vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});
    const cancelAuth = vi
      .spyOn(codebuddyOAuthApi, 'cancelAuth')
      .mockResolvedValue({ cancelled: true });

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(popup.location.href).toBe('about:blank');
    expect(popup.close).toHaveBeenCalledOnce();
    expect(pollAuth).not.toHaveBeenCalled();
    expect(cancelAuth).toHaveBeenCalledWith('unsafe-state');
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
    expect(toastMock.error).toHaveBeenCalledWith('认证链接无效');
  });

  it('弹窗被拦截或在响应前关闭时保留手动打开入口', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-blocked',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );

    const blocked = useOAuthPolling();
    await blocked.start();
    expect(blocked.manualOpenRequired.value).toBe(true);
    blocked.reset();

    const closedPopup = {
      closed: true,
      close: vi.fn<() => void>(),
      location: { href: 'about:blank' },
      opener: window,
    } as unknown as Window;
    vi.mocked(window.open).mockReturnValue(closedPopup);
    const closed = useOAuthPolling();
    await closed.start();
    expect(closed.manualOpenRequired.value).toBe(true);
    closed.reset();
  });

  it('登录窗口在轮询期间被关闭时切换为手动打开提示', async () => {
    const popupState = {
      closed: false,
      close: vi.fn<() => void>(),
      location: { href: 'about:blank' },
      opener: window,
    };
    vi.mocked(window.open).mockReturnValue(popupState as unknown as Window);
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-closed-later',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );
    const oauth = useOAuthPolling();
    await oauth.start();

    popupState.closed = true;
    await vi.advanceTimersByTimeAsync(1000);

    expect(oauth.manualOpenRequired.value).toBe(true);
    oauth.reset();
  });

  it('预打开窗口或窗口跳转抛错时保留手动打开入口', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-popup-errors',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );

    vi.mocked(window.open).mockImplementationOnce(() => {
      throw new Error('blocked');
    });
    const openFailed = useOAuthPolling();
    await openFailed.start();
    expect(openFailed.manualOpenRequired.value).toBe(true);
    openFailed.reset();

    const close = vi.fn<() => void>();
    const location = {} as Location;
    Object.defineProperty(location, 'href', {
      set: () => {
        throw new Error('navigation denied');
      },
    });
    vi.mocked(window.open).mockReturnValue({
      closed: false,
      close,
      location,
      opener: window,
    } as unknown as Window);
    const navigationFailed = useOAuthPolling();
    await navigationFailed.start();
    expect(close).toHaveBeenCalledOnce();
    expect(navigationFailed.manualOpenRequired.value).toBe(true);
    navigationFailed.reset();
  });

  it('可手动重新打开认证链接并记录再次被拦截', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-reopen',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );
    const oauth = useOAuthPolling();
    await oauth.start();

    vi.mocked(window.open).mockClear();
    const reopened = {
      closed: false,
      close: vi.fn<() => void>(),
      location: { href: 'about:blank' },
      opener: window,
    } as unknown as Window;
    vi.mocked(window.open).mockReturnValue(reopened);
    oauth.openAuthUrl();
    expect(window.open).toHaveBeenCalledWith('about:blank', '_blank');
    expect(reopened.location.href).toBe('https://cb/auth');
    expect(oauth.manualOpenRequired.value).toBe(false);

    vi.mocked(window.open).mockReturnValue(null);
    oauth.openAuthUrl();
    expect(oauth.manualOpenRequired.value).toBe(true);
    oauth.reset();
  });

  it('没有认证链接时手动打开操作直接返回', () => {
    const oauth = useOAuthPolling();
    vi.mocked(window.open).mockClear();

    oauth.openAuthUrl();

    expect(window.open).not.toHaveBeenCalled();
  });

  it('取消认证先完整重置本地状态，再通知后端消费 state', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-cancel',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );
    const cancelAuth = vi
      .spyOn(codebuddyOAuthApi, 'cancelAuth')
      .mockResolvedValue({ cancelled: true });
    const oauth = useOAuthPolling();
    await oauth.start();

    await oauth.cancel();

    expect(cancelAuth).toHaveBeenCalledWith('state-cancel');
    expect(oauth.polling.value).toBe(false);
    expect(oauth.starting.value).toBe(false);
    expect(oauth.authUrl.value).toBe('');
    expect(oauth.authState.value).toBe('');
    expect(oauth.elapsedSeconds.value).toBe(0);
    expect(oauth.manualOpenRequired.value).toBe(false);
  });

  it('没有 state 时取消不发远端请求，远端取消失败时按错误类型提示', async () => {
    const cancelAuth = vi.mocked(codebuddyOAuthApi.cancelAuth);
    const empty = useOAuthPolling();
    await empty.cancel();
    expect(cancelAuth).not.toHaveBeenCalled();

    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-cancel-error',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );
    const oauth = useOAuthPolling();
    await oauth.start();
    cancelAuth.mockRejectedValueOnce(new Error('远端取消失败'));
    await oauth.cancel();
    expect(toastMock.error).toHaveBeenLastCalledWith('远端取消失败');

    await oauth.start();
    cancelAuth.mockRejectedValueOnce('bad');
    await oauth.cancel();
    expect(toastMock.error).toHaveBeenLastCalledWith('取消认证失败');
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

  it('pollAuth 未返回 saved=true 时停止并提示协议错误', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-no-token',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockResolvedValue({});

    const oauth = useOAuthPolling();
    await oauth.start();

    expect(oauth.polling.value).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith('认证响应无效，请重新认证');
    oauth.reset();
  });

  it('stop() 后忽略未完成 pollAuth 的迟到成功响应', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-late-poll',
    });
    let resolvePoll: (value: { saved: boolean }) => void = () => {};
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
    resolvePoll({ saved: true });
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
    const pollAuth = vi
      .spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockRejectedValue(new ApiError(400, 'pending', { error: 'authorization_pending' }));

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

  it('凭证保存失败的 500 为终止错误，保留原始提示且不再轮询已消费 state', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-save-failed',
    });
    const pollAuth = vi
      .spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockRejectedValue(
        new ApiError(500, '凭证保存失败，请重新认证', { detail: '凭证保存失败，请重新认证' }),
      );
    const cancelAuth = vi.mocked(codebuddyOAuthApi.cancelAuth);

    const oauth = useOAuthPolling({ pollIntervalMs: 1000 });
    await oauth.start();

    expect(oauth.polling.value).toBe(false);
    expect(oauth.authState.value).toBe('');
    expect(toastMock.error).toHaveBeenCalledWith('凭证保存失败，请重新认证');
    expect(cancelAuth).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(pollAuth).toHaveBeenCalledOnce();
  });

  it('pollAuth 的网络错误指数退避并保留 state', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-normal-error',
    });
    const pollAuth = vi
      .spyOn(codebuddyOAuthApi, 'pollAuth')
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce(new Error('network again'))
      .mockRejectedValue(new ApiError(400, 'pending', { error: 'authorization_pending' }));
    const cancelAuth = vi.mocked(codebuddyOAuthApi.cancelAuth);

    const oauth = useOAuthPolling({ pollIntervalMs: 1000 });
    await oauth.start();
    expect(oauth.polling.value).toBe(true);
    expect(oauth.authState.value).toBe('state-normal-error');
    expect(cancelAuth).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1000);
    expect(pollAuth).toHaveBeenCalledTimes(2);
    expect(oauth.polling.value).toBe(true);

    await vi.advanceTimersByTimeAsync(1999);
    expect(pollAuth).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(pollAuth).toHaveBeenCalledTimes(3);
    expect(cancelAuth).not.toHaveBeenCalled();
    oauth.reset();
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

  it('达到五分钟式时长上限时取消后端 state 并停止轮询', async () => {
    vi.spyOn(codebuddyOAuthApi, 'startAuth').mockResolvedValue({
      verification_uri_complete: 'https://cb/auth',
      auth_state: 'state-duration-limit',
    });
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );
    const cancelAuth = vi.mocked(codebuddyOAuthApi.cancelAuth);
    const oauth = useOAuthPolling({
      pollIntervalMs: 5000,
      maxDurationSeconds: 3,
      maxAttempts: 100,
    });

    await oauth.start();
    await vi.advanceTimersByTimeAsync(3000);

    expect(cancelAuth).toHaveBeenCalledWith('state-duration-limit');
    expect(oauth.polling.value).toBe(false);
    expect(oauth.authState.value).toBe('');
    expect(toastMock.warning).toHaveBeenCalledWith('授权等待超时，请重试');
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
    vi.spyOn(codebuddyOAuthApi, 'pollAuth').mockRejectedValue(
      new ApiError(400, 'pending', { error: 'authorization_pending' }),
    );

    const oauth = useOAuthPolling();
    await oauth.start();
    expect(oauth.polling.value).toBe(true);
    expect(beforeUnmountCallbacks).toHaveLength(1);

    vi.mocked(codebuddyOAuthApi.cancelAuth).mockRejectedValueOnce(new Error('ignore on unmount'));
    beforeUnmountCallbacks[0]();
    await Promise.resolve();
    expect(oauth.polling.value).toBe(false);
    expect(toastMock.error).not.toHaveBeenCalled();
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
