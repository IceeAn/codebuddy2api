export type SseDecodeResult =
  | { type: 'message'; data: unknown; event?: string }
  | { type: 'done' }
  | { type: 'error'; message: string };

const EVENT_SEPARATOR = /(?:\r\n|\r|\n)(?:\r\n|\r|\n)/;

/**
 * 增量 SSE 解码器。网络 chunk、UTF-8 解码和 SSE 事件边界彼此独立，调用方应先用
 * `TextDecoder.decode(value, { stream: true })` 解码字节，再把文本交给本类。
 */
export class SseStreamDecoder {
  private buffer = '';

  feed(chunk: string): SseDecodeResult[] {
    this.buffer += chunk;
    const results: SseDecodeResult[] = [];

    while (true) {
      const separator = EVENT_SEPARATOR.exec(this.buffer);
      if (!separator) break;

      const rawEvent = this.buffer.slice(0, separator.index);
      this.buffer = this.buffer.slice(separator.index + separator[0].length);
      const result = decodeEvent(rawEvent);
      if (result) results.push(result);
    }

    return results;
  }

  /** 流结束时报告尚未以空行结束的 data 事件，避免把截断响应当成成功。 */
  finish(): SseDecodeResult[] {
    const tail = this.buffer;
    this.buffer = '';
    if (extractDataPayload(tail) === null) return [];
    return [{ type: 'error', message: 'SSE 流在事件结束前中断' }];
  }
}

function decodeEvent(rawEvent: string): SseDecodeResult | null {
  const payload = extractDataPayload(rawEvent);
  if (payload === null) return null;
  if (payload.trim() === '[DONE]') return { type: 'done' };

  let data: unknown;
  try {
    data = JSON.parse(payload);
  } catch {
    return { type: 'error', message: 'SSE data 不是有效 JSON' };
  }

  if (isRecord(data) && Object.prototype.hasOwnProperty.call(data, 'error')) {
    return { type: 'error', message: describeErrorEnvelope(data.error) };
  }
  const event = extractEventName(rawEvent);
  return event ? { type: 'message', event, data } : { type: 'message', data };
}

function extractEventName(rawEvent: string): string | undefined {
  for (const line of rawEvent.split(/\r\n|\r|\n/)) {
    if (!line.startsWith('event:')) continue;
    const value = line.slice(6).trim();
    if (value) return value;
  }
  return undefined;
}

function describeErrorEnvelope(error: unknown): string {
  if (typeof error === 'string' && error.trim()) return error.trim();
  if (isRecord(error) && typeof error.message === 'string' && error.message.trim()) {
    return error.message.trim();
  }
  return '上游返回流式错误';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function extractDataPayload(rawEvent: string): string | null {
  const dataLines: string[] = [];
  for (const line of rawEvent.split(/\r\n|\r|\n/)) {
    if (!line.startsWith('data:')) continue;
    const rest = line.slice(5);
    dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
  }
  return dataLines.length > 0 ? dataLines.join('\n') : null;
}

/** 一次性解析完整 SSE 文本；流式场景直接使用 SseStreamDecoder。 */
export function parseSsePayload(buffer: string): SseDecodeResult[] {
  const decoder = new SseStreamDecoder();
  return [...decoder.feed(buffer), ...decoder.finish()];
}
