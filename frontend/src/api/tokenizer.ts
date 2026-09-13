import { apiRequest } from './client';

export interface BuiltinTokenizer {
  id: string;
  model: string;
  repo: string;
  revision: string;
  encoder: string;
  license: string;
}
export interface UserTokenizer {
  id: string;
  name: string;
  revision: string;
  profile: { encoder: string; method?: string };
  origin: { id: string; revision: string } | null;
  update_available: boolean;
}
export interface TokenizerResources {
  builtin_resources: BuiltinTokenizer[];
  pending_models: string[];
  user_resources: UserTokenizer[];
  mappings: Record<string, string>;
  encoders: string[];
  limits: Record<string, number>;
}
export interface CountResult {
  input_tokens: number;
  method: string | null;
  source: string | null;
  revision: string | null;
}
const root = '/api/admin/tokenizers';

export const tokenizerApi = {
  resources: () => apiRequest<TokenizerResources>(root),
  upload: (form: FormData, timeoutMs: number) =>
    apiRequest<UserTokenizer>(`${root}/resources`, { method: 'POST', body: form, timeoutMs }),
  snapshot: (id: string) =>
    apiRequest<UserTokenizer>(`${root}/builtin/${encodeURIComponent(id)}/snapshot`, {
      method: 'POST',
    }),
  delete: (id: string) =>
    apiRequest(`${root}/resources/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  mappings: (mappings: Record<string, string>) =>
    apiRequest<TokenizerResources>(`${root}/mappings`, { method: 'PUT', json: { mappings } }),
  async count(
    protocol: 'tokenizer' | 'anthropic',
    body: unknown,
    timeoutMs: number,
    signal?: AbortSignal,
  ): Promise<CountResult> {
    let metadata: Pick<CountResult, 'method' | 'source' | 'revision'>;
    const result = await apiRequest<{ input_tokens: number }>(
      `/api/admin/playground/${protocol}/v1/${protocol === 'anthropic' ? 'messages/' : ''}count_tokens`,
      {
        method: 'POST',
        json: body,
        timeoutMs,
        signal,
        headers: { 'anthropic-version': '2023-06-01' },
        onResponse(response) {
          metadata = {
            method: response.headers.get('X-Tokenizer-Method'),
            source: response.headers.get('X-Tokenizer-Source'),
            revision: response.headers.get('X-Tokenizer-Revision'),
          };
        },
      },
    );
    return { ...result, ...metadata! };
  },
};

/** 预留排队、隔离进程执行及传输处理时间。 */
export function tokenizerTimeout(data: TokenizerResources): number {
  return (data.limits.queue_timeout_seconds + data.limits.timeout_seconds + 30) * 1000;
}
