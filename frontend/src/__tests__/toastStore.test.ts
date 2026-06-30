import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useToastStore } from '../stores/toast';
import type { ToastType } from '../stores/toast';

describe('useToastStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("push('success', 'msg') 添加一条 toast，类型和消息正确", () => {
    const store = useToastStore();
    store.push('success', '操作成功');

    expect(store.toasts).toHaveLength(1);
    expect(store.toasts[0].type).toBe('success');
    expect(store.toasts[0].message).toBe('操作成功');
    expect(store.toasts[0].id).toBeTruthy();
  });

  it.each([
    ['success', 3000],
    ['info', 4000],
    ['warning', 5000],
    ['error', 5000],
  ] as [ToastType, number][])('默认时长按类型不同：%s 为 %ims', (type, expected) => {
    const store = useToastStore();
    store.push(type, 'msg');

    expect(store.toasts[0].duration).toBe(expected);
  });

  it('自定义 duration 覆盖默认值', () => {
    const store = useToastStore();
    store.push('success', 'msg', 8000);

    expect(store.toasts[0].duration).toBe(8000);
  });

  it('duration=0 时不设置自动消失（不会自动 remove）', () => {
    const store = useToastStore();
    store.push('success', 'msg', 0);

    expect(store.toasts).toHaveLength(1);
    vi.advanceTimersByTime(10_000);
    expect(store.toasts).toHaveLength(1);
  });

  it('超时后自动 remove', () => {
    const store = useToastStore();
    store.push('success', 'msg');

    expect(store.toasts).toHaveLength(1);
    vi.advanceTimersByTime(3000);
    expect(store.toasts).toHaveLength(0);
  });

  it('remove(id) 移除指定 toast', () => {
    const store = useToastStore();
    store.push('success', 'first');
    store.push('error', 'second');

    const firstId = store.toasts[0].id;
    store.remove(firstId);

    expect(store.toasts).toHaveLength(1);
    expect(store.toasts[0].message).toBe('second');
  });

  it('remove(不存在的 id) 不报错、不影响其他 toast', () => {
    const store = useToastStore();
    store.push('success', 'first');

    store.remove('nonexistent-id');

    expect(store.toasts).toHaveLength(1);
    expect(store.toasts[0].message).toBe('first');
  });

  it('超过 MAX_TOASTS(5) 时移除最早的', () => {
    const store = useToastStore();
    // duration=0 避免自动消失干扰
    store.push('success', 'msg-1', 0);
    store.push('success', 'msg-2', 0);
    store.push('success', 'msg-3', 0);
    store.push('success', 'msg-4', 0);
    store.push('success', 'msg-5', 0);
    store.push('success', 'msg-6', 0);

    expect(store.toasts).toHaveLength(5);
    expect(store.toasts[0].message).toBe('msg-2');
    expect(store.toasts[4].message).toBe('msg-6');
  });

  it('clear() 清空所有 toast', () => {
    const store = useToastStore();
    store.push('success', 'a', 0);
    store.push('error', 'b', 0);
    store.push('info', 'c', 0);

    store.clear();

    expect(store.toasts).toHaveLength(0);
  });

  it('多条 toast 各自独立计时', () => {
    const store = useToastStore();
    store.push('success', 'short', 1000);
    store.push('error', 'long', 5000);

    vi.advanceTimersByTime(1000);
    expect(store.toasts).toHaveLength(1);
    expect(store.toasts[0].message).toBe('long');

    vi.advanceTimersByTime(4000);
    expect(store.toasts).toHaveLength(0);
  });

  it('pauseAll() 暂停自动移除，resumeAll() 按剩余时间继续', () => {
    const store = useToastStore();
    store.push('success', 'msg', 3000);

    vi.advanceTimersByTime(1000);
    store.pauseAll();
    expect(store.isPaused).toBe(true);

    vi.advanceTimersByTime(10_000);
    expect(store.toasts).toHaveLength(1);

    store.resumeAll();
    expect(store.isPaused).toBe(false);
    vi.advanceTimersByTime(1999);
    expect(store.toasts).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(store.toasts).toHaveLength(0);
  });

  it('暂停期间新增的 toast 也不会自动移除，恢复后再开始计时', () => {
    const store = useToastStore();
    store.pauseAll();
    store.push('success', 'paused-new', 1000);

    vi.advanceTimersByTime(5000);
    expect(store.toasts).toHaveLength(1);

    store.resumeAll();
    vi.advanceTimersByTime(1000);
    expect(store.toasts).toHaveLength(0);
  });

  it('pauseAll() 与 resumeAll() 重复调用保持幂等', () => {
    const store = useToastStore();

    store.pauseAll();
    store.pauseAll();
    expect(store.isPaused).toBe(true);

    store.resumeAll();
    store.resumeAll();
    expect(store.isPaused).toBe(false);
  });

  it('恢复时剩余时间已为 0 的 toast 会立即移除', () => {
    const store = useToastStore();
    store.push('success', 'expired-while-pausing', 1000);

    vi.setSystemTime(Date.now() + 1500);
    store.pauseAll();
    expect(store.toasts).toHaveLength(1);

    store.resumeAll();
    expect(store.toasts).toHaveLength(0);
  });

  it('clear() 清理计时器并重置暂停状态', () => {
    const store = useToastStore();
    store.push('success', 'timed', 1000);
    store.pauseAll();

    store.clear();

    expect(store.isPaused).toBe(false);
    expect(store.toasts).toHaveLength(0);
    vi.advanceTimersByTime(1000);
    expect(store.toasts).toHaveLength(0);
  });
});
