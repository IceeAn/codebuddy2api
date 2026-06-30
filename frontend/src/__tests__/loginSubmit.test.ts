import { describe, expect, it, vi } from 'vitest';
import { createLoginSubmitter } from '../utils/loginSubmit';

describe('createLoginSubmitter', () => {
  function makeSubmitter() {
    const login = vi
      .fn<(username: string, password: string) => Promise<void>>()
      .mockResolvedValue(undefined);
    const onSuccess = vi.fn<() => void>();
    const onError = vi.fn<(message: string) => void>();
    const submit = createLoginSubmitter(login, onSuccess, onError);
    return { login, onSuccess, onError, submit };
  }

  it('loading 时早返回，不调用 login', async () => {
    const { login, submit } = makeSubmitter();
    await submit({ username: 'admin', password: 'pass', isLoading: true });
    expect(login).not.toHaveBeenCalled();
  });

  it('用户名空时校验失败，不调用 login，触发校验回调', async () => {
    const { login, onError, submit } = makeSubmitter();
    const ok = await submit({ username: '  ', password: 'pass', isLoading: false });
    expect(ok).toBe(false);
    expect(login).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith('请输入用户名和密码');
  });

  it('密码空时校验失败，不调用 login', async () => {
    const { login, onError, submit } = makeSubmitter();
    const ok = await submit({ username: 'admin', password: '', isLoading: false });
    expect(ok).toBe(false);
    expect(login).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith('请输入用户名和密码');
  });

  it('字段非空且非 loading 时调用 login，username 被 trim', async () => {
    const { login, onSuccess, submit } = makeSubmitter();
    const ok = await submit({ username: '  admin  ', password: 'pass', isLoading: false });
    expect(ok).toBe(true);
    expect(login).toHaveBeenCalledWith('admin', 'pass');
    await vi.waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it('login 抛错时触发 onError 并返回 false', async () => {
    const login = vi
      .fn<(username: string, password: string) => Promise<void>>()
      .mockRejectedValue(new Error('网络错误'));
    const onSuccess = vi.fn<() => void>();
    const onError = vi.fn<(message: string) => void>();
    const submit = createLoginSubmitter(login, onSuccess, onError);

    const ok = await submit({ username: 'admin', password: 'pass', isLoading: false });
    expect(ok).toBe(false);
    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith('网络错误'));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('login 抛出非 Error 时使用默认错误', async () => {
    const login = vi
      .fn<(username: string, password: string) => Promise<void>>()
      .mockRejectedValue('bad');
    const onError = vi.fn<(message: string) => void>();
    const submit = createLoginSubmitter(login, vi.fn<() => void>(), onError);

    await expect(submit({ username: 'admin', password: 'pass', isLoading: false })).resolves.toBe(
      false,
    );
    expect(onError).toHaveBeenCalledWith('登录失败');
  });
});
