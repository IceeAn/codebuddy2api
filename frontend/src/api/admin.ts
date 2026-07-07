import { apiRequest, ApiError, handleUnauthorizedResponse } from './client';
import type {
  AdminStatus,
  ApiKeyCreateResponse,
  ApiKeyRecord,
  ChatCompletionRequest,
  CodeBuddyPollAuthResponse,
  CredentialRecord,
  CredentialsResponse,
  CurrentCredential,
  DeleteCredentialResponse,
  ModelListResponse,
  SessionInfo,
  SettingsResponse,
} from '../types';

// 覆盖后端串行执行的 30 秒模型查询与 300 秒聊天请求，并预留响应处理时间。
const CREDENTIAL_TEST_TIMEOUT_MS = 335_000;
const OAUTH_START_TIMEOUT_MS = 35_000;
const OAUTH_POLL_TIMEOUT_MS = 35_000;

export const authApi = {
  session: () => apiRequest<SessionInfo>('/auth/session'),
  login: (username: string, password: string) =>
    apiRequest<SessionInfo>('/auth/login', {
      method: 'POST',
      json: { username, password },
    }),
  logout: () =>
    apiRequest<{ authenticated: false }>('/auth/logout', {
      method: 'POST',
      json: {},
    }),
};

export const adminApi = {
  status: () => apiRequest<AdminStatus>('/api/admin/status'),
  settings: () => apiRequest<SettingsResponse>('/api/admin/settings'),
  saveSettings: (settings: Record<string, unknown>) =>
    apiRequest<SettingsResponse>('/api/admin/settings', {
      method: 'PUT',
      json: { settings },
    }),
  apiKeys: () => apiRequest<{ api_keys: ApiKeyRecord[] }>('/api/admin/api-keys'),
  createApiKey: (name: string) =>
    apiRequest<ApiKeyCreateResponse>('/api/admin/api-keys', {
      method: 'POST',
      json: { name },
    }),
  deleteApiKey: (keyId: string) =>
    apiRequest<{ deleted: boolean }>(`/api/admin/api-keys/${encodeURIComponent(keyId)}`, {
      method: 'DELETE',
    }),
  credentials: () => apiRequest<CredentialsResponse>('/api/admin/credentials'),
  createCredential: (bearerToken: string) =>
    apiRequest<{ credential: CredentialRecord }>('/api/admin/credentials', {
      method: 'POST',
      json: { bearer_token: bearerToken },
    }),
  selectCredential: (credentialId: string) =>
    apiRequest<{
      auto_rotation_disabled_by_select: boolean;
      current: CurrentCredential;
    }>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/select`,
      {
        method: 'POST',
      },
    ),
  deleteCredential: (credentialId: string) =>
    apiRequest<DeleteCredentialResponse>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}`,
      {
        method: 'DELETE',
      },
    ),
  testCredential: (credentialId: string) =>
    apiRequest<{ ok: boolean; status_code: number; detail?: string }>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/test`,
      {
        method: 'POST',
        json: {},
        timeoutMs: CREDENTIAL_TEST_TIMEOUT_MS,
      },
    ),
  toggleRotation: () =>
    apiRequest<{
      message?: string;
      auto_rotation_enabled: boolean;
      current: CredentialsResponse['current'];
    }>('/api/admin/credentials/rotation/toggle', { method: 'POST' }),
};

export const codebuddyOAuthApi = {
  startAuth: (signal?: AbortSignal) => {
    const path = '/codebuddy/auth/start';
    if (signal) {
      return apiRequest<{
        verification_uri_complete?: string;
        auth_state?: string;
        success?: boolean;
        message?: string;
      }>(path, { signal, timeoutMs: OAUTH_START_TIMEOUT_MS });
    }
    return apiRequest<{
      verification_uri_complete?: string;
      auth_state?: string;
      success?: boolean;
      message?: string;
    }>(path, { timeoutMs: OAUTH_START_TIMEOUT_MS });
  },
  pollAuth: (authState: string, signal?: AbortSignal) => {
    const options: {
      method: string;
      json: { auth_state: string };
      signal?: AbortSignal;
      timeoutMs: number;
    } = {
      method: 'POST',
      json: { auth_state: authState },
      timeoutMs: OAUTH_POLL_TIMEOUT_MS,
    };
    if (signal) {
      options.signal = signal;
    }
    return apiRequest<CodeBuddyPollAuthResponse>('/codebuddy/auth/poll', options);
  },
  cancelAuth: (authState: string, signal?: AbortSignal) => {
    const options: {
      method: string;
      json: { auth_state: string };
      signal?: AbortSignal;
    } = {
      method: 'POST',
      json: { auth_state: authState },
    };
    if (signal) {
      options.signal = signal;
    }
    return apiRequest<{ cancelled: true }>('/codebuddy/auth/cancel', options);
  },
};

export const openaiPlaygroundApi = {
  models: () => apiRequest<ModelListResponse>('/api/admin/playground/openai/v1/models'),
  /**
   * 直接使用 fetch，避免 apiRequest 先消费 body；调用方需要自行读取流式响应。
   * 仅将带 Bearer challenge 的 401 识别为本系统会话失效；上游凭证 401 交给调用方处理。
   */
  chat: (body: ChatCompletionRequest, signal?: AbortSignal) => {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    return fetch('/api/admin/playground/openai/v1/chat/completions', {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body: JSON.stringify(body),
      signal,
    }).then((response) => {
      if (handleUnauthorizedResponse(response)) {
        throw new ApiError(401, '认证过期，请重新登录');
      }
      return response;
    });
  },
};
