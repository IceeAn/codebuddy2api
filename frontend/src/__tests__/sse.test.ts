import { describe, expect, it } from 'vitest';
import { parseSsePayload, SseStreamDecoder } from '../utils/sse';

describe('parseSsePayload', () => {
  it('区分消息、DONE 和坏 JSON 协议错误', () => {
    expect(
      parseSsePayload(
        'data: {"choices":[{"delta":{"content":"A"}}]}\n\n' +
          'data: not-json\n\n' +
          'data: [DONE]\n\n',
      ),
    ).toEqual([
      {
        type: 'message',
        data: { choices: [{ delta: { content: 'A' } }] },
      },
      { type: 'error', message: 'SSE data 不是有效 JSON' },
      { type: 'done' },
    ]);
  });

  it('识别上游错误信封而不是把它当成成功消息', () => {
    expect(
      parseSsePayload(
        'data: {"error":{"message":"额度不足"}}\n\n' +
          'data: {"error":"服务不可用"}\n\n' +
          'data: {"error":{"code":"upstream_error"}}\n\n',
      ),
    ).toEqual([
      { type: 'error', message: '额度不足' },
      { type: 'error', message: '服务不可用' },
      { type: 'error', message: '上游返回流式错误' },
    ]);
  });
});

describe('SseStreamDecoder', () => {
  it('跨 chunk 保留半个事件并支持多个完整事件', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data: {"cho')).toEqual([]);
    expect(decoder.feed('ices":[]}\n\ndata: {"b":2}\n\n')).toEqual([
      { type: 'message', data: { choices: [] } },
      { type: 'message', data: { b: 2 } },
    ]);
  });

  it('支持 CRLF、混合空行和跨 chunk 的 CRLF', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data: {"a":1}\r')).toEqual([]);
    expect(decoder.feed('\n\r\ndata: {"b":2}\r\n\n')).toEqual([
      { type: 'message', data: { a: 1 } },
      { type: 'message', data: { b: 2 } },
    ]);
  });

  it('多行 data 用换行拼接并允许没有首空格', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data:{"a":\ndata: 1}\n\n')).toEqual([{ type: 'message', data: { a: 1 } }]);
  });

  it('保留合法 JSON 标量消息并忽略没有 data 的事件', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('event: ping\n\ndata: "hello"\n\ndata: 42\n\n')).toEqual([
      { type: 'message', data: 'hello' },
      { type: 'message', data: 42 },
    ]);
  });

  it('保留 Anthropic 具名事件并继续识别错误信封', () => {
    const decoder = new SseStreamDecoder();
    expect(
      decoder.feed(
        'event: content_block_delta\n' +
          'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"A"}}\n\n' +
          'event: error\n' +
          'data: {"type":"error","error":{"type":"api_error","message":"failed"}}\n\n',
      ),
    ).toEqual([
      {
        type: 'message',
        event: 'content_block_delta',
        data: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'A' } },
      },
      { type: 'error', message: 'failed' },
    ]);
  });

  it('忽略空 event 名称', () => {
    expect(parseSsePayload('event:   \ndata: {"ok":true}\n\n')).toEqual([
      { type: 'message', data: { ok: true } },
    ]);
  });

  it('finish 报告被截断的事件且清空缓冲', () => {
    const decoder = new SseStreamDecoder();
    decoder.feed('data: {"partial":');

    expect(decoder.finish()).toEqual([{ type: 'error', message: 'SSE 流在事件结束前中断' }]);
    expect(decoder.finish()).toEqual([]);
  });

  it('finish 忽略仅含空白或注释的尾部内容', () => {
    const decoder = new SseStreamDecoder();
    decoder.feed(': keep-alive\r\n  ');
    expect(decoder.finish()).toEqual([]);
  });
});
