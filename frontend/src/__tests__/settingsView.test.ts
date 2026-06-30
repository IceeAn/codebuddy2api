import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { ref, type Ref } from 'vue';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const { query, mutationOptions, mutationStates, invalidateQueries, toastMock } = vi.hoisted(() => ({
  query: {
    data: { __v_isRef: true, value: undefined as unknown },
    error: { __v_isRef: true, value: undefined as unknown },
    isError: { __v_isRef: true, value: false },
    isLoading: { __v_isRef: true, value: false },
    isFetching: { __v_isRef: true, value: false },
    refetch: vi.fn<() => Promise<unknown>>(),
  },
  mutationOptions: [] as Array<Record<string, (...args: any[]) => any>>,
  mutationStates: [] as Array<{
    isPending: Ref<boolean>;
    isSuccess: Ref<boolean>;
    mutate: Mock<(variables?: unknown) => void>;
  }>,
  invalidateQueries: vi.fn<(filters?: unknown) => Promise<void>>(),
  toastMock: {
    success: vi.fn<(message: string, duration?: number) => void>(),
    error: vi.fn<(message: string, duration?: number) => void>(),
    warning: vi.fn<(message: string, duration?: number) => void>(),
    info: vi.fn<(message: string, duration?: number) => void>(),
  },
}));

// 提供给 vi.mock 工厂使用的 ref 工厂（工厂内无法直接 import vue）
const createRef = ref;

vi.mock('@tanstack/vue-query', () => ({
  useQuery: () => query,
  useQueryClient: () => ({ invalidateQueries }),
  useMutation: (options: Record<string, (...args: any[]) => any>) => {
    mutationOptions.push(options);
    const state = {
      isPending: createRef(false),
      isSuccess: createRef(false),
      mutate: vi.fn<(variables?: unknown) => void>(),
    };
    mutationStates.push(state);
    return state;
  },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

import SettingsView from '../views/SettingsView.vue';
import { adminApi } from '../api/admin';
import { RefreshButtonStub } from './refreshButtonStub';
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
const SpinStub = defineComponent({
  name: 'CSpin',
  props: { size: String, inherit: Boolean },
  setup() {
    return () => h('span', { class: 'c-spin' });
  },
});
const FormStub = defineComponent({
  name: 'CForm',
  props: { labelPlacement: String, labelWidth: String },
  setup(_, { slots }) {
    return () => h('form', null, slots.default?.());
  },
});
const FormItemStub = defineComponent({
  name: 'CFormItem',
  props: { label: String, path: String },
  setup(props, { slots }) {
    return () => h('div', { 'data-label': props.label ?? '' }, slots.default?.());
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
  },
  emits: ['update:modelValue'],
  setup(props, { attrs, emit }) {
    return () =>
      h('select', {
        ...attrs,
        value: props.modelValue,
        onChange: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLSelectElement).value),
      });
  },
});
const SwitchStub = defineComponent({
  name: 'CSwitch',
  props: { modelValue: Boolean, disabled: Boolean, size: String },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('button', {
        type: 'button',
        'data-checked': String(props.modelValue),
        onClick: () => emit('update:modelValue', !props.modelValue),
      });
  },
});
const InputNumberStub = defineComponent({
  name: 'CInputNumber',
  inheritAttrs: false,
  props: {
    modelValue: { default: null },
    min: Number,
    max: Number,
    step: { default: 1 },
    clearable: Boolean,
    disabled: Boolean,
  },
  emits: ['update:modelValue'],
  setup(props, { attrs, emit }) {
    return () =>
      h('input', {
        ...attrs,
        type: 'number',
        value: props.modelValue ?? '',
        onInput: (event: Event) => {
          const raw = (event.target as HTMLInputElement).value;
          emit('update:modelValue', raw === '' ? null : Number(raw));
        },
      });
  },
});
const DynamicTagsStub = defineComponent({
  name: 'CDynamicTags',
  props: { modelValue: { default: () => [] }, placeholder: String },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        type: 'text',
        'data-tags': String(props.modelValue),
        onInput: (event: Event) =>
          emit('update:modelValue', [(event.target as HTMLInputElement).value]),
      });
  },
});
const InputStub = defineComponent({
  name: 'CInput',
  inheritAttrs: false,
  props: {
    modelValue: { default: '' },
    type: String,
    placeholder: String,
  },
  emits: ['update:modelValue', 'enter', 'keyup'],
  setup(props, { attrs, emit }) {
    return () =>
      h('input', {
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

function mountView() {
  mutationOptions.length = 0;
  mutationStates.length = 0;
  return mount(SettingsView, {
    global: {
      stubs: {
        CCard: CardStub,
        CAlert: AlertStub,
        CSpin: SpinStub,
        CForm: FormStub,
        CFormItem: FormItemStub,
        CSelect: SelectStub,
        CSwitch: SwitchStub,
        CInputNumber: InputNumberStub,
        CDynamicTags: DynamicTagsStub,
        CInput: InputStub,
        CButton: ButtonStub,
        RefreshButton: RefreshButtonStub,
        Save: true,
      },
    },
  });
}

describe('SettingsView', () => {
  beforeEach(() => {
    (query as any).data = createRef(undefined);
    (query as any).error = createRef(undefined);
    (query as any).isError = createRef(false);
    (query as any).isLoading = createRef(false);
    (query as any).isFetching = createRef(false);
    query.data.value = undefined;
    query.isError.value = false;
    query.isLoading.value = false;
    query.refetch.mockReset();
    invalidateQueries.mockReset();
    invalidateQueries.mockResolvedValue(undefined);
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    toastMock.info.mockReset();
  });

  it('没有配置项时显示空状态', () => {
    const wrapper = mountView();
    expect(wrapper.text()).toContain('暂无可配置项');
  });

  it('初始化各种字段并构造保存 payload', async () => {
    query.data.value = {
      settings: {
        model: 'glm',
        enabled: true,
        count: 2,
        tags: 'a|b',
        nullable: null,
        text: 'value',
      },
      fields: [
        { key: 'model', label: '模型', type: 'select', options: ['glm', 'deepseek'] },
        { key: 'enabled', label: '启用', type: 'boolean' },
        { key: 'count', label: '次数', type: 'number', min: 1, max: 5, step: 0 },
        { key: 'tags', label: '标签', type: 'tags', separator: '|' },
        { key: 'nullable', label: '可空', type: 'text', nullable: true },
        { key: 'text', label: '文本', type: 'text' },
      ],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.fields).toHaveLength(6);
    expect(state.selectOptions(state.fields[0])).toEqual([
      { label: 'glm', value: 'glm' },
      { label: 'deepseek', value: 'deepseek' },
    ]);
    expect(state.selectOptions({ options: undefined })).toEqual([]);
    expect(state.buildPayload()).toEqual({
      model: 'glm',
      enabled: true,
      count: 2,
      tags: 'a|b',
      nullable: '',
      text: 'value',
    });

    state.form.nullable = '';
    state.tagValues.tags = [];
    expect(state.buildPayload()).toMatchObject({ nullable: '', tags: '' });

    delete state.tagValues.tags;
    state.fields[3].separator = undefined;
    expect(state.buildPayload()).toMatchObject({ tags: '' });
  });

  it('保存成功后提示并刷新设置与状态', async () => {
    const saveSpy = vi.spyOn(adminApi, 'saveSettings').mockResolvedValue({} as never);
    const wrapper = mountView();
    await mutationOptions[0].mutationFn();
    expect(saveSpy).toHaveBeenCalledWith({});

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('保存'));
    await saveButton?.trigger('click');
    await mutationOptions[0].onSuccess({ settings: {}, fields: [] });

    expect(toastMock.success).toHaveBeenCalledWith('设置已保存');
    expect(invalidateQueries.mock.calls).toEqual([
      [{ queryKey: ['admin-settings'] }],
      [{ queryKey: ['admin-status'] }],
    ]);
  });

  it('表单未编辑时再次收到服务端设置会同步真实值', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    query.data.value = {
      settings: { text: 'server-new' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    await wrapper.vm.$nextTick();

    expect((wrapper.vm.$ as any).setupState.form.text).toBe('server-new');
  });

  it('表单已编辑时自动刷新不覆盖用户输入', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'user-edit');
    query.data.value = {
      settings: { text: 'server-new' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    await wrapper.vm.$nextTick();

    expect((wrapper.vm.$ as any).setupState.form.text).toBe('user-edit');
  });

  it('保存成功后强制应用后端返回的真实设置并重置 dirty', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'user-edit');
    vi.spyOn(adminApi, 'saveSettings').mockResolvedValue({} as never);
    await mutationOptions[0].mutationFn();
    await mutationOptions[0].onSuccess({
      settings: { text: 'server-saved' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    });
    query.data.value = {
      settings: { text: 'server-refetch' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    await wrapper.vm.$nextTick();

    expect((wrapper.vm.$ as any).setupState.form.text).toBe('server-refetch');
  });

  it('保存请求期间产生的新编辑不会被旧响应覆盖', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    vi.spyOn(adminApi, 'saveSettings').mockResolvedValue({} as never);
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'submitted-edit');
    await mutationOptions[0].mutationFn();
    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'newer-edit');
    await mutationOptions[0].onSuccess({
      settings: { text: 'server-saved' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    });
    query.data.value = {
      settings: { text: 'server-refetch' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    await wrapper.vm.$nextTick();

    expect((wrapper.vm.$ as any).setupState.form.text).toBe('newer-edit');
  });

  it('显式刷新成功后强制同步后端真实设置', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    query.refetch.mockImplementation(async () => ({
      isError: false,
      data: {
        settings: { text: 'server-refresh' },
        fields: [{ key: 'text', label: '文本', type: 'text' }],
      },
    }));
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'user-edit');
    const refreshButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('刷新'));
    await refreshButton?.trigger('click');
    await wrapper.vm.$nextTick();

    expect(query.refetch).toHaveBeenCalledOnce();
    expect((wrapper.vm.$ as any).setupState.form.text).toBe('server-refresh');
  });

  it('显式刷新失败或无数据时不覆盖 dirty 表单', async () => {
    query.data.value = {
      settings: { text: 'old' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();
    await wrapper.findComponent(InputStub).vm.$emit('update:modelValue', 'user-edit');
    const refreshButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('刷新'));

    query.refetch.mockResolvedValueOnce({ isError: true });
    await refreshButton?.trigger('click');
    await wrapper.vm.$nextTick();
    expect((wrapper.vm.$ as any).setupState.form.text).toBe('user-edit');

    query.refetch.mockResolvedValueOnce({ isError: false });
    await refreshButton?.trigger('click');
    await wrapper.vm.$nextTick();
    expect((wrapper.vm.$ as any).setupState.form.text).toBe('user-edit');
  });

  it('正在刷新时忽略重复刷新点击', async () => {
    query.isFetching.value = true;
    const wrapper = mountView();

    const refreshButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('刷新'));
    await refreshButton?.trigger('click');

    expect(query.refetch).not.toHaveBeenCalled();
  });

  it('保存返回轮换频率时同步并归一化后端真实值', async () => {
    query.data.value = {
      settings: { CODEBUDDY_AUTO_ROTATION_ENABLED: true, CODEBUDDY_ROTATION_COUNT: 3 },
      fields: [
        { key: 'CODEBUDDY_AUTO_ROTATION_ENABLED', label: '凭证轮换', type: 'boolean' },
        { key: 'CODEBUDDY_ROTATION_COUNT', label: '轮换频率', type: 'number', min: 1 },
      ],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    await mutationOptions[0].onSuccess({
      settings: { CODEBUDDY_AUTO_ROTATION_ENABLED: true, CODEBUDDY_ROTATION_COUNT: 0 },
      fields: [
        { key: 'CODEBUDDY_AUTO_ROTATION_ENABLED', label: '凭证轮换', type: 'boolean' },
        { key: 'CODEBUDDY_ROTATION_COUNT', label: '轮换频率', type: 'number', min: 1 },
      ],
    });

    expect((wrapper.vm.$ as any).setupState.form.CODEBUDDY_ROTATION_COUNT).toBe(1);
  });

  it('保存成功时保存按钮短暂应用 animate-success 动效', async () => {
    vi.useFakeTimers();
    const wrapper = mountView();

    mutationStates[0].isSuccess.value = true;
    await wrapper.vm.$nextTick();

    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('保存'))!;
    expect(saveButton.classes()).toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.savedFlash).toBe(true);

    vi.advanceTimersByTime(600);
    await wrapper.vm.$nextTick();

    expect(saveButton.classes()).not.toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.savedFlash).toBe(false);
    vi.useRealTimers();
  });

  it('saveMutation.isSuccess 从 true 变为 false 时不移除已应用的动效', async () => {
    vi.useFakeTimers();
    const wrapper = mountView();

    mutationStates[0].isSuccess.value = true;
    await wrapper.vm.$nextTick();
    expect((wrapper.vm.$ as any).setupState.savedFlash).toBe(true);

    mutationStates[0].isSuccess.value = false;
    await wrapper.vm.$nextTick();
    expect((wrapper.vm.$ as any).setupState.savedFlash).toBe(true);

    vi.advanceTimersByTime(600);
    await wrapper.vm.$nextTick();
    expect((wrapper.vm.$ as any).setupState.savedFlash).toBe(false);
    vi.useRealTimers();
  });

  it('模板字段双向绑定覆盖所有控件类型', async () => {
    query.data.value = {
      settings: { select: 'a', bool: false, number: 1, tags: ['x'], text: 'old', nullable: null },
      fields: [
        { key: 'select', label: '选择', type: 'select', options: ['a'] },
        { key: 'bool', label: '布尔', type: 'boolean' },
        { key: 'number', label: '数字', type: 'number' },
        { key: 'tags', label: '标签', type: 'tags' },
        { key: 'nullable', label: '可空', type: 'text', nullable: true },
        { key: 'text', label: '文本', type: 'text' },
      ],
    };
    const wrapper = mountView();
    await wrapper.findComponent(SelectStub).vm.$emit('update:modelValue', 'b');
    await wrapper.findComponent(SwitchStub).vm.$emit('update:modelValue', true);
    await wrapper.findComponent(InputNumberStub).vm.$emit('update:modelValue', 2);
    await wrapper.findComponent(DynamicTagsStub).vm.$emit('update:modelValue', ['y']);
    const inputs = wrapper.findAllComponents(InputStub);
    await inputs[inputs.length - 1].vm.$emit('update:modelValue', 'new');

    const state = (wrapper.vm.$ as any).setupState;
    expect(state.form).toMatchObject({ select: 'b', bool: true, number: 2, text: 'new' });
    expect(state.tagValues.tags).toEqual(['y']);
  });

  it('凭证轮换关闭时隐藏频率，开启后频率默认为正整数', async () => {
    query.data.value = {
      settings: {
        CODEBUDDY_AUTO_ROTATION_ENABLED: false,
        CODEBUDDY_ROTATION_COUNT: 0,
      },
      fields: [
        { key: 'CODEBUDDY_AUTO_ROTATION_ENABLED', label: '凭证轮换', type: 'boolean' },
        { key: 'CODEBUDDY_ROTATION_COUNT', label: '轮换频率', type: 'number', min: 1 },
      ],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.visibleFields.map((field: any) => field.key)).toEqual([
      'CODEBUDDY_AUTO_ROTATION_ENABLED',
    ]);
    expect(state.buildPayload()).toEqual({
      CODEBUDDY_AUTO_ROTATION_ENABLED: false,
      CODEBUDDY_ROTATION_COUNT: 1,
    });

    state.updateBooleanField(state.fields[0], true);
    await wrapper.vm.$nextTick();

    expect(state.form.CODEBUDDY_ROTATION_COUNT).toBe(1);
    expect(state.visibleFields.map((field: any) => field.key)).toEqual([
      'CODEBUDDY_AUTO_ROTATION_ENABLED',
      'CODEBUDDY_ROTATION_COUNT',
    ]);

    state.updateNumberField(state.fields[1], 0);
    expect(state.form.CODEBUDDY_ROTATION_COUNT).toBe(1);
    state.updateNumberField(state.fields[1], 4);
    expect(state.buildPayload()).toMatchObject({ CODEBUDDY_ROTATION_COUNT: 4 });
    state.form.CODEBUDDY_ROTATION_COUNT = null;
    expect(state.buildPayload()).toMatchObject({ CODEBUDDY_ROTATION_COUNT: 1 });
  });

  it('保留环境变量返回的字符串轮换频率', async () => {
    query.data.value = {
      settings: {
        CODEBUDDY_AUTO_ROTATION_ENABLED: true,
        CODEBUDDY_ROTATION_COUNT: '5',
      },
      fields: [
        { key: 'CODEBUDDY_AUTO_ROTATION_ENABLED', label: '凭证轮换', type: 'boolean' },
        { key: 'CODEBUDDY_ROTATION_COUNT', label: '轮换频率', type: 'number', min: 1 },
      ],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.form.CODEBUDDY_ROTATION_COUNT).toBe(5);
    expect(state.buildPayload()).toMatchObject({ CODEBUDDY_ROTATION_COUNT: 5 });
    state.form.CODEBUDDY_ROTATION_COUNT = 'bad';
    expect(state.buildPayload()).toMatchObject({ CODEBUDDY_ROTATION_COUNT: 1 });
  });

  it('错误状态支持重试', async () => {
    query.isError.value = true;
    const wrapper = mountView();

    expect(wrapper.text()).toContain('加载配置失败');
    const retry = wrapper.findAll('button').find((button) => button.text().includes('重试'));
    await retry?.trigger('click');
    expect(query.refetch).toHaveBeenCalledOnce();
  });

  it('使用自建卡片与表单控件组件', async () => {
    query.data.value = {
      settings: { text: 'value' },
      fields: [{ key: 'text', label: '文本', type: 'text' }],
    };
    const wrapper = mountView();
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent(CardStub).exists()).toBe(true);
    expect(wrapper.findComponent(FormStub).exists()).toBe(true);
  });

  it('loading 状态下在独立的最小高度容器中居中显示 CSpin', async () => {
    query.isLoading.value = true;
    const wrapper = mountView();
    const loading = wrapper.get('.settings-loading');

    expect(loading.classes()).toEqual(
      expect.arrayContaining(['grid', 'min-h-24', 'place-items-center']),
    );
    expect(loading.findComponent(SpinStub).exists()).toBe(true);
    expect(wrapper.findComponent(FormStub).exists()).toBe(false);
  });
});
