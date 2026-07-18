import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { anthropicPlaygroundApi, openaiPlaygroundApi } from '../api/admin';

const {
  modelsQuery,
  queryOptions,
  toastMock,
  openaiModelsMock,
  chatMock,
  anthropicChatMock,
  anthropicModelsMock,
  validateMock,
} = vi.hoisted(() => {
  const refValue = <T>(value: T) => ({ __v_isRef: true as const, value });
  return {
    modelsQuery: {
      data: refValue<unknown>(undefined),
      error: refValue<unknown>(undefined),
      isError: refValue(false),
      isLoading: refValue(false),
      isFetching: refValue(false),
      refetch: vi.fn<() => Promise<unknown>>(),
    },
    queryOptions: { value: undefined as any },
    toastMock: {
      success: vi.fn<(message: string, duration?: number) => void>(),
      error: vi.fn<(message: string, duration?: number) => void>(),
      warning: vi.fn<(message: string, duration?: number) => void>(),
      info: vi.fn<(message: string, duration?: number) => void>(),
    },
    openaiModelsMock: vi.fn<typeof openaiPlaygroundApi.models>(),
    chatMock: vi.fn<typeof openaiPlaygroundApi.chat>(),
    anthropicChatMock: vi.fn<typeof anthropicPlaygroundApi.chat>(),
    anthropicModelsMock: vi.fn<typeof anthropicPlaygroundApi.models>(),
    validateMock: vi.fn<() => Promise<void>>(),
  };
});

vi.mock('@tanstack/vue-query', () => ({
  useQuery: (options: unknown) => {
    queryOptions.value = options;
    return modelsQuery;
  },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../api/admin', () => ({
  openaiPlaygroundApi: {
    models: openaiModelsMock,
    chat: chatMock,
  },
  anthropicPlaygroundApi: {
    models: anthropicModelsMock,
    chat: anthropicChatMock,
  },
}));

import ApiConsoleView from '../views/ApiConsoleView.vue';
const FormStub = defineComponent({
  name: 'CForm',
  setup(_, { expose, slots }) {
    expose({ validate: validateMock, restoreValidation: vi.fn<() => void>() });
    return () => h('form', null, slots.default?.());
  },
});
const FormItemStub = defineComponent({
  name: 'CFormItem',
  props: { label: String, path: String },
  setup(props, { slots }) {
    return () => h('div', { 'data-path': props.path }, slots.default?.());
  },
});
const InputStub = defineComponent({
  name: 'CInput',
  inheritAttrs: false,
  props: {
    modelValue: { default: '' },
    type: String,
    placeholder: String,
    autosize: { default: undefined },
  },
  emits: ['update:modelValue', 'enter', 'keyup'],
  setup(props, { attrs, emit }) {
    return () =>
      h(props.type === 'textarea' ? 'textarea' : 'input', {
        ...attrs,
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
const SelectStub = defineComponent({
  name: 'CSelect',
  inheritAttrs: false,
  props: {
    modelValue: { default: '' },
    options: { default: () => [] },
    loading: Boolean,
    filterable: Boolean,
    placeholder: String,
  },
  emits: ['update:modelValue'],
  setup(props, { attrs, emit }) {
    return () =>
      h('select', {
        ...attrs,
        value: props.modelValue,
        'data-loading': String(props.loading),
        onChange: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLSelectElement).value),
      });
  },
});
const CheckboxStub = defineComponent({
  name: 'CCheckbox',
  props: { modelValue: Boolean, disabled: Boolean },
  emits: ['update:modelValue'],
  setup(props, { emit, slots }) {
    return () =>
      h('label', [
        h('input', {
          type: 'checkbox',
          checked: props.modelValue,
          disabled: props.disabled,
          onChange: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).checked),
        }),
        slots.default?.(),
      ]);
  },
});
const ButtonStub = defineComponent({
  name: 'CButton',
  inheritAttrs: false,
  props: {
    variant: String,
    size: String,
    loading: Boolean,
    disabled: Boolean,
    block: Boolean,
  },
  emits: ['click'],
  setup(props, { attrs, emit, slots }) {
    return () =>
      h(
        'button',
        {
          ...attrs,
          disabled: props.disabled || props.loading,
          'data-loading': String(props.loading),
          'data-variant': props.variant ?? '',
          onClick: () => emit('click'),
        },
        [slots.icon?.(), slots.default?.()],
      );
  },
});
const CardStub = defineComponent({
  name: 'CCard',
  props: { title: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('section', { 'data-title': props.title ?? '' }, [
        props.title ? h('div', { class: 'c-card-title' }, props.title) : null,
        slots['header-extra']?.(),
        slots.default?.(),
      ]);
  },
});
const AlertStub = defineComponent({
  name: 'CAlert',
  inheritAttrs: false,
  props: { type: String, title: String, closable: Boolean, showIcon: { default: true } },
  setup(props, { attrs, slots }) {
    return () =>
      h(
        'div',
        {
          ...attrs,
          'data-type': props.type ?? '',
          'data-title': props.title ?? '',
        },
        [slots.default?.(), slots.action?.()],
      );
  },
});

function mountView() {
  return mount(ApiConsoleView, {
    global: {
      stubs: {
        CCard: CardStub,
        CAlert: AlertStub,
        CForm: FormStub,
        CFormItem: FormItemStub,
        CSelect: SelectStub,
        CInput: InputStub,
        CCheckbox: CheckboxStub,
        CButton: ButtonStub,
        Play: true,
        RefreshCw: true,
        Square: true,
      },
    },
  });
}

function response(body: BodyInit | null, init?: ResponseInit) {
  return new Response(body, init);
}

describe('ApiConsoleView', () => {
  beforeEach(() => {
    modelsQuery.data.value = undefined;
    modelsQuery.error.value = undefined;
    modelsQuery.isError.value = false;
    modelsQuery.isLoading.value = false;
    modelsQuery.isFetching.value = false;
    modelsQuery.refetch.mockReset();
    chatMock.mockReset();
    anthropicChatMock.mockReset();
    anthropicModelsMock.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    toastMock.info.mockReset();
    validateMock.mockReset();
    validateMock.mockResolvedValue(undefined);
  });

  it('构造模型选项并校验空消息', async () => {
    modelsQuery.data.value = { data: [{ id: 'glm' }, { id: 'deepseek' }] };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.modelOptions).toEqual([
      { label: 'glm', value: 'glm' },
      { label: 'deepseek', value: 'deepseek' },
    ]);
    expect(state.consoleRules.prompt.whitespace).toBe(true);
    state.prompt = ' ';
    validateMock.mockRejectedValueOnce(new Error('invalid'));
    await state.send();
    expect(validateMock).toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalledWith('请输入消息');
    expect(chatMock).not.toHaveBeenCalled();
    state.stop();
  });

  it('非流式请求成功并选择显式模型', async () => {
    chatMock.mockResolvedValue(
      response(JSON.stringify({ answer: 4 }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.selectedModel = 'glm';
    state.prompt = '2+2';

    await state.send();

    expect(chatMock).toHaveBeenCalledWith(
      {
        model: 'glm',
        messages: [{ role: 'user', content: '2+2' }],
        stream: false,
      },
      expect.any(AbortSignal),
    );
    expect(state.output).toContain('"answer": 4');
    expect(toastMock.success).toHaveBeenCalledWith('请求完成');
    expect(state.loading).toBe(false);
    expect(state.abortController).toBeNull();
  });

  it('切换 Anthropic 后隔离模型查询、转换请求并清空进行中输出', async () => {
    anthropicModelsMock.mockResolvedValue({ data: [{ id: 'anthropic/codebuddy/glm' }] });
    anthropicChatMock.mockResolvedValue(response('{"type":"message"}'));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.selectedModel = 'old';
    state.output = 'old output';
    const abort = vi.fn<AbortController['abort']>();
    state.abortController = { abort };

    await wrapper.findComponent({ name: 'CRadioGroup' }).vm.$emit('update:modelValue', 'anthropic');
    await wrapper.vm.$nextTick();

    expect(abort).toHaveBeenCalledOnce();
    expect(state.selectedModel).toBe('');
    expect(state.output).toBe('点击发送查看响应');
    expect(queryOptions.value.queryKey.value).toEqual([
      'admin',
      'test-user',
      'playground',
      'anthropic',
      'models',
    ]);
    const modelController = new AbortController();
    await expect(queryOptions.value.queryFn({ signal: modelController.signal })).resolves.toEqual({
      data: [{ id: 'anthropic/codebuddy/glm' }],
    });
    expect(anthropicModelsMock).toHaveBeenCalledWith(modelController.signal);
    state.protocol = 'openai';
    await wrapper.vm.$nextTick();
    await queryOptions.value.queryFn({ signal: modelController.signal });
    expect(openaiModelsMock).toHaveBeenCalledWith(modelController.signal);
    state.protocol = 'anthropic';
    await wrapper.vm.$nextTick();

    state.prompt = 'hello';
    await state.send();
    expect(anthropicChatMock).toHaveBeenCalledWith(
      {
        model: 'anthropic/codebuddy/glm-5.2',
        max_tokens: 1024,
        system: 'You are a helpful assistant.',
        messages: [{ role: 'user', content: 'hello' }],
        stream: false,
      },
      expect.any(AbortSignal),
    );
    expect(chatMock).not.toHaveBeenCalled();
  });

  it('Anthropic 流保留事件名并以 message_stop 完成', async () => {
    anthropicChatMock.mockResolvedValue(
      response(
        'event: message_start\ndata: {"type":"message_start"}\n\n' +
          'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n\n' +
          'event: message_stop\ndata: {"type":"message_stop"}\n\n',
      ),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.protocol = 'anthropic';
    state.stream = true;
    await wrapper.vm.$nextTick();

    await state.send();

    expect(state.output).toContain('"event": "content_block_delta"');
    expect(state.output).toContain('"event": "message_stop"');
    expect(toastMock.success).toHaveBeenCalledWith('流式请求完成');
  });

  it('Anthropic 流不把 DONE 当完成，协议切换取消时不覆盖已清空输出', async () => {
    anthropicChatMock
      .mockResolvedValueOnce(response('data: [DONE]\n\n'))
      .mockImplementationOnce((_body, signal) => {
        return new Promise((_, reject) => {
          signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        });
      });
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.protocol = 'anthropic';
    state.stream = true;
    await wrapper.vm.$nextTick();

    await state.send();
    expect(state.output).toBe('流式响应在 message_stop 之前结束');

    const pending = state.send();
    await vi.waitFor(() => expect(state.loading).toBe(true));
    state.protocol = 'openai';
    await wrapper.vm.$nextTick();
    await pending;
    expect(state.output).toBe('点击发送查看响应');
  });

  it('请求期间固定发送时的流式模式并禁用模式开关', async () => {
    let resolveResponse: (value: Response) => void = () => {};
    chatMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveResponse = resolve;
        }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = false;

    const pending = state.send();
    await vi.waitFor(() => expect(chatMock).toHaveBeenCalledOnce());
    expect(wrapper.findComponent(CheckboxStub).props('disabled')).toBe(true);

    state.stream = true;
    resolveResponse(
      response(JSON.stringify({ answer: 4 }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await pending;

    expect(chatMock.mock.calls[0][0].stream).toBe(false);
    expect(state.output).toContain('"answer": 4');
    expect(wrapper.findComponent(CheckboxStub).props('disabled')).toBe(false);
  });

  it('未选择模型时使用首个模型', async () => {
    modelsQuery.data.value = { data: [{ id: 'first' }] };
    chatMock.mockResolvedValue(response('{}', { headers: { 'Content-Type': 'application/json' } }));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    await state.send();
    expect(chatMock.mock.calls[0][0].model).toBe('first');
  });

  it('模型列表为空时使用默认模型', async () => {
    modelsQuery.data.value = { data: [] };
    chatMock.mockResolvedValue(response('{}', { headers: { 'Content-Type': 'application/json' } }));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    await state.send();
    expect(chatMock.mock.calls[0][0].model).toBe('glm-5.2');
  });

  it('HTTP 错误展示响应文本', async () => {
    chatMock.mockResolvedValue(response('bad request', { status: 400 }));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    await state.send();
    expect(state.output).toBe('bad request');
    expect(toastMock.error).toHaveBeenCalledWith('HTTP 400');
  });

  it('流式请求跨 UTF-8 与 SSE chunk 解码，只在 DONE 后成功并批量刷新输出', async () => {
    const cancel = vi
      .fn<ReadableStreamDefaultReader<Uint8Array>['cancel']>()
      .mockResolvedValue(undefined);
    const releaseLock = vi.fn<ReadableStreamDefaultReader<Uint8Array>['releaseLock']>();
    const encoded = new TextEncoder().encode(
      'data: {"text":"你"}\r\n\r\ndata: {"b":2}\n\ndata: [DONE]\r\n\r\n',
    );
    const splitAt = encoded.indexOf(0xe4) + 1;
    const chunks = [encoded.slice(0, splitAt), encoded.slice(splitAt)];
    const read = vi
      .fn<ReadableStreamDefaultReader<Uint8Array>['read']>()
      .mockResolvedValueOnce({ done: false, value: chunks[0] })
      .mockResolvedValueOnce({ done: false, value: chunks[1] })
      .mockResolvedValueOnce({ done: true, value: undefined });
    const animationFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 123);
    const cancelAnimationFrame = vi
      .spyOn(window, 'cancelAnimationFrame')
      .mockImplementation(() => {});
    chatMock.mockResolvedValue({
      ok: true,
      body: { getReader: () => ({ read, cancel, releaseLock }) },
    } as unknown as Response);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = true;

    await state.send();

    expect(state.output).toContain('"text": "你"');
    expect(state.output).toContain('"b": 2');
    expect(toastMock.success).toHaveBeenCalledWith('流式请求完成');
    expect(animationFrame).toHaveBeenCalledOnce();
    expect(cancelAnimationFrame).toHaveBeenCalledWith(123);
    expect(cancel).toHaveBeenCalledOnce();
    expect(releaseLock).toHaveBeenCalledOnce();
    animationFrame.mockRestore();
    cancelAnimationFrame.mockRestore();
  });

  it('动画帧合并多个事件，并可在卸载前取消尚未刷新的帧', () => {
    let frameCallback: FrameRequestCallback | undefined;
    const animationFrame = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        frameCallback = callback;
        return 456;
      });
    const cancelAnimationFrame = vi
      .spyOn(window, 'cancelAnimationFrame')
      .mockImplementation(() => {});
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.output = '';

    state.queueStreamOutput({ a: 1 });
    state.queueStreamOutput({ b: 2 });
    expect(animationFrame).toHaveBeenCalledOnce();
    frameCallback?.(0);
    expect(state.output).toContain('"a": 1');
    expect(state.output).toContain('"b": 2');
    frameCallback?.(0);

    state.queueStreamOutput({ pending: true });
    state.resetStreamOutput();
    expect(cancelAnimationFrame).toHaveBeenCalledWith(456);
    animationFrame.mockRestore();
    cancelAnimationFrame.mockRestore();
  });

  it('流在 DONE 前 EOF 时失败，不再误报成功', async () => {
    chatMock.mockResolvedValue(
      response('data: {"partial":true}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = true;

    await state.send();

    expect(state.output).toContain('"partial": true');
    expect(state.output).toContain('流式响应在 [DONE] 之前结束');
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith('请求失败');
  });

  it('坏 JSON、错误信封和被截断事件均报告失败', async () => {
    chatMock
      .mockResolvedValueOnce(response('data: not-json\n\n'))
      .mockResolvedValueOnce(response('data: {"error":{"message":"额度不足"}}\n\n'))
      .mockResolvedValueOnce(response('data: {"partial":'));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = true;

    await state.send();
    expect(state.output).toBe('SSE data 不是有效 JSON');

    await state.send();
    expect(state.output).toBe('额度不足');

    await state.send();
    expect(state.output).toBe('SSE 流在事件结束前中断');
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledTimes(3);
  });

  it('reader cancel 失败仍释放锁', async () => {
    const releaseLock = vi.fn<ReadableStreamDefaultReader<Uint8Array>['releaseLock']>();
    chatMock.mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: vi
            .fn<ReadableStreamDefaultReader<Uint8Array>['read']>()
            .mockResolvedValue({ done: true, value: undefined }),
          cancel: vi
            .fn<ReadableStreamDefaultReader<Uint8Array>['cancel']>()
            .mockRejectedValue(new Error('closed')),
          releaseLock,
        }),
      },
    } as unknown as Response);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = true;

    await state.send();
    expect(releaseLock).toHaveBeenCalledOnce();
  });

  it('流式响应体不可读时显示错误', async () => {
    chatMock.mockResolvedValue({ ok: true, body: null } as Response);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = true;

    await state.send();
    expect(state.output).toBe('响应体不可读');
    expect(toastMock.error).toHaveBeenCalledWith('请求失败');
  });

  it('401 与普通异常使用对应提示', async () => {
    chatMock
      .mockRejectedValueOnce(new ApiError(401, 'unauthorized'))
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce('bad');
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    await state.send();
    expect(state.output).toBe('认证过期，请重新登录');
    expect(toastMock.error).toHaveBeenLastCalledWith('认证过期，请重新登录');

    await state.send();
    expect(state.output).toBe('network');
    expect(toastMock.error).toHaveBeenLastCalledWith('请求失败');

    await state.send();
    expect(state.output).toBe('bad');
  });

  it('停止请求后显示已取消，非流式 loading 时忽略重复发送', async () => {
    chatMock.mockImplementation((_body, signal) => {
      if (!signal) throw new Error('缺少中止信号');
      return new Promise((_, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    });
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    const pending = state.send();
    await vi.waitFor(() => expect(state.loading).toBe(true));
    const duplicate = state.send();
    expect(chatMock).toHaveBeenCalledOnce();
    state.stop();
    await pending;
    await duplicate;

    expect(state.output).toBe('已取消');
    expect(toastMock.info).toHaveBeenCalledWith('请求已取消');
  });

  it('模型错误状态支持重试，卸载时中止请求', async () => {
    modelsQuery.isError.value = true;
    modelsQuery.error.value = new Error('models failed');
    const wrapper = mountView();
    expect(wrapper.text()).toContain('models failed');

    const retry = wrapper.findAll('button').find((button) => button.text().includes('重试'));
    await retry?.trigger('click');
    expect(modelsQuery.refetch).toHaveBeenCalledOnce();

    const state = (wrapper.vm.$ as any).setupState;
    const abort = vi.fn<AbortController['abort']>();
    state.abortController = { abort };
    wrapper.unmount();
    expect(abort).toHaveBeenCalledOnce();
  });

  it('模板双向绑定和模型刷新按钮可用，并显示未知模型错误', async () => {
    modelsQuery.isError.value = true;
    modelsQuery.error.value = 'bad';
    const wrapper = mountView();

    expect(wrapper.text()).toContain('未知错误');
    await wrapper.findComponent(SelectStub).vm.$emit('update:modelValue', 'selected');
    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'new prompt');
    await wrapper.findComponent(CheckboxStub).vm.$emit('update:modelValue', true);
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.selectedModel).toBe('selected');
    expect(state.prompt).toBe('new prompt');
    expect(state.stream).toBe(true);

    const modelButton = wrapper.findAll('button').find((button) => button.text().trim() === '刷新');
    await modelButton?.trigger('click');
    expect(modelsQuery.refetch).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('模型列表已刷新'));
  });

  it('请求与响应卡片固定上下排列，模型和操作控件按要求对齐', () => {
    const wrapper = mountView();

    const layout = wrapper.find('.console-layout');
    expect(layout.classes()).toContain('grid-cols-1');
    expect(layout.classes().some((name) => name.startsWith('lg:grid-cols-'))).toBe(false);

    const modelRow = wrapper.find('.console-model-row');
    expect(modelRow.classes()).toEqual(expect.arrayContaining(['flex', 'items-center']));
    expect(modelRow.findComponent(SelectStub).exists()).toBe(true);
    expect(modelRow.findAll('button').some((button) => button.text().trim() === '刷新')).toBe(true);

    const actionRow = wrapper.find('.console-action-row');
    expect(actionRow.classes()).toEqual(
      expect.arrayContaining(['flex', 'items-center', 'justify-end']),
    );
    expect(actionRow.findComponent(CheckboxStub).exists()).toBe(true);
    expect(actionRow.findAll('button').some((button) => button.text().trim() === '发送')).toBe(
      true,
    );
  });

  it('非流式 loading 时直接调用 doSend 不发起请求', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.stream = false;
    state.loading = true;

    await state.doSend();

    expect(chatMock).not.toHaveBeenCalled();
    expect(state.loading).toBe(true);
  });

  it('使用自建表单控件组件', () => {
    const wrapper = mountView();
    expect(wrapper.findAllComponents(ButtonStub).length).toBeGreaterThan(0);
    expect(wrapper.findComponent(InputStub).exists()).toBe(true);
    expect(wrapper.findComponent(SelectStub).exists()).toBe(true);
  });
});
