/** 模型与凭证维度的调用统计，键为名称、值为当前进程内累计调用次数。 */
export interface UsageStats {
  model_usage: Record<string, number>;
  credential_usage: Record<string, number>;
}

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
  usage: UsageStats;
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
  has_refresh_token: boolean;
  has_token: boolean;
  token_display: string;
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
