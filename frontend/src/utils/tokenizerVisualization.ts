import type { TokenSpan } from '../api/tokenizer';

const colors = [
  '#a78bfa',
  '#34d399',
  '#fb923c',
  '#38bdf8',
  '#f472b6',
  '#facc15',
  '#818cf8',
  '#2dd4bf',
];

export function tokenColor(index: number): string {
  return `color-mix(in oklab, ${colors[index % colors.length]} 38%, var(--surface))`;
}

interface TextSegment {
  text: string;
  background: string;
  title: string;
}

/** 按字素绘制文字，按 UTF-8 字节相交比例绘制硬边界背景；不解码单个 token。 */
export function tokenSegments(text: string, tokens: TokenSpan[]): TextSegment[] {
  const segments: TextSegment[] = [];
  const encoder = new TextEncoder();
  const graphemes = new Intl.Segmenter('zh', { granularity: 'grapheme' });
  let offset = 0;
  let tokenIndex = 0;
  for (const { segment } of graphemes.segment(text)) {
    const size = encoder.encode(segment).length;
    const end = offset + size;
    let position = offset;
    const parts: { color: string; start: number; end: number; label: string; bytes: number }[] = [];
    while (position < end) {
      while (tokenIndex < tokens.length && tokens[tokenIndex].end <= position) tokenIndex++;
      const token = tokens[tokenIndex];
      const covered = token && token.start <= position;
      const boundary = token ? Math.min(end, covered ? token.end : token.start) : end;
      parts.push({
        color: covered ? tokenColor(tokenIndex) : 'transparent',
        start: ((position - offset) / size) * 100,
        end: ((boundary - offset) / size) * 100,
        label: covered ? `Token ${tokenIndex + 1} · ID ${token.id}` : '未编码的文本',
        bytes: boundary - position,
      });
      position = boundary;
    }
    const single = parts.length === 1;
    const background = single
      ? parts[0].color
      : `linear-gradient(to right, ${parts
          .map((part) => `${part.color} ${part.start}% ${part.end}%`)
          .join(', ')})`;
    const title = parts
      .map((part) => (single ? part.label : `${part.label}（${part.bytes} 字节/${size} 字节）`))
      .join('\n');
    const previous = segments.at(-1);
    if (single && previous?.background === background && previous.title === title) {
      previous.text += segment;
    } else {
      segments.push({ text: segment, background, title });
    }
    offset = end;
  }
  return segments;
}
