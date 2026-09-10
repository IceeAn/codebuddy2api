import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';

const { loginMock, bootstrapStatusMock, dismissFlashMock, sessionMock, toastMock, validateMock } =
  vi.hoisted(() => ({
    loginMock: vi.fn<(username: string, password: string) => Promise<void>>(),
    bootstrapStatusMock:
      vi.fn<() => Promise<{ bootstrap_required: boolean; bootstrap_expired: boolean }>>(),
    dismissFlashMock: vi.fn<() => void>(),
    sessionMock: {
      loginPrefillUsername: '',
      passwordChangedFlash: '',
    },
    toastMock: {
      success: vi.fn<(message: string, duration?: number) => void>(),
      error: vi.fn<(message: string, duration?: number) => void>(),
      warning: vi.fn<(message: string, duration?: number) => void>(),
      info: vi.fn<(message: string, duration?: number) => void>(),
    },
    validateMock: vi.fn<() => Promise<void>>(),
  }));

vi.mock('../stores/session', () => ({
  useSessionStore: () => ({
    ...sessionMock,
    login: loginMock,
    dismissPasswordChangedFlash: dismissFlashMock,
  }),
}));

vi.mock('../api/admin', () => ({
  authApi: { bootstrapStatus: bootstrapStatusMock },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

import LoginView from '../views/LoginView.vue';
const FormStub = defineComponent({
  name: 'CForm',
  setup(_, { expose, slots }) {
    expose({ validate: validateMock, restoreValidation: vi.fn<() => void>() });
    return () =>
      h('form', { onSubmit: (event: Event) => event.preventDefault() }, slots.default?.());
  },
});
const FormItemStub = defineComponent({
  name: 'CFormItem',
  props: { label: String, path: String },
  setup(props, { slots }) {
    return () =>
      h('div', { 'data-path': props.path }, [
        props.label ? h('label', props.label) : null,
        slots.default?.(),
      ]);
  },
});
const InputStub = defineComponent({
  name: 'CInput',
  props: { modelValue: { default: '' }, type: String, placeholder: String },
  emits: ['update:modelValue', 'enter', 'keyup'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        type: props.type === 'password' ? 'password' : 'text',
        value: props.modelValue,
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value),
        onKeyup: (event: KeyboardEvent) => {
          emit('keyup', event);
          if (event.key === 'Enter') emit('enter', event);
        },
      });
  },
});
const ButtonStub = defineComponent({
  name: 'CButton',
  props: { loading: Boolean, disabled: Boolean, block: Boolean, variant: String, size: String },
  emits: ['click'],
  setup(props, { attrs, emit, slots }) {
    return () =>
      h(
        'button',
        {
          ...attrs,
          disabled: props.disabled || props.loading,
          'data-loading': String(props.loading),
          onClick: () => emit('click'),
        },
        [slots.icon?.(), slots.default?.()],
      );
  },
});

function mountView() {
  return mount(LoginView, {
    global: {
      stubs: {
        CForm: FormStub,
        CFormItem: FormItemStub,
        CInput: InputStub,
        CButton: ButtonStub,
        LogIn: true,
        PlugZap: true,
      },
    },
  });
}

describe('LoginView', () => {
  beforeEach(() => {
    loginMock.mockReset();
    bootstrapStatusMock.mockReset();
    bootstrapStatusMock.mockResolvedValue({
      bootstrap_required: false,
      bootstrap_expired: false,
    });
    dismissFlashMock.mockReset();
    sessionMock.loginPrefillUsername = '';
    sessionMock.passwordChangedFlash = '';
    validateMock.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    toastMock.info.mockReset();
    validateMock.mockResolvedValue(undefined);
  });

  it('设置登录页标题并静默获取非引导状态', async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(bootstrapStatusMock).toHaveBeenCalledOnce());

    expect(document.title).toBe('登录 · CodeBuddy2API');
    expect(wrapper.text()).not.toContain('系统尚未初始化');
  });

  it('显示可关闭的有效引导提示，刷新组件后会重新出现', async () => {
    bootstrapStatusMock.mockResolvedValue({
      bootstrap_required: true,
      bootstrap_expired: false,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(wrapper.text()).toContain('系统尚未初始化'));

    expect(wrapper.text()).toContain('系统尚未初始化，请使用初始账号密码登录。');
    expect(wrapper.text()).toContain('初始账号仅在服务启动后 1 小时内有效。');
    await wrapper.get('[aria-label="关闭初始账号提示"]').trigger('click');
    expect(wrapper.text()).not.toContain('系统尚未初始化');
  });

  it('过期引导提示不可关闭且状态请求失败时不阻止登录', async () => {
    bootstrapStatusMock.mockResolvedValueOnce({
      bootstrap_required: true,
      bootstrap_expired: true,
    });
    const expired = mountView();
    await vi.waitFor(() => expect(expired.text()).toContain('初始账号已过期'));
    expect(expired.find('[aria-label="关闭初始账号提示"]').exists()).toBe(false);
    expect(expired.find('button').exists()).toBe(true);

    bootstrapStatusMock.mockRejectedValueOnce(new Error('network'));
    const unavailable = mountView();
    await vi.waitFor(() => expect(bootstrapStatusMock).toHaveBeenCalledTimes(2));
    expect(unavailable.text()).not.toContain('系统尚未初始化');
  });

  it('改密后预填用户名并显示可关闭提示', async () => {
    sessionMock.loginPrefillUsername = 'alice';
    sessionMock.passwordChangedFlash = '密码已修改，请使用新密码重新登录';
    const wrapper = mountView();

    expect((wrapper.findAll('input')[0].element as HTMLInputElement).value).toBe('alice');
    expect(wrapper.text()).toContain('密码已修改，请使用新密码重新登录');
    await wrapper.get('[aria-label="关闭密码修改提示"]').trigger('click');
    expect(dismissFlashMock).toHaveBeenCalledOnce();
  });

  it('使用项目图标', () => {
    const wrapper = mountView();

    expect(wrapper.get('img.project-icon').attributes('src')).not.toBe('/assets/codebuddy2api.svg');
  });

  it('密码校验提示与登录按钮之间保留固定间距', () => {
    const wrapper = mountView();

    expect(wrapper.get('button').classes()).toContain('mt-2');
  });

  it('校验通过后登录并清空密码，不额外弹成功提示', async () => {
    loginMock.mockResolvedValue(undefined);
    sessionMock.passwordChangedFlash = '密码已修改';
    const wrapper = mountView();
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue(' admin ');
    await inputs[1].setValue('secret');

    const loginButton = wrapper.findAll('button').find((button) => button.text().includes('登录'))!;
    await loginButton.trigger('click');
    await vi.waitFor(() => expect(loginMock).toHaveBeenCalledWith('admin', 'secret'));

    expect(toastMock.success).not.toHaveBeenCalled();
    expect(dismissFlashMock).toHaveBeenCalledOnce();
    expect((inputs[1].element as HTMLInputElement).value).toBe('');
    expect(loginButton.attributes('data-loading')).toBe('false');
  });

  it('表单校验失败时不发起登录', async () => {
    validateMock.mockRejectedValue(new Error('invalid'));
    const wrapper = mountView();

    await wrapper.get('button').trigger('click');
    await Promise.resolve();

    expect(loginMock).not.toHaveBeenCalled();
  });

  it('登录失败时提示错误并恢复 loading', async () => {
    loginMock.mockRejectedValue(new Error('认证失败'));
    const wrapper = mountView();
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue('admin');
    await inputs[1].setValue('bad');

    await wrapper.get('button').trigger('click');
    await vi.waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('认证失败'));

    expect(wrapper.get('button').attributes('data-loading')).toBe('false');
  });

  it('登录返回初始账号过期错误后立即切换为固定过期提示', async () => {
    bootstrapStatusMock.mockResolvedValue({
      bootstrap_required: true,
      bootstrap_expired: false,
    });
    loginMock.mockRejectedValue(
      new ApiError(
        401,
        '初始账号已过期，请重启服务后重试',
        { error_code: 'bootstrap_expired' },
        true,
      ),
    );
    const wrapper = mountView();
    await vi.waitFor(() => expect(wrapper.text()).toContain('系统尚未初始化'));
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue('admin');
    await inputs[1].setValue('admin');

    const loginButton = wrapper.findAll('button').find((button) => button.text().includes('登录'))!;
    await loginButton.trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('初始账号已过期'));

    expect(wrapper.find('[aria-label="关闭初始账号提示"]').exists()).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith('初始账号已过期，请重启服务后重试');
  });

  it('登录确认初始账号过期后忽略更早发起的未过期状态响应', async () => {
    let resolveBootstrapStatus!: (status: {
      bootstrap_required: boolean;
      bootstrap_expired: boolean;
    }) => void;
    bootstrapStatusMock.mockReturnValue(
      new Promise((resolve) => {
        resolveBootstrapStatus = resolve;
      }),
    );
    loginMock.mockRejectedValue(
      new ApiError(
        401,
        '初始账号已过期，请重启服务后重试',
        { error_code: 'bootstrap_expired' },
        true,
      ),
    );
    const wrapper = mountView();
    await vi.waitFor(() => expect(bootstrapStatusMock).toHaveBeenCalledOnce());
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue('admin');
    await inputs[1].setValue('admin');

    const loginButton = wrapper.findAll('button').find((button) => button.text().includes('登录'))!;
    await loginButton.trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('初始账号已过期'));

    resolveBootstrapStatus({ bootstrap_required: true, bootstrap_expired: false });
    await Promise.resolve();
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('初始账号已过期');
    expect(wrapper.text()).not.toContain('系统尚未初始化');
  });

  it('loading 期间重复提交被忽略', async () => {
    let resolveLogin = () => {};
    loginMock.mockReturnValue(
      new Promise<void>((resolve) => {
        resolveLogin = resolve;
      }),
    );
    const wrapper = mountView();
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue('admin');
    await inputs[1].setValue('secret');

    const first = wrapper.get('button').trigger('click');
    await vi.waitFor(() => expect(loginMock).toHaveBeenCalledOnce());
    await wrapper.get('button').trigger('click');
    expect(loginMock).toHaveBeenCalledOnce();

    resolveLogin();
    await first;
  });

  it('handleSubmit 在 loading 状态下直接返回', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.loading = true;

    await state.handleSubmit();
    expect(validateMock).not.toHaveBeenCalled();
    expect(loginMock).not.toHaveBeenCalled();
  });
});
