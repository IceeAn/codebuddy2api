import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { loginMock, toastMock, validateMock } = vi.hoisted(() => ({
  loginMock: vi.fn<(username: string, password: string) => Promise<void>>(),
  toastMock: {
    success: vi.fn<(message: string, duration?: number) => void>(),
    error: vi.fn<(message: string, duration?: number) => void>(),
    warning: vi.fn<(message: string, duration?: number) => void>(),
    info: vi.fn<(message: string, duration?: number) => void>(),
  },
  validateMock: vi.fn<() => Promise<void>>(),
}));

vi.mock('../stores/session', () => ({
  useSessionStore: () => ({ login: loginMock }),
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
    validateMock.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    toastMock.info.mockReset();
    validateMock.mockResolvedValue(undefined);
  });

  it('使用项目图标', () => {
    const wrapper = mountView();

    expect(wrapper.get('img.project-icon').attributes('src')).toBe('/assets/codebuddy2api.svg');
  });

  it('校验通过后登录并清空密码，不额外弹成功提示', async () => {
    loginMock.mockResolvedValue(undefined);
    const wrapper = mountView();
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue(' admin ');
    await inputs[1].setValue('secret');

    await wrapper.get('button').trigger('click');
    await vi.waitFor(() => expect(loginMock).toHaveBeenCalledWith('admin', 'secret'));

    expect(toastMock.success).not.toHaveBeenCalled();
    expect((inputs[1].element as HTMLInputElement).value).toBe('');
    expect(wrapper.get('button').attributes('data-loading')).toBe('false');
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
