import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { apiRequest } from '../api/client';

const { apiRequestMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn<typeof apiRequest>(),
}));

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    apiRequest: apiRequestMock,
  };
});

import { adminApi, authApi, codebuddyOAuthApi, openaiPlaygroundApi } from '../api/admin';
import { ApiError, setUnauthorizedHandler } from '../api/client';

describe('管理 API 封装', () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it('按约定构造认证请求', async () => {
    apiRequestMock.mockResolvedValue({});

    await authApi.session();
    await authApi.login('admin', 'secret');
    await authApi.logout();

    expect(apiRequestMock.mock.calls).toEqual([
      ['/auth/session'],
      ['/auth/login', { method: 'POST', json: { username: 'admin', password: 'secret' } }],
      ['/auth/logout', { method: 'POST', json: {} }],
    ]);
  });

  it('按约定构造管理请求', async () => {
    apiRequestMock.mockResolvedValue({});

    await adminApi.status();
    await adminApi.settings();
    await adminApi.saveSettings({ level: 'DEBUG' });
    await adminApi.apiKeys();
    await adminApi.createApiKey('robot');
    await adminApi.deleteApiKey('key/id');
    await adminApi.credentials();
    await adminApi.createCredential('token');
    await adminApi.selectCredential('cred/id');
    await adminApi.deleteCredential('cred/id');
    await adminApi.testCredential('cred/id');
    await adminApi.toggleRotation();

    expect(apiRequestMock.mock.calls).toEqual([
      ['/api/admin/status'],
      ['/api/admin/settings'],
      ['/api/admin/settings', { method: 'PUT', json: { settings: { level: 'DEBUG' } } }],
      ['/api/admin/api-keys'],
      ['/api/admin/api-keys', { method: 'POST', json: { name: 'robot' } }],
      ['/api/admin/api-keys/key%2Fid', { method: 'DELETE' }],
      ['/api/admin/credentials'],
      [
        '/api/admin/credentials',
        { method: 'POST', json: { bearer_token: 'token' } },
      ],
      ['/api/admin/credentials/cred%2Fid/select', { method: 'POST' }],
      ['/api/admin/credentials/cred%2Fid', { method: 'DELETE' }],
      ['/api/admin/credentials/cred%2Fid/test', { method: 'POST', json: {}, timeoutMs: 335000 }],
      ['/api/admin/credentials/rotation/toggle', { method: 'POST' }],
    ]);
  });

  it('按约定构造 CodeBuddy OAuth 与 OpenAI Playground 请求', async () => {
    apiRequestMock.mockResolvedValue({});

    await codebuddyOAuthApi.startAuth();
    await codebuddyOAuthApi.pollAuth('state');
    await codebuddyOAuthApi.cancelAuth('state');
    await openaiPlaygroundApi.models();

    expect(apiRequestMock.mock.calls).toEqual([
      ['/codebuddy/auth/start', { timeoutMs: 35000 }],
      ['/codebuddy/auth/poll', { method: 'POST', json: { auth_state: 'state' }, timeoutMs: 35000 }],
      ['/codebuddy/auth/cancel', { method: 'POST', json: { auth_state: 'state' } }],
      ['/api/admin/playground/openai/v1/models'],
    ]);
  });

  it('CodeBuddy OAuth 启动请求透传取消 signal', async () => {
    apiRequestMock.mockResolvedValue({});
    const controller = new AbortController();

    await codebuddyOAuthApi.startAuth(controller.signal);

    expect(apiRequestMock).toHaveBeenCalledWith('/codebuddy/auth/start', {
      signal: controller.signal,
      timeoutMs: 35000,
    });
  });

  it('CodeBuddy OAuth 轮询请求透传取消 signal', async () => {
    apiRequestMock.mockResolvedValue({});
    const controller = new AbortController();

    await codebuddyOAuthApi.pollAuth('state', controller.signal);

    expect(apiRequestMock).toHaveBeenCalledWith('/codebuddy/auth/poll', {
      method: 'POST',
      json: { auth_state: 'state' },
      signal: controller.signal,
      timeoutMs: 35000,
    });
  });

  it('CodeBuddy OAuth 取消请求透传 signal', async () => {
    apiRequestMock.mockResolvedValue({});
    const controller = new AbortController();

    await codebuddyOAuthApi.cancelAuth('state', controller.signal);

    expect(apiRequestMock).toHaveBeenCalledWith('/codebuddy/auth/cancel', {
      method: 'POST',
      json: { auth_state: 'state' },
      signal: controller.signal,
    });
  });
});

describe('OpenAI Playground chat 请求', () => {
  afterEach(() => {
    setUnauthorizedHandler(null);
    vi.unstubAllGlobals();
  });

  it('透传请求体和 signal 并返回响应', async () => {
    const response = new Response('{}');
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    const body = { model: 'glm', messages: [{ role: 'user', content: 'hello' }] };

    await expect(openaiPlaygroundApi.chat(body, controller.signal)).resolves.toBe(response);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/playground/openai/v1/chat/completions',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify(body),
        signal: controller.signal,
      }),
    );
    const requestInit = fetchMock.mock.calls[0]![1]!;
    expect(new Headers(requestInit.headers).get('Content-Type')).toBe('application/json');
  });

  it('401 响应触发全局未授权处理并转换为 ApiError', async () => {
    const unauthorizedHandler = vi.fn<() => void>();
    setUnauthorizedHandler(unauthorizedHandler);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response('', {
          status: 401,
          headers: { 'WWW-Authenticate': 'Bearer' },
        }),
      ),
    );

    await expect(openaiPlaygroundApi.chat({ model: 'glm', messages: [] })).rejects.toEqual(
      new ApiError(401, '认证过期，请重新登录'),
    );
    expect(unauthorizedHandler).toHaveBeenCalledOnce();
  });

  it('上游凭证 401 不触发全局未授权处理并保留响应', async () => {
    const unauthorizedHandler = vi.fn<() => void>();
    setUnauthorizedHandler(unauthorizedHandler);
    const response = new Response('{"detail":"CodeBuddy API authentication failed"}', {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(response));

    await expect(openaiPlaygroundApi.chat({ model: 'glm', messages: [] })).resolves.toBe(response);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
  });
});
