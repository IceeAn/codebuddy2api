/** 后端当前凭证接口返回的完整状态集合。 */
export type CredentialStatus = 'no_credentials' | 'auto_rotation_disabled' | 'auto_rotation';

export interface CurrentCredential {
  status: CredentialStatus;
  credential_id?: string;
  filename?: string;
  user_id?: string;
  enterprise_id?: string;
  usage_count?: number;
  rotation_count?: number;
  auto_rotation_enabled?: boolean;
}

export interface AdminStatus {
  service: string;
  status: string;
  username: string;
  source: string;
  uptime_seconds: number;
  api_base_url: string;
  credentials: {
    total: number;
    valid: number;
    current: CurrentCredential;
  };
}

export type StatsTraffic = 'all' | 'external' | 'admin';
export type StatsSource = 'external_api' | 'admin_playground' | 'credential_test' | string;
export type StatsOutcome = 'success' | 'failure' | 'cancelled';
export type StatsRangePreset = 'today' | '7d' | '30d' | '90d' | 'all' | 'custom';
export type StatsMetric =
  | 'request_count'
  | 'total_tokens'
  | 'total_credit'
  | 'success_rate'
  | 'p95_first_output_ms'
  | 'p95_total_ms';
export type StatsDimension = 'models' | 'api_keys' | 'credentials';

export interface StatsOverviewQuery {
  start_at: number;
  end_at: number;
  timezone: string;
  traffic: StatsTraffic;
  model?: string;
  api_key_id?: string;
  credential_id?: string;
  outcome?: string;
}

export interface StatsRequestsQuery extends StatsOverviewQuery {
  page?: number;
  page_size?: number;
  snapshot_id?: number;
  snapshot_time?: number;
}

export interface StatsDimensionQuery extends StatsOverviewQuery {
  search?: string;
  cursor?: string;
  limit?: number;
}

export interface StatsTotals {
  request_count: number;
  success_rate: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_hit_tokens: number | null;
  cache_miss_tokens: number | null;
  total_credit: number | null;
  p95_first_output_ms: number | null;
  p95_first_output_ms_overflow: boolean;
  p95_total_ms: number | null;
  p95_total_ms_overflow: boolean;
  usage_coverage: number | null;
}

export interface StatsSeriesPoint {
  period_start: number;
  period?: string;
  request_count?: number;
  success_rate?: number | null;
  total_tokens?: number | null;
  total_credit?: number | null;
  p95_first_output_ms?: number | null;
  p95_first_output_ms_overflow?: boolean;
  p95_total_ms?: number | null;
  p95_total_ms_overflow?: boolean;
  usage_coverage?: number | null;
}

export interface StatsApiKeyDimension {
  id: string;
  name: string;
}

export interface StatsCredentialDimension {
  id: string;
  label: string;
}

export interface StatsRankMetrics {
  request_count: number;
  success_rate: number | null;
  total_tokens: number | null;
  total_credit: number | null;
  p95_total_ms: number | null;
  p95_total_ms_overflow: boolean;
  usage_coverage: number | null;
}

export interface StatsDimensionItem extends StatsRankMetrics {
  id: string;
  label: string;
  p95_first_output_ms: number | null;
  p95_first_output_ms_overflow: boolean;
}

export interface StatsDimensionResponse {
  items: StatsDimensionItem[];
  next_cursor: string | null;
}

export interface StatsModelBreakdown extends StatsRankMetrics {
  model: string;
}

export interface StatsApiKeyBreakdown extends StatsRankMetrics {
  id: string;
  name: string;
}

export interface StatsCredentialBreakdown extends StatsRankMetrics {
  id: string;
  label: string;
}

export interface StatsDataQuality {
  usage_coverage: number | null;
  dropped_events: number;
  detail_retention_days: number;
  boundary_precision: 'exact' | 'hourly_approximate';
}

export interface StatsOverviewResponse {
  totals: StatsTotals;
  series: StatsSeriesPoint[];
  dimensions: {
    models: string[];
    api_keys: StatsApiKeyDimension[];
    credentials: StatsCredentialDimension[];
    outcomes: string[];
  };
  breakdowns: {
    models: StatsModelBreakdown[];
    api_keys: StatsApiKeyBreakdown[];
    credentials: StatsCredentialBreakdown[];
  };
  data_quality: StatsDataQuality;
}

/** 逐请求明细只包含脱敏指标，不包含提示词、回答、Token、工具参数或原始错误体。 */
export interface StatsRequestRecord {
  id: number;
  started_at: number;
  source: StatsSource;
  requested_model: string;
  upstream_model: string | null;
  api_key_id: string | null;
  api_key_name: string | null;
  credential_id: string | null;
  credential_label: string | null;
  outcome: StatsOutcome;
  http_status: number | null;
  result_status: number | null;
  error_type: string | null;
  client_stream: boolean | null;
  thinking_mode: string | null;
  message_count: number | null;
  tool_count: number | null;
  request_bytes: number | null;
  response_bytes: number | null;
  retry_count: number | null;
  tool_call_count: number | null;
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  reasoning_tokens: number | null;
  cache_hit_tokens: number | null;
  cache_miss_tokens: number | null;
  cache_write_tokens: number | null;
  credit: number | null;
  duration_ms: number | null;
  first_event_ms: number | null;
  first_reasoning_ms: number | null;
  first_content_ms: number | null;
  first_output_ms: number | null;
}

export interface StatsRequestsResponse {
  items: StatsRequestRecord[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  snapshot_id: number;
  snapshot_time: number;
}

export interface ApiKeyRecord {
  id: string;
  name: string;
  preview: string;
  created_at: number;
  last_used_at: number | null;
}

/** `api_key` 只在创建响应中返回一次，不会出现在列表接口。 */
export interface ApiKeyCreateResponse extends ApiKeyRecord {
  api_key: string;
}

export interface CredentialRecord {
  credential_id: string;
  filename: string;
  user_id: string;
  email?: string;
  name?: string;
  created_at?: number;
  expires_at?: number;
  time_remaining?: number;
  time_remaining_str: string;
  is_expired: boolean;
  token_type: string;
  scope?: string;
  domain?: string;
  enterprise_id?: string;
  enterprise_name?: string;
  department_full_name?: string;
  account_type?: string;
  account_id?: string;
  account_count?: number;
  auth_source?: 'oauth' | 'manual';
  has_refresh_token: boolean;
  has_token: boolean;
  token_display: string;
}

export interface CredentialAccount {
  account_id: string;
  type?: string;
  nickname?: string;
  enterprise_name?: string;
  department_full_name?: string;
  is_current: boolean;
}

export interface CredentialAccountsResponse {
  accounts: CredentialAccount[];
  current_account_id: string | null;
  can_switch: boolean;
}

export interface CredentialsResponse {
  credentials: CredentialRecord[];
  current: CurrentCredential;
}

/** 后端返回的动态设置字段；新增 type 时需同步 SettingsView 的控件分支。 */
export interface SettingField {
  key: string;
  label: string;
  type: 'select' | 'tags' | 'number' | 'boolean' | 'text';
  description?: string;
  options?: string[];
  separator?: string;
  nullable?: boolean;
  min?: number;
  max?: number;
  step?: number;
}

export interface SettingsResponse {
  settings: Record<string, string | number | boolean | null>;
  fields: SettingField[];
  message?: string;
}
