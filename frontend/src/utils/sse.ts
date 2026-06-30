/**
 * SSE 流式解码器。
 *
 * SSE 规范：事件之间以空行（`\n\n`）分隔；单个事件的 `data:` 字段可跨多行，
 * 解析时需将这些行用 `\n` 拼接成完整 payload。
 *
 * 网络传输中一个 `reader.read()` 返回的 chunk 可能包含半个事件、
 * 一个完整事件或多个事件，因此必须维护跨 chunk 的内部缓冲区。
 */
export class SseStreamDecoder {
  private buffer = '';

  /**
   * 追加 chunk 到内部缓冲区，按 `\n\n` 切出所有已完整的事件并返回解析结果。
   *
   * 不完整的事件会留在缓冲区中，等待后续 feed 调用拼接。
   *
   * @param chunk 本次读取到的文本片段
   * @returns 本次 feed 产生的已解析事件数组（JSON 解析失败的 payload 会被忽略）
   */
  feed(chunk: string): unknown[] {
    this.buffer += chunk;
    const events: unknown[] = [];
    let idx: number;
    while ((idx = this.buffer.indexOf('\n\n')) >= 0) {
      const raw = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const payload = extractDataPayload(raw);
      if (payload === null || payload === '[DONE]') continue;
      try {
        events.push(JSON.parse(payload));
      } catch {
        // 忽略坏 JSON，避免单条事件破坏整条流
      }
    }
    return events;
  }
}

/**
 * 从单个完整事件（不含尾部空行）的原始文本中提取 data payload。
 *
 * 按行扫描所有以 `data:` 开头的行，去掉前缀和一个可选的首空格后，
 * 用 `\n` 拼接成完整 payload（符合 SSE 规范）。
 *
 * @returns 拼接后的 payload 字符串；若该事件无 data 行则返回 null
 */
function extractDataPayload(rawEvent: string): string | null {
  const lines = rawEvent.split('\n');
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('data:')) {
      // 去掉 "data:" 前缀，再去掉一个可选的首空格（"data: x" → "x"，"data:x" → "x"）
      const rest = line.slice(5);
      dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
    }
  }
  if (dataLines.length === 0) return null;
  return dataLines.join('\n');
}

/** 一次性解析完整 SSE 文本；流式场景直接使用 SseStreamDecoder。 */
export function parseSsePayload(buffer: string): unknown[] {
  const decoder = new SseStreamDecoder();
  return decoder.feed(buffer);
}
