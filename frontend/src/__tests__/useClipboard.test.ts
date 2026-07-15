import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// useClipboard 内部调用 useToast()，用 hoisted mock 让测试能断言 toast 调用。
const { toastMock } = vi.hoisted(() => ({
  toastMock: {
    success: vi.fn<(message: string, duration?: number) => void>(),
    error: vi.fn<(message: string, duration?: number) => void>(),
  },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

import { useClipboard } from '../composables/useClipboard';

/**
 * 构造一个最小 document mock，用于 fallback 路径测试。
 * 返回 textarea、body 引用以便断言交互。
 */
function stubDocument(execCommandResult: boolean) {
  const previousFocus = { focus: vi.fn<() => void>() };
  const textarea = {
    value: '',
    style: {} as Record<string, string>,
    select: vi.fn<() => void>(),
  };
  const body = {
    appendChild: vi.fn<(node: typeof textarea) => typeof textarea>(),
    removeChild: vi.fn<(node: typeof textarea) => typeof textarea>(),
  };
  const documentMock = {
    createElement: vi.fn<(tagName: 'textarea') => typeof textarea>(() => textarea),
    execCommand: vi.fn<(commandId: string) => boolean>(() => execCommandResult),
    activeElement: previousFocus,
    body,
  };
  vi.stubGlobal('document', documentMock);
  return { textarea, body, documentMock, previousFocus };
}

describe('useClipboard', () => {
  beforeEach(() => {
    toastMock.success.mockReset();
    toastMock.error.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('clipboard 可用时调用 writeText 成功，返回 true', async () => {
    const writeText = vi.fn<Clipboard['writeText']>().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    const { copy } = useClipboard();
    const result = await copy('hello');

    expect(result).toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
    expect(toastMock.success).toHaveBeenCalledWith('已复制');
  });

  it('clipboard 不可用时 fallback 到 execCommand，成功返回 true', async () => {
    vi.stubGlobal('navigator', {});
    const { textarea, body } = stubDocument(true);

    const { copy } = useClipboard();
    const result = await copy('some text');

    expect(result).toBe(true);
    expect(textarea.value).toBe('some text');
    expect(textarea.style.position).toBe('fixed');
    expect(body.appendChild).toHaveBeenCalledWith(textarea);
    expect(body.removeChild).toHaveBeenCalledWith(textarea);
    expect(toastMock.success).toHaveBeenCalledWith('已复制');
  });

  it('execCommand 失败时返回 false 并不抛错', async () => {
    vi.stubGlobal('navigator', {});
    const { textarea, body, previousFocus } = stubDocument(false);

    const { copy } = useClipboard();
    const result = await copy('x');

    expect(result).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith('复制失败');
    expect(body.removeChild).toHaveBeenCalledWith(textarea);
    expect(previousFocus.focus).toHaveBeenCalled();
  });

  it('writeText 抛错时返回 false', async () => {
    const writeText = vi.fn<Clipboard['writeText']>().mockRejectedValue(new Error('network'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    const { copy } = useClipboard();
    const result = await copy('x');

    expect(result).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith('network');
  });

  it('非 Error 异常使用通用失败消息', async () => {
    const writeText = vi.fn<Clipboard['writeText']>().mockRejectedValue('bad');
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    const { copy } = useClipboard();
    await expect(copy('x')).resolves.toBe(false);

    expect(toastMock.error).toHaveBeenCalledWith('复制失败');
  });

  it('成功后 copied 在 2 秒内为 true，之后归零', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn<Clipboard['writeText']>().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    const { copy, copied } = useClipboard();
    await copy('x');

    expect(copied.value).toBe(true);
    vi.advanceTimersByTime(1999);
    expect(copied.value).toBe(true);
    vi.advanceTimersByTime(1);
    expect(copied.value).toBe(false);
  });

  it('支持自定义成功消息', async () => {
    const writeText = vi.fn<Clipboard['writeText']>().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    const { copy } = useClipboard();
    await copy('x', '链接已复制');

    expect(toastMock.success).toHaveBeenCalledWith('链接已复制');
  });

  it('后一次复制会取消前一次 copied 复位计时器', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn<Clipboard['writeText']>().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const { copy, copied } = useClipboard();

    await copy('first');
    vi.advanceTimersByTime(1000);
    await copy('second');
    vi.advanceTimersByTime(1000);
    expect(copied.value).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(copied.value).toBe(false);
  });

  it('fallback 挂载临时节点失败时不尝试移除但仍恢复焦点', async () => {
    vi.stubGlobal('navigator', {});
    const { body, previousFocus } = stubDocument(true);
    body.appendChild.mockImplementation(() => {
      throw new Error('无法挂载');
    });
    const { copy } = useClipboard();
    await expect(copy('x')).resolves.toBe(false);
    expect(body.removeChild).not.toHaveBeenCalled();
    expect(previousFocus.focus).toHaveBeenCalled();
  });
});
