import { ref, onBeforeUnmount } from 'vue';
import { useToast } from './useToast';
import { ApiError } from '../api/client';
import { codebuddyApi } from '../api/admin';

interface OAuthPollingOptions {
  /** 轮询间隔，默认 5000ms */
  pollIntervalMs?: number;
  /** 最大轮询次数（含 start 时立即执行的那一次），默认 60（约 5 分钟） */
  maxAttempts?: number;
  /** 认证成功回调，通常用于 invalidate credentials 查询 */
  onSuccess?: () => void;
}

interface StartAuthResponse {
  verification_uri_complete?: string;
  auth_state?: string;
  success?: boolean;
  message?: string;
}

interface PollAuthResponse {
  access_token?: string;
  saved?: boolean;
}

/**
 * CodeBuddy OAuth 设备授权轮询 composable。
 *
 * 集中处理设备授权中的状态竞态、轮询超时、卸载清理和慢网络下的轮询重叠。
 * 上一轮 pollAuth 完成后才会安排下一轮，避免并发轮询写入旧状态。
 *
 * 必须在组件 setup 上下文中调用（依赖 `useToast` 与 `onBeforeUnmount`）。
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

  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let attemptCount = 0;
  let abortController: AbortController | null = null;
  let pollInFlight = false;

  async function start(): Promise<void> {
    // 防竞态：startAuth 进行中或已在轮询中，直接返回
    if (starting.value || polling.value) return;
    stop();
    starting.value = true;
    const controller = new AbortController();
    abortController = controller;
    try {
      const result: StartAuthResponse = await codebuddyApi.startAuth(controller.signal);
      if (!isCurrentController(controller)) return;
      if (!result.verification_uri_complete || !result.auth_state) {
        toast.error(result.message || '认证启动失败');
        return;
      }
      authUrl.value = result.verification_uri_complete;
      authState.value = result.auth_state;
      polling.value = true;
      attemptCount = 0;
      elapsedSeconds.value = 0;
      // 使用全局 setInterval/clearInterval 而非 window.setInterval：
      // 浏览器中两者等价；node 测试环境中 window 不存在，全局 setInterval 仍可用且被 fake timers 拦截。
      elapsedTimer = setInterval(() => {
        elapsedSeconds.value += 1;
      }, 1000);
      // 立即执行一次轮询，避免首次需要等满一个周期
      await poll();
    } catch (error) {
      if ((error as Error)?.name === 'AbortError' || !isCurrentController(controller)) return;
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
      toast.warning('授权等待超时，请重试');
      stop();
      return;
    }
    pollInFlight = true;
    try {
      const result: PollAuthResponse = await codebuddyApi.pollAuth(
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
      if (result.access_token) {
        stop();
        authUrl.value = '';
        authState.value = '';
        toast.success('认证成功');
        options.onSuccess?.();
      }
    } catch (error) {
      if ((error as Error)?.name === 'AbortError' || !isCurrentController(controller)) return;
      if (error instanceof ApiError) {
        const detail = error.detail as { error?: string; error_description?: string };
        // authorization_pending / slow_down 是可恢复的，继续轮询
        if (detail?.error === 'authorization_pending' || detail?.error === 'slow_down') return;
        stop();
        toast.error(detail?.error_description || error.message);
        return;
      }
      stop();
      toast.error(error instanceof Error ? error.message : '认证失败');
    } finally {
      if (abortController === controller) {
        pollInFlight = false;
        scheduleNextPoll();
      }
    }
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

  function stop(): void {
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
    pollInFlight = false;
    polling.value = false;
    starting.value = false;
  }

  function reset(): void {
    stop();
    authUrl.value = '';
    authState.value = '';
    elapsedSeconds.value = 0;
    attemptCount = 0;
  }

  onBeforeUnmount(stop);

  return { authUrl, authState, starting, polling, elapsedSeconds, start, stop, reset };
}
