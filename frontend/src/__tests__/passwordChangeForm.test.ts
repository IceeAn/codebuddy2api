import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';

const {
  changePasswordMock,
  clearCacheMock,
  finishPasswordChangeMock,
  replaceMock,
  sessionMock,
  toastMock,
} = vi.hoisted(() => {
  const finishPasswordChangeMock = vi.fn<(username: string, message: string) => void>();
  return {
    changePasswordMock:
      vi.fn<(currentPassword: string | undefined, newPassword: string) => Promise<unknown>>(),
    clearCacheMock: vi.fn<() => void>(),
    finishPasswordChangeMock,
    replaceMock: vi.fn<(target: string) => Promise<void>>(),
    sessionMock: {
      username: 'alice',
      finishPasswordChange: finishPasswordChangeMock,
    },
    toastMock: {
      success: vi.fn<(message: string) => void>(),
      error: vi.fn<(message: string) => void>(),
      warning: vi.fn<(message: string) => void>(),
      info: vi.fn<(message: string) => void>(),
    },
  };
});

vi.mock('../api/admin', () => ({
  authApi: { changePassword: changePasswordMock },
}));
vi.mock('../stores/session', () => ({
  useSessionStore: () => sessionMock,
}));
vi.mock('@tanstack/vue-query', () => ({
  useQueryClient: () => ({ clear: clearCacheMock }),
}));
vi.mock('../utils/chunkLoadRecovery', () => ({
  chunkLoadRecovery: { replace: replaceMock },
}));
vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

import PasswordChangeForm from '../components/PasswordChangeForm.vue';

const mountedWrappers: ReturnType<typeof mount>[] = [];

function mountForm(forced = false) {
  const wrapper = mount(PasswordChangeForm, {
    props: { forced },
    attachTo: document.body,
    global: {
      stubs: {
        KeyRound: true,
      },
    },
  });
  mountedWrappers.push(wrapper);
  return wrapper;
}

async function fill(wrapper: ReturnType<typeof mountForm>, values: string[]) {
  const inputs = wrapper.findAll('input');
  for (let index = 0; index < values.length; index += 1) {
    await inputs[index].setValue(values[index]);
  }
  return inputs;
}

describe('PasswordChangeForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionMock.username = 'alice';
    changePasswordMock.mockResolvedValue({ password_changed: true, authenticated: false });
    replaceMock.mockResolvedValue(undefined);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
  });

  it('普通模式显示只读用户名和三个带自动完成语义的密码框', () => {
    const wrapper = mountForm();
    const inputs = wrapper.findAll('input');

    expect(inputs).toHaveLength(4);
    expect(inputs[0].attributes('readonly')).toBeDefined();
    expect((inputs[0].element as HTMLInputElement).value).toBe('alice');
    expect(inputs[1].attributes('autocomplete')).toBe('current-password');
    expect(inputs[2].attributes('autocomplete')).toBe('new-password');
    expect(inputs[3].attributes('autocomplete')).toBe('new-password');
    expect(wrapper.text()).toContain('所有管理台会话都会退出');
  });

  it('本地拒绝无效、复用和确认不一致的新密码并聚焦约定字段', async () => {
    const wrapper = mountForm();
    const inputs = await fill(wrapper, ['alice', 'old-password', 'short', 'short']);
    await wrapper.get('[data-submit-password]').trigger('click');
    expect(changePasswordMock).not.toHaveBeenCalled();
    expect(wrapper.findAll('[data-new-password-error]')).toHaveLength(2);
    expect(document.activeElement).toBe(inputs[2].element);

    await inputs[2].setValue('old-password');
    await inputs[3].setValue('old-password');
    await wrapper.get('[data-submit-password]').trigger('click');
    expect(wrapper.text()).toContain('新密码不能与当前密码相同');
    expect(document.activeElement).toBe(inputs[2].element);

    await inputs[2].setValue('new-password');
    await inputs[3].setValue('different-password');
    await wrapper.get('[data-submit-password]').trigger('click');
    expect(wrapper.text()).toContain('两次输入的新密码不一致');
    expect(document.activeElement).toBe(inputs[3].element);
  });

  it('普通模式要求当前密码，并拒绝控制字符和超长新密码', async () => {
    const wrapper = mountForm();
    const inputs = wrapper.findAll('input');
    await inputs[2].setValue('new-password');
    await inputs[3].setValue('new-password');
    await wrapper.get('[data-submit-password]').trigger('click');
    expect(wrapper.text()).toContain('请输入当前密码');

    await inputs[1].setValue('old-password');
    for (const invalid of ['valid123\u007f', '密'.repeat(129)]) {
      await inputs[2].setValue(invalid);
      await inputs[3].setValue(invalid);
      await wrapper.get('[data-submit-password]').trigger('click');
      expect(wrapper.text()).toContain('不能包含控制字符');
    }
    expect(changePasswordMock).not.toHaveBeenCalled();
  });

  it('普通模式改密成功后清缓存、退出本地状态并替换到总览', async () => {
    const wrapper = mountForm();
    await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);

    await wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() =>
      expect(changePasswordMock).toHaveBeenCalledWith('old-password', 'new-password'),
    );

    expect(clearCacheMock).toHaveBeenCalledOnce();
    expect(finishPasswordChangeMock).toHaveBeenCalledWith(
      'alice',
      '密码已修改，请使用新密码重新登录',
    );
    expect(replaceMock).toHaveBeenCalledWith('/dashboard');
  });

  it('强制模式不显示当前密码并以专用按钮提交', async () => {
    const wrapper = mountForm(true);
    const inputs = await fill(wrapper, ['alice', 'new-password', 'new-password']);

    expect(inputs).toHaveLength(3);
    expect(wrapper.text()).toContain('修改密码并重新登录');
    await wrapper.get('[data-submit-password]').trigger('click');

    await vi.waitFor(() =>
      expect(changePasswordMock).toHaveBeenCalledWith(undefined, 'new-password'),
    );
  });

  it('当前密码错误时只清空并聚焦当前密码', async () => {
    changePasswordMock.mockRejectedValue(
      new ApiError(400, '当前密码错误', {
        error_code: 'current_password_incorrect',
        detail: '当前密码错误',
      }),
    );
    const wrapper = mountForm();
    const inputs = await fill(wrapper, ['alice', 'wrong-password', 'new-password', 'new-password']);

    await wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('当前密码错误'));

    expect((inputs[1].element as HTMLInputElement).value).toBe('');
    expect((inputs[2].element as HTMLInputElement).value).toBe('new-password');
    expect(document.activeElement).toBe(inputs[1].element);
  });

  it('并发改密冲突回登录页并显示明确提示', async () => {
    changePasswordMock.mockImplementation(async () => {
      // 真实请求的 Bearer 401 会先触发全局未授权处理并清空当前用户名。
      sessionMock.username = '';
      throw new ApiError(
        401,
        '密码已由其他会话修改，请使用新密码登录',
        { error_code: 'password_changed_elsewhere' },
        true,
      );
    });
    const wrapper = mountForm(true);
    await fill(wrapper, ['alice', 'new-password', 'new-password']);

    await wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(finishPasswordChangeMock).toHaveBeenCalledOnce());

    expect(finishPasswordChangeMock).toHaveBeenCalledWith(
      'alice',
      '密码已由其他会话修改，请使用新密码登录',
    );
    expect(replaceMock).toHaveBeenCalledWith('/dashboard');
  });

  it('服务端的新密码错误和初始账号过期分别回填字段或返回登录页', async () => {
    changePasswordMock.mockRejectedValueOnce(
      new ApiError(400, '新密码不能与当前密码相同', {
        error_code: 'new_password_unchanged',
      }),
    );
    const ordinary = mountForm();
    await fill(ordinary, ['alice', 'old-password', 'new-password', 'new-password']);
    await ordinary.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(ordinary.text()).toContain('新密码不能与当前密码相同'));

    changePasswordMock.mockRejectedValueOnce(
      new ApiError(
        401,
        '初始账号已过期，请重启服务后重试',
        { error_code: 'bootstrap_expired' },
        true,
      ),
    );
    const forced = mountForm(true);
    await fill(forced, ['alice', 'another-password', 'another-password']);
    await forced.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() =>
      expect(finishPasswordChangeMock).toHaveBeenCalledWith(
        'alice',
        '初始账号已过期，请重启服务后重试',
      ),
    );
  });

  it('未知业务错误或没有错误码的 API 错误显示服务端消息', async () => {
    for (const detail of [{ error_code: 'password_change_rate_limited' }, undefined]) {
      changePasswordMock.mockRejectedValueOnce(new ApiError(429, '请稍后重试', detail));
      const wrapper = mountForm();
      await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);
      await wrapper.get('[data-submit-password]').trigger('click');
      await vi.waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('请稍后重试'));
      wrapper.unmount();
      toastMock.error.mockClear();
    }
  });

  it('网络结果不确定时保留输入并明确警告，不自动重试', async () => {
    changePasswordMock.mockRejectedValue(new TypeError('network'));
    const wrapper = mountForm();
    const inputs = await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);

    await wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(toastMock.warning).toHaveBeenCalledOnce());

    expect(toastMock.warning.mock.calls[0][0]).toContain('无法确认密码是否已修改');
    expect((inputs[2].element as HTMLInputElement).value).toBe('new-password');
    expect(changePasswordMock).toHaveBeenCalledOnce();
  });

  it('服务端 5xx 时同样将改密结果视为不确定并保留输入', async () => {
    changePasswordMock.mockRejectedValue(new ApiError(503, '服务暂时不可用'));
    const wrapper = mountForm();
    const inputs = await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);

    await wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(toastMock.warning).toHaveBeenCalledOnce());

    expect(toastMock.warning.mock.calls[0][0]).toContain('无法确认密码是否已修改');
    expect(toastMock.error).not.toHaveBeenCalled();
    expect((inputs[1].element as HTMLInputElement).value).toBe('old-password');
    expect((inputs[2].element as HTMLInputElement).value).toBe('new-password');
    expect(changePasswordMock).toHaveBeenCalledOnce();
  });

  it('脏表单和提交中状态保护导航与关闭页面', async () => {
    let finishRequest!: () => void;
    changePasswordMock.mockReturnValue(
      new Promise<void>((resolve) => {
        finishRequest = () => resolve();
      }),
    );
    const wrapper = mountForm();
    await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);
    const state = (wrapper.vm.$ as any).exposed;

    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    expect(state.confirmDiscard()).toBe(true);
    expect(state.confirmDiscard()).toBe(true);

    const cleanUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(cleanUnload);
    expect(cleanUnload.defaultPrevented).toBe(false);

    await fill(wrapper, ['alice', 'old-password', 'new-password', 'new-password']);
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    expect(state.confirmDiscard()).toBe(false);

    void wrapper.get('[data-submit-password]').trigger('click');
    await vi.waitFor(() => expect(changePasswordMock).toHaveBeenCalled());
    await wrapper.get('[data-submit-password]').trigger('click');
    expect(changePasswordMock).toHaveBeenCalledOnce();
    expect(state.confirmDiscard()).toBe(false);
    finishRequest();
  });
});
