import { ref, onBeforeUnmount } from 'vue';
import { useToast } from './useToast';
import { ApiError } from '../api/client';
import { codebuddyOAuthApi } from '../api/admin';

interface OAuthPollingOptions {
  /** 轮询间隔，默认 5000ms */
  pollIntervalMs?: number;
  /** 最大轮询次数（含 start 时立即执行的那一次），默认 60（约 5 分钟） */
  maxAttempts?: number;
  /** 认证成功回调，通常用于刷新凭证列表 */
  onSuccess?: () => void;
}

interface StartAuthResponse {
  verification_uri_complete?: string;
  auth_state?: string;
  success?: boolean;
  message?: string;
}

interface PollAuthResponse {
  saved?: boolean;
  message?: string;
}

/**
 * CodeBuddy OAuth 登录轮询状态机。
 *
 * 点击开始时同步预打开空白页，避免异步响应返回后再打开窗口而被浏览器拦截。
 * 每次轮询完成后才安排下一次，保证慢网络下不会出现并发轮询。
 */
export function useOAuthPolling(options: OAuthPollingOptions = {}) {
  const toast = useToast();
  const pollIntervalMs = options.pollIntervalMs ?? 5000;
  const maxAttempts = options.maxAttempts ?? 60;

  const authUrl = ref('');
  const authState = ref('');
  const starting = ref(false);
  const polling = ref(false);
  const elapsedSeconds = ref(0);
  const manualOpenRequired = ref(false);

  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let attemptCount = 0;
  let abortController: AbortController | null = null;
  let pollInFlight = false;
  let authWindow: Window | null = null;

  async function start(): Promise<void> {
    if (starting.value || polling.value) return;

    reset();
    preopenAuthWindow();
    starting.value = true;
    const controller = new AbortController();
    abortController = controller;

    try {
      const result: StartAuthResponse = await codebuddyOAuthApi.startAuth(controller.signal);
      if (!isCurrentController(controller)) return;
      if (!result.verification_uri_complete || !result.auth_state) {
        const message = result.message || '认证启动失败';
        reset();
        toast.error(message);
        return;
      }

      authUrl.value = result.verification_uri_complete;
      authState.value = result.auth_state;
      polling.value = true;
      starting.value = false;
      attemptCount = 0;
      elapsedSeconds.value = 0;
      navigateAuthWindow(authUrl.value);
      elapsedTimer = setInterval(() => {
        elapsedSeconds.value += 1;
        if (authWindow?.closed) {
          authWindow = null;
          manualOpenRequired.value = true;
        }
      }, 1000);
      await poll();
    } catch (error) {
      if ((error as Error)?.name === 'AbortError' || !isCurrentController(controller)) return;
      reset();
      toast.error(error instanceof Error ? error.message : '认证启动失败');
    } finally {
      if (abortController === controller) {
        starting.value = false;
      }
    }
  }

  async function poll(): Promise<void> {
    if (!authState.value || !polling.value || pollInFlight) return;
    const controller = abortController!;
    const currentAuthState = authState.value;
    attemptCount++;

    if (attemptCount > maxAttempts) {
      await cancelAndReset(false);
      toast.warning('授权等待超时，请重试');
      return;
    }

    pollInFlight = true;
    try {
      const result: PollAuthResponse = await codebuddyOAuthApi.pollAuth(
        currentAuthState,
        controller.signal,
      );
      if (
        !isCurrentController(controller) ||
        !polling.value ||
        authState.value !== currentAuthState
      ) {
        return;
      }

      if (result.saved === true) {
        reset(false);
        toast.success('认证成功');
        options.onSuccess?.();
        return;
      }

      await cancelAndReset(false);
      toast.error('认证响应无效，请重新认证');
    } catch (error) {
      if ((error as Error)?.name === 'AbortError' || !isCurrentController(controller)) return;
      if (error instanceof ApiError) {
        const detail = error.detail as { error?: string; error_description?: string };
        if (detail?.error === 'authorization_pending' || detail?.error === 'slow_down') return;
        await cancelAndReset(false);
        toast.error(detail?.error_description || error.message);
        return;
      }
      await cancelAndReset(false);
      toast.error(error instanceof Error ? error.message : '认证失败');
    } finally {
      if (abortController === controller) {
        pollInFlight = false;
        scheduleNextPoll();
      }
    }
  }

  function preopenAuthWindow(): void {
    try {
      authWindow = window.open('about:blank', '_blank');
    } catch {
      authWindow = null;
    }
    manualOpenRequired.value = authWindow === null;
  }

  function navigateAuthWindow(url: string): void {
    if (!authWindow || authWindow.closed) {
      authWindow = null;
      manualOpenRequired.value = true;
      return;
    }
    try {
      authWindow.opener = null;
      authWindow.location.href = url;
      manualOpenRequired.value = false;
    } catch {
      releaseAuthWindow(true);
      manualOpenRequired.value = true;
    }
  }

  function openAuthUrl(): void {
    if (!authUrl.value) return;
    releaseAuthWindow(false);
    preopenAuthWindow();
    if (authWindow) navigateAuthWindow(authUrl.value);
  }

  function isCurrentController(controller: AbortController): boolean {
    return abortController === controller && !controller.signal.aborted;
  }

  function scheduleNextPoll(): void {
    pollTimer = setTimeout(() => {
      pollTimer = null;
      void poll();
    }, pollIntervalMs);
  }

  function clearRuntime(closeWindow: boolean): void {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    releaseAuthWindow(closeWindow);
    pollInFlight = false;
    polling.value = false;
    starting.value = false;
  }

  function releaseAuthWindow(closeWindow: boolean): void {
    if (closeWindow && authWindow && !authWindow.closed) {
      try {
        authWindow.close();
      } catch {
        // 跨域窗口或浏览器策略可能拒绝关闭；状态仍必须清理。
      }
    }
    authWindow = null;
  }

  /** 仅本地停止；保留给组件卸载和测试清理，用户取消应调用 cancel。 */
  function stop(): void {
    clearRuntime(true);
  }

  function reset(closeWindow = true): void {
    clearRuntime(closeWindow);
    authUrl.value = '';
    authState.value = '';
    elapsedSeconds.value = 0;
    manualOpenRequired.value = false;
    attemptCount = 0;
  }

  async function cancelAndReset(reportFailure: boolean): Promise<void> {
    const state = authState.value;
    reset();
    if (!state) return;
    try {
      await codebuddyOAuthApi.cancelAuth(state);
    } catch (error) {
      if (reportFailure) {
        toast.error(error instanceof Error ? error.message : '取消认证失败');
      }
    }
  }

  async function cancel(): Promise<void> {
    await cancelAndReset(true);
  }

  onBeforeUnmount(() => {
    void cancelAndReset(false);
  });

  return {
    authUrl,
    authState,
    starting,
    polling,
    elapsedSeconds,
    manualOpenRequired,
    start,
    stop,
    reset,
    cancel,
    openAuthUrl,
  };
}
