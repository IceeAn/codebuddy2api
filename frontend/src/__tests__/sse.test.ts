import { describe, expect, it } from 'vitest';
import { parseSsePayload, SseStreamDecoder } from '../utils/sse';

describe('parseSsePayload', () => {
  it('解析 data-only SSE 并忽略 DONE 与坏 JSON', () => {
    const events = parseSsePayload(
      'data: {"choices":[{"delta":{"content":"A"}}]}\n\n' +
        'data: not-json\n\n' +
        'data: [DONE]\n\n',
    );

    expect(events).toEqual([{ choices: [{ delta: { content: 'A' } }] }]);
  });

  it('处理完整字符串中的多事件', () => {
    const events = parseSsePayload('data: {"a":1}\n\ndata: {"b":2}\n\n');
    expect(events).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('data 字段跨多行时用换行拼接为完整 JSON', () => {
    // SSE 规范：连续多个 data: 行用 \n 拼接成完整 payload。
    // JSON 允许键值之间的空白包含换行，故拼接后仍为合法 JSON。
    const events = parseSsePayload('data: {"a":\ndata: 1}\n\n');
    expect(events).toEqual([{ a: 1 }]);
  });

  it('过滤 [DONE]', () => {
    const events = parseSsePayload('data: [DONE]\n\n');
    expect(events).toEqual([]);
  });

  it('忽略坏 JSON 不抛错', () => {
    const events = parseSsePayload('data: {bad\n\n');
    expect(events).toEqual([]);
  });
});

describe('SseStreamDecoder', () => {
  it('跨 chunk 的事件不丢失', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data: {"cho')).toEqual([]);
    expect(decoder.feed('ices":[]}\n\n')).toEqual([{ choices: [] }]);
  });

  it('多事件分块传输', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data: {"a":1}\n\nda')).toEqual([{ a: 1 }]);
    expect(decoder.feed('ta: {"b":2}\n\n')).toEqual([{ b: 2 }]);
  });

  it('空行分隔多事件', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: {"x":1}\n\ndata: {"y":2}\n\n');
    expect(events).toEqual([{ x: 1 }, { y: 2 }]);
  });

  it('data 字段跨多行用换行拼接为完整 JSON', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: {"a":\ndata: 1}\n\n');
    expect(events).toEqual([{ a: 1 }]);
  });

  it('过滤 [DONE]', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: [DONE]\n\n');
    expect(events).toEqual([]);
  });

  it('忽略坏 JSON 不抛错', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: {bad\n\n');
    expect(events).toEqual([]);
  });

  it('单个 chunk 内可包含多个完整事件', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: {"a":1}\n\ndata: {"b":2}\n\ndata: {"c":3}\n\n');
    expect(events).toEqual([{ a: 1 }, { b: 2 }, { c: 3 }]);
  });

  it('不完整事件保留在 buffer 等待后续 chunk', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('data: {"a":1}\n\ndata: {"b":')).toEqual([{ a: 1 }]);
    expect(decoder.feed('2}\n\n')).toEqual([{ b: 2 }]);
  });

  it('解析后 JSON 字符串 payload 返回原始字符串', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: "hello"\n\n');
    expect(events).toEqual(['hello']);
  });

  it('解析数字 payload 返回数字', () => {
    const decoder = new SseStreamDecoder();
    const events = decoder.feed('data: 42\n\n');
    expect(events).toEqual([42]);
  });

  it('忽略没有 data 字段的 SSE 事件并支持无空格 data 前缀', () => {
    const decoder = new SseStreamDecoder();
    expect(decoder.feed('event: ping\n\n')).toEqual([]);
    expect(decoder.feed('data:{"ok":true}\n\n')).toEqual([{ ok: true }]);
  });
});
