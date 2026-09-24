import { apiRequest, ApiError, handleUnauthorizedResponse } from './client';
import type {
  AdminStatus,
  AnthropicMessageRequest,
  ApiKeyCreateResponse,
  ApiKeyRecord,
  ChatCompletionRequest,
  ResponsesRequest,
  CodeBuddyPollAuthResponse,
  CredentialRecord,
  CredentialQuota,
  CredentialQuotaProbeMode,
  CredentialQuotaProbeModeUpdateResponse,
  CredentialDailyCheckin,
  CredentialAccountsResponse,
  CredentialsResponse,
  CurrentCredential,
  DeleteCredentialResponse,
  ModelListResponse,
  SessionInfo,
  BootstrapStatus,
  PasswordChangedResponse,
  SettingsResponse,
  StatsOverviewQuery,
  StatsOverviewResponse,
  StatsDimension,
  StatsDimensionQuery,
  StatsDimensionResponse,
  StatsRequestRecord,
  StatsRequestsQuery,
  StatsRequestsResponse,
} from '../types';
import { buildStatsSearchParams } from '../utils/stats';

// 覆盖后端串行执行的 30 秒模型查询与 300 秒聊天请求，并预留响应处理时间。
const CREDENTIAL_TEST_TIMEOUT_MS = 335_000;
const ACCOUNT_SWITCH_TIMEOUT_MS = 70_000;
// 手动签到可能依次等待自动签到、自身签到与随后并发执行的一轮额度探测。
const DAILY_CHECKIN_TIMEOUT_MS = 335_000;
const QUOTA_PROBE_TIMEOUT_MS = 35_000;
const OAUTH_START_TIMEOUT_MS = 35_000;
const OAUTH_POLL_TIMEOUT_MS = 100_000;
const MODEL_LIST_TIMEOUT_MS = 35_000;
const PASSWORD_CHANGE_TIMEOUT_MS = 30_000;

export const authApi = {
  session: (signal?: AbortSignal) => apiRequest<SessionInfo>('/auth/session', { signal }),
  bootstrapStatus: () => apiRequest<BootstrapStatus>('/auth/bootstrap-status'),
  login: (username: string, password: string) =>
    apiRequest<SessionInfo>('/auth/login', {
      method: 'POST',
      json: { username, password },
    }),
  changePassword: (currentPassword: string | undefined, newPassword: string) =>
    apiRequest<PasswordChangedResponse>('/auth/change-password', {
      method: 'POST',
      json:
        currentPassword === undefined
          ? { new_password: newPassword }
          : { current_password: currentPassword, new_password: newPassword },
      timeoutMs: PASSWORD_CHANGE_TIMEOUT_MS,
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
    }>(`/api/admin/credentials/${encodeURIComponent(credentialId)}/select`, {
      method: 'POST',
    }),
  deleteCredential: (credentialId: string) =>
    apiRequest<DeleteCredentialResponse>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}`,
      {
        method: 'DELETE',
      },
    ),
  testCredential: (credentialId: string) =>
    apiRequest<{
      ok: boolean;
      status_code: number;
      detail?: string;
      model_source?: 'actual' | 'configured_fallback';
    }>(`/api/admin/credentials/${encodeURIComponent(credentialId)}/test`, {
      method: 'POST',
      json: {},
      timeoutMs: CREDENTIAL_TEST_TIMEOUT_MS,
    }),
  credentialAccounts: (credentialId: string) =>
    apiRequest<CredentialAccountsResponse>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/accounts`,
    ),
  selectCredentialAccount: (credentialId: string, accountId: string) =>
    apiRequest<{ selected: boolean; credential_id: string; account_id: string }>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/accounts/${encodeURIComponent(accountId)}/select`,
      { method: 'POST', timeoutMs: ACCOUNT_SWITCH_TIMEOUT_MS },
    ),
  toggleRotation: () =>
    apiRequest<{
      message?: string;
      auto_rotation_enabled: boolean;
      current: CredentialsResponse['current'];
    }>('/api/admin/credentials/rotation/toggle', { method: 'POST' }),
  dailyCheckin: (credentialId: string) =>
    apiRequest<CredentialDailyCheckin>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/daily-checkin`,
      { method: 'POST', timeoutMs: DAILY_CHECKIN_TIMEOUT_MS },
    ),
  refreshCredentialQuota: (credentialId: string) =>
    apiRequest<{ quota: CredentialQuota }>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/quota/refresh`,
      { method: 'POST', timeoutMs: QUOTA_PROBE_TIMEOUT_MS },
    ),
  updateCredentialQuotaProbeMode: (credentialId: string, mode: CredentialQuotaProbeMode) =>
    apiRequest<CredentialQuotaProbeModeUpdateResponse>(
      `/api/admin/credentials/${encodeURIComponent(credentialId)}/quota-probe-mode`,
      {
        method: 'PUT',
        json: { mode },
        timeoutMs: QUOTA_PROBE_TIMEOUT_MS,
      },
    ),
  statsOverview: (query: StatsOverviewQuery) =>
    apiRequest<StatsOverviewResponse>(`/api/admin/stats/overview?${buildStatsSearchParams(query)}`),
  statsRequests: (query: StatsRequestsQuery) =>
    apiRequest<StatsRequestsResponse>(`/api/admin/stats/requests?${buildStatsSearchParams(query)}`),
  statsDimensions: (dimension: StatsDimension, query: StatsDimensionQuery) =>
    apiRequest<StatsDimensionResponse>(
      `/api/admin/stats/dimensions/${encodeURIComponent(dimension)}?${buildStatsSearchParams(query)}`,
    ),
  statsRequestDetail: (requestId: number, snapshot: { id: number; time: number }) =>
    apiRequest<StatsRequestRecord>(
      `/api/admin/stats/requests/${encodeURIComponent(String(requestId))}?snapshot_id=${encodeURIComponent(String(snapshot.id))}&snapshot_time=${encodeURIComponent(String(snapshot.time))}`,
    ),
};

export const codebuddyOAuthApi = {
  startAuth: (signal?: AbortSignal) => {
    const path = '/codebuddy/auth/start';
    return apiRequest<{
      verification_uri_complete?: string;
      auth_state?: string;
      success?: boolean;
      message?: string;
      interval?: number;
      expires_in?: number;
    }>(path, { method: 'POST', signal, timeoutMs: OAUTH_START_TIMEOUT_MS });
  },
  pollAuth: (authState: string, signal?: AbortSignal) =>
    apiRequest<CodeBuddyPollAuthResponse>('/codebuddy/auth/poll', {
      method: 'POST',
      json: { auth_state: authState },
      signal,
      timeoutMs: OAUTH_POLL_TIMEOUT_MS,
    }),
  cancelAuth: (authState: string, signal?: AbortSignal) =>
    apiRequest<{ cancelled: true }>('/codebuddy/auth/cancel', {
      method: 'POST',
      json: { auth_state: authState },
      signal,
    }),
};

/** 返回未消费的响应体，供调用方读取 SSE；只拦截本系统的会话失效。 */
async function playgroundChat(
  path: string,
  body: ChatCompletionRequest | AnthropicMessageRequest,
  headers: Headers,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (handleUnauthorizedResponse(response)) {
    throw new ApiError(401, '认证过期，请重新登录');
  }
  return response;
}

export const openaiPlaygroundApi = {
  responses: (body: ResponsesRequest, signal?: AbortSignal) =>
    fetch('/api/admin/playground/openai/v1/responses', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    }).then((response) => {
      if (handleUnauthorizedResponse(response)) {
        throw new ApiError(401, '认证过期，请重新登录');
      }
      return response;
    }),
  models: (signal?: AbortSignal) =>
    apiRequest<ModelListResponse>('/api/admin/playground/openai/v1/models', {
      timeoutMs: MODEL_LIST_TIMEOUT_MS,
      signal,
    }),
  chat: (body: ChatCompletionRequest, signal?: AbortSignal) =>
    playgroundChat(
      '/api/admin/playground/openai/v1/chat/completions',
      body,
      new Headers({ 'Content-Type': 'application/json' }),
      signal,
    ),
};

export const anthropicPlaygroundApi = {
  models: (signal?: AbortSignal) =>
    apiRequest<ModelListResponse>('/api/admin/playground/anthropic/v1/models', {
      headers: { 'anthropic-version': '2023-06-01' },
      timeoutMs: MODEL_LIST_TIMEOUT_MS,
      signal,
    }),
  chat: (body: AnthropicMessageRequest, signal?: AbortSignal) =>
    playgroundChat(
      '/api/admin/playground/anthropic/v1/messages',
      body,
      new Headers({
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01',
      }),
      signal,
    ),
};
