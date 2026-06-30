import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useToast } from '../composables/useToast';
import { useToastStore } from '../stores/toast';

describe('useToast', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('success(msg) 调用 store.push("success", msg)', () => {
    const store = useToastStore();
    const pushSpy = vi.spyOn(store, 'push');

    const toast = useToast();
    toast.success('done');

    expect(pushSpy).toHaveBeenCalledWith('success', 'done', undefined);
    expect(store.toasts[0].type).toBe('success');
    expect(store.toasts[0].message).toBe('done');
  });

  it('error(msg) 调用 store.push("error", msg)', () => {
    const store = useToastStore();
    const pushSpy = vi.spyOn(store, 'push');

    const toast = useToast();
    toast.error('failed');

    expect(pushSpy).toHaveBeenCalledWith('error', 'failed', undefined);
    expect(store.toasts[0].type).toBe('error');
  });

  it('warning(msg) 调用 store.push("warning", msg)', () => {
    const store = useToastStore();
    const pushSpy = vi.spyOn(store, 'push');

    const toast = useToast();
    toast.warning('careful');

    expect(pushSpy).toHaveBeenCalledWith('warning', 'careful', undefined);
    expect(store.toasts[0].type).toBe('warning');
  });

  it('info(msg) 调用 store.push("info", msg)', () => {
    const store = useToastStore();
    const pushSpy = vi.spyOn(store, 'push');

    const toast = useToast();
    toast.info('notice');

    expect(pushSpy).toHaveBeenCalledWith('info', 'notice', undefined);
    expect(store.toasts[0].type).toBe('info');
  });

  it('传入 duration 参数透传给 store.push', () => {
    const store = useToastStore();
    const pushSpy = vi.spyOn(store, 'push');

    const toast = useToast();
    toast.success('msg', 7000);

    expect(pushSpy).toHaveBeenCalledWith('success', 'msg', 7000);
    expect(store.toasts[0].duration).toBe(7000);
  });
});
