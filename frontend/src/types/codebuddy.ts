import type { CurrentCredential } from './admin';

export interface ModelListResponse {
  data: Array<{ id: string }>;
}

/**
 * OpenAI 兼容的 Chat Completion 请求体。
 *
 * 显式列出管理台会用到的字段，并通过索引签名允许客户端透传上游扩展参数。
 */
export interface ChatCompletionRequest {
  model: string;
  messages: Array<{
    role: string;
    content: string;
    tool_call_id?: string;
    tool_calls?: unknown;
  }>;
  stream?: boolean;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  n?: number;
  stop?: string | string[];
  thinking?: { type?: string; [key: string]: unknown };
  reasoning_effort?: string;
  enable_thinking?: boolean;
  [key: string]: unknown;
}

/** CodeBuddy OAuth 授权成功后仅确认凭证已安全落盘。 */
export interface CodeBuddyPollAuthResponse {
  saved?: boolean;
  message?: string;
}

export interface DeleteCredentialResponse {
  deleted: boolean;
  current: CurrentCredential;
}
