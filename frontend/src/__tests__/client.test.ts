import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiRequest, ApiError, isUnauthorizedError, setUnauthorizedHandler } from '../api/client';

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

describe('apiRequest', () => {
  it('发送 JSON 并返回解析后的响应', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiRequest<{ ok: boolean }>('/api/admin/status', {
      method: 'POST',
      json: { value: 1 },
    });

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/status',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: '{"value":1}',
      }),
    );
  });

  it('把非 2xx JSON 响应转换为 ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: '认证失败' }, 401)),
    );

    await expect(apiRequest('/api/admin/status')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: '认证失败',
    });
  });

  it('非 JSON 错误响应使用 HTTP 状态生成消息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(new Response('bad gateway', { status: 502 })),
    );

    await expect(apiRequest('/api/admin/status')).rejects.toMatchObject({
      status: 502,
      message: '请求失败：HTTP 502',
    });
  });

  it('JSON null 错误响应使用 HTTP 状态生成消息', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(null, 500)));

    await expect(apiRequest('/api/admin/status')).rejects.toMatchObject({
      status: 500,
      message: '请求失败：HTTP 500',
    });
  });

  it('允许没有 content-type 的文本响应', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue({
        ok: true,
        headers: { get: () => null },
        text: async () => 'ok',
      } as unknown as Response),
    );

    await expect(apiRequest<string>('/plain')).resolves.toBe('ok');
  });
});

describe('401 全局未授权处理', () => {
  afterEach(() => setUnauthorizedHandler(null));

  it('401 响应触发已注册的未授权 handler', async () => {
    const handler = vi.fn<() => void>();
    setUnauthorizedHandler(handler);
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({ detail: '认证失败' }, 401, { 'WWW-Authenticate': 'Bearer' }),
        ),
    );

    await expect(apiRequest('/api/admin/status')).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('不带认证挑战头的业务 401 不触发未授权 handler', async () => {
    const handler = vi.fn<() => void>();
    setUnauthorizedHandler(handler);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: '上游凭证失效' }, 401)),
    );

    await expect(
      apiRequest('/api/admin/playground/openai/v1/chat/completions'),
    ).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });

  it('未注册 handler 时 401 仍抛出 ApiError 但不报错', async () => {
    setUnauthorizedHandler(null);
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({ detail: '认证失败' }, 401, { 'WWW-Authenticate': 'Bearer' }),
        ),
    );

    await expect(apiRequest('/api/admin/status')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
    });
  });

  it('非 401 错误不触发未授权 handler', async () => {
    const handler = vi.fn<() => void>();
    setUnauthorizedHandler(handler);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(new Response('err', { status: 500 })),
    );

    await expect(apiRequest('/api/admin/status')).rejects.toMatchObject({ status: 500 });
    expect(handler).not.toHaveBeenCalled();
  });

  it('isUnauthorizedError 识别 401 ApiError', () => {
    expect(isUnauthorizedError(new ApiError(401, '认证失败'))).toBe(true);
    expect(isUnauthorizedError(new ApiError(403, '禁止'))).toBe(false);
    expect(isUnauthorizedError(new Error('普通错误'))).toBe(false);
    expect(isUnauthorizedError(null)).toBe(false);
    expect(isUnauthorizedError({ status: 401 })).toBe(false);
  });
});

describe('请求超时', () => {
  it('为请求设置 15 秒超时', async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout');
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await apiRequest('/api/admin/status');

    expect(timeoutSpy).toHaveBeenCalledWith(15000);
    expect(fetchMock.mock.calls[0]![1]!.signal).toBeInstanceOf(AbortSignal);
    timeoutSpy.mockRestore();
  });

  it('允许单次请求覆盖默认超时时间', async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout');
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await apiRequest('/codebuddy/auth/start', { timeoutMs: 35000 });

    expect(timeoutSpy).toHaveBeenCalledWith(35000);
    expect(fetchMock.mock.calls[0]![1]!.signal).toBeInstanceOf(AbortSignal);
    timeoutSpy.mockRestore();
  });

  it('与调用方传入的 signal 合并', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    const callerController = new AbortController();

    await apiRequest('/api/admin/status', { signal: callerController.signal });

    const signal = fetchMock.mock.calls[0]![1]!.signal as AbortSignal;
    expect(signal).not.toBe(callerController.signal);
    callerController.abort();
    expect(signal.aborted).toBe(true);
  });

  it('自定义超时时间仍与调用方传入的 signal 合并', async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout');
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    const callerController = new AbortController();

    await apiRequest('/codebuddy/auth/poll', {
      method: 'POST',
      json: { auth_state: 'state' },
      signal: callerController.signal,
      timeoutMs: 35000,
    });

    const signal = fetchMock.mock.calls[0]![1]!.signal as AbortSignal;
    expect(timeoutSpy).toHaveBeenCalledWith(35000);
    expect(signal).not.toBe(callerController.signal);
    callerController.abort();
    expect(signal.aborted).toBe(true);
    timeoutSpy.mockRestore();
  });
});
