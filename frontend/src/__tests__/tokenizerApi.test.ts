import { afterEach, describe, expect, it, vi } from 'vitest';
import { tokenizerApi } from '../api/tokenizer';

const response = { input_tokens: 3 };
afterEach(() => vi.unstubAllGlobals());
describe('本地计数接口', () => {
  it('保留计数元数据并使用会话测试入口', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(response), {
        headers: {
          'content-type': 'application/json',
          'X-Tokenizer-Method': 'budget_v1',
          'X-Tokenizer-Source': 'user',
          'X-Tokenizer-Revision': 'abc',
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    expect(await tokenizerApi.count('tokenizer', { model: 'local', text: '你好' }, 75000)).toEqual({
      ...response,
      method: 'budget_v1',
      source: 'user',
      revision: 'abc',
    });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/admin/playground/tokenizer/v1/count_tokens');
  });
});

it('资源 CRUD、快照及 Anthropic 计数保持正确请求格式', async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementation(
      async () => new Response('{}', { headers: { 'content-type': 'application/json' } }),
    );
  vi.stubGlobal('fetch', fetchMock);
  await tokenizerApi.resources();
  const form = new FormData();
  form.append('name', '词表');
  await tokenizerApi.upload(form, 90000);
  await tokenizerApi.snapshot('a/b');
  await tokenizerApi.delete('a/b');
  await tokenizerApi.mappings({ m: 'id' });
  expect(await tokenizerApi.count('anthropic', {}, 90000)).toEqual({
    method: null,
    source: null,
    revision: null,
  });
  expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
    '/api/admin/tokenizers',
    '/api/admin/tokenizers/resources',
    '/api/admin/tokenizers/builtin/a%2Fb/snapshot',
    '/api/admin/tokenizers/resources/a%2Fb',
    '/api/admin/tokenizers/mappings',
    '/api/admin/playground/anthropic/v1/messages/count_tokens',
  ]);
  expect(fetchMock.mock.calls[1][1]?.body).toBe(form);
  expect(new Headers(fetchMock.mock.calls[1][1]?.headers).has('content-type')).toBe(false);
});

it('可视化使用管理端分词接口，并保留取消信号和总超时', async () => {
  const result = {
    text: '你',
    input_tokens: 2,
    tokens: [
      { id: 1, start: 0, end: 2 },
      { id: 2, start: 2, end: 3 },
    ],
  };
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(result), {
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const controller = new AbortController();
  expect(
    await tokenizerApi.encode({ model: 'local', text: '你' }, 90000, controller.signal),
  ).toEqual(result);
  expect(fetchMock.mock.calls[0][0]).toBe('/api/admin/tokenizers/encode');
  expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({
    model: 'local',
    text: '你',
  });
});
