export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/**
 * 注册全局未授权回调。传入 null 可清除已注册的回调。
 * 在 apiRequest 检测到本系统认证层的 Bearer 401 时调用，随后仍照常抛出 ApiError，
 * 以便 QueryCache / MutationCache 通过 isUnauthorizedError 识别并跳过错误提示。
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/**
 * 仅处理本系统认证层返回的 Bearer challenge，避免把上游凭证 401 误判为会话失效。
 * 返回 true 表示响应已确认为本系统认证失败。
 */
export function handleUnauthorizedResponse(response: Response): boolean {
  const challenge = response.headers.get('WWW-Authenticate')?.trim().toLowerCase();
  if (response.status !== 401 || challenge !== 'bearer') return false;
  unauthorizedHandler?.();
  return true;
}

export function isUnauthorizedError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

interface ApiRequestOptions extends RequestInit {
  json?: unknown;
  timeoutMs?: number;
}

function throwApiError(status: number, body: unknown): never {
  const message =
    typeof body === 'object' && body !== null && 'detail' in body
      ? String((body as { detail: unknown }).detail)
      : `请求失败：HTTP ${status}`;
  throw new ApiError(status, message, body);
}

const REQUEST_TIMEOUT_MS = 15_000;

/**
 * 调用方取消和请求超时任一触发都会 abort fetch。
 */
function buildSignal(callerSignal: AbortSignal | null | undefined, timeoutMs: number): AbortSignal {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  if (!callerSignal) {
    return timeoutSignal;
  }
  // AbortSignal.any 在 ES2022 lib 可用；若类型缺失则做类型断言
  const anyOf = (
    AbortSignal as unknown as {
      any: (signals: AbortSignal[]) => AbortSignal;
    }
  ).any;
  return anyOf([callerSignal, timeoutSignal]);
}

/**
 * 统一设置 JSON、可按调用覆盖的超时和 same-origin cookie；本系统认证层的 Bearer 401 会先触发全局 handler，
 * 再继续抛出 ApiError，供 QueryCache/MutationCache 跳过重复提示。
 */
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const { signal: callerSignal, timeoutMs = REQUEST_TIMEOUT_MS, ...rest } = options;
  const signal = buildSignal(callerSignal, timeoutMs);

  const response = await fetch(path, {
    ...rest,
    headers,
    signal,
    credentials: 'same-origin',
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
  });

  handleUnauthorizedResponse(response);

  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  return response.ok ? (body as T) : throwApiError(response.status, body);
}
