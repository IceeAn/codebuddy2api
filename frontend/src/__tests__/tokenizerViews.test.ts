import { mount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TokenizerSettings from '../components/TokenizerSettings.vue';
import TokenizerConsole from '../components/TokenizerConsole.vue';
import { tokenizerApi } from '../api/tokenizer';
const data = {
  builtin_resources: [
    {
      id: 'official',
      model: 'glm-5.2',
      repo: 'zai-org/GLM-5.2',
      revision: 'abc',
      encoder: 'jinja',
      license: 'MIT',
    },
  ],
  pending_models: ['glm-5.0'],
  user_resources: [],
  mappings: {},
  encoders: ['auto', 'jinja'],
  limits: { upload_max_bytes: 67108864, timeout_seconds: 30, queue_timeout_seconds: 30 },
};
function render(component: typeof TokenizerSettings | typeof TokenizerConsole) {
  return mount(component, {
    global: {
      plugins: [
        createPinia(),
        [
          VueQueryPlugin,
          { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        ],
      ],
    },
  });
}
afterEach(() => vi.restoreAllMocks());
describe('Tokenizer 页面', () => {
  it('区分官方资源与待配置模型', async () => {
    vi.spyOn(tokenizerApi, 'resources').mockResolvedValue(data);
    const wrapper = render(TokenizerSettings);
    await flushPromises();
    expect(wrapper.text()).toContain('glm-5.2');
    expect(wrapper.text()).toContain('glm-5.0');
    wrapper.unmount();
  });
  it('计数页面无需加载上游模型', async () => {
    vi.spyOn(tokenizerApi, 'resources').mockResolvedValue(data);
    const wrapper = render(TokenizerConsole);
    await flushPromises();
    expect(wrapper.text()).toContain('纯文本');
    expect(wrapper.text()).toContain('Anthropic');
    expect(wrapper.get('.c-radio-group').classes()).toContain('justify-self-start');
    wrapper.unmount();
  });
});

import CInput from '../components/ui/CInput.vue';
import CSelect from '../components/ui/CSelect.vue';
import CButton from '../components/ui/CButton.vue';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import RefreshButton from '../components/RefreshButton.vue';
import type { TokenizerResources } from '../api/tokenizer';
import type { VueWrapper } from '@vue/test-utils';

function button(wrapper: VueWrapper, text: string) {
  return wrapper.findAllComponents(CButton).find((item) => item.text() === text)!;
}
async function choose(wrapper: VueWrapper, files: File[]) {
  const input = wrapper.get('input[type=file]');
  Object.defineProperty(input.element, 'files', { value: files, configurable: true });
  await input.trigger('change');
}
const userResource = {
  id: 'r1',
  name: '用户词表',
  revision: 'xyz',
  profile: { encoder: 'auto' },
  origin: null,
  update_available: true,
};

it('编辑上传字段、限制大小、验证并保存资源', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockResolvedValue({
    ...data,
    limits: { ...data.limits, upload_max_bytes: 5 },
  });
  const upload = vi.spyOn(tokenizerApi, 'upload').mockResolvedValue(userResource);
  const wrapper = render(TokenizerSettings);
  await flushPromises();
  await button(wrapper, '上传并验证').trigger('click');
  expect(wrapper.text()).toContain('请输入资源名称');
  wrapper.findAllComponents(CInput)[0].vm.$emit('update:modelValue', '词表');
  await choose(wrapper, [new File(['123456'], 'tokenizer.json')]);
  await button(wrapper, '上传并验证').trigger('click');
  expect(wrapper.text()).toContain('文件总大小超过');
  wrapper.findAllComponents(CSelect)[0].vm.$emit('update:modelValue', 'kimi');
  wrapper.findAllComponents(CSelect)[1].vm.$emit('update:modelValue', 'kimi_k2');
  wrapper.findAllComponents(CInput)[1].vm.$emit('update:modelValue', 'default');
  await choose(wrapper, [new File(['x'], 'tiktoken.model')]);
  await button(wrapper, '上传并验证').trigger('click');
  await flushPromises();
  expect(upload).toHaveBeenCalledOnce();
  const form = upload.mock.calls[0][0];
  expect(form.get('encoder')).toBe('kimi_k2');
  expect(form.get('format')).toBe('kimi');
  expect(form.get('template_name')).toBe('default');
  expect(wrapper.findAllComponents(CInput)[0].props('modelValue')).toBe('');
  wrapper.unmount();
});

it('映射验证、保存、删除引用及快照操作', async () => {
  let current: TokenizerResources = {
    ...data,
    user_resources: [userResource],
    limits: { ...data.limits, max_mappings: 3 },
  };
  vi.spyOn(tokenizerApi, 'resources').mockImplementation(async () => current);
  const save = vi.spyOn(tokenizerApi, 'mappings').mockImplementation(async (mappings) => {
    current = { ...current, mappings };
    return current;
  });
  const snapshot = vi.spyOn(tokenizerApi, 'snapshot').mockResolvedValue(userResource);
  const remove = vi.spyOn(tokenizerApi, 'delete').mockResolvedValue({ deleted: true });
  const wrapper = render(TokenizerSettings);
  await flushPromises();
  expect(wrapper.text()).toContain('内置资源已有更新');
  await button(wrapper, '创建快照').trigger('click');
  await flushPromises();
  expect(snapshot).toHaveBeenCalledWith('official');
  await button(wrapper, '添加映射').trigger('click');
  await button(wrapper, '保存映射').trigger('click');
  expect(wrapper.text()).toContain('完整且不重复');
  wrapper.findAllComponents(CInput)[2].vm.$emit('update:modelValue', ' model ');
  await button(wrapper, '保存映射').trigger('click');
  expect(save).not.toHaveBeenCalled();
  wrapper.findAllComponents(CSelect)[2].vm.$emit('update:modelValue', 'r1');
  await flushPromises();
  expect(button(wrapper, '删除').props('disabled')).toBe(true);
  await button(wrapper, '添加映射').trigger('click');
  wrapper.findAllComponents(CInput)[3].vm.$emit('update:modelValue', 'model');
  wrapper.findAllComponents(CSelect)[3].vm.$emit('update:modelValue', 'r1');
  await button(wrapper, '保存映射').trigger('click');
  expect(save).not.toHaveBeenCalled();
  await button(wrapper, '移除').trigger('click');
  await button(wrapper, '保存映射').trigger('click');
  await flushPromises();
  expect(save).toHaveBeenCalledWith({ model: 'r1' });
  await button(wrapper, '移除').trigger('click');
  expect(button(wrapper, '删除').props('disabled')).toBe(true);
  await button(wrapper, '保存映射').trigger('click');
  await flushPromises();
  await button(wrapper, '删除').trigger('click');
  await flushPromises();
  expect(remove).toHaveBeenCalledWith('r1');
  wrapper.unmount();
});

it('未保存字段保护切换、刷新及窗口关闭', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockResolvedValue(data);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const wrapper = render(TokenizerSettings);
  await flushPromises();
  const vm = wrapper.vm as unknown as { confirmDiscard: () => boolean };
  expect(vm.confirmDiscard()).toBe(true);
  const clean = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  const inputs = wrapper.findAllComponents(CInput);
  for (const [component, value] of [
    [inputs[0], 'name'],
    [inputs[1], 'other'],
  ] as const) {
    component.vm.$emit('update:modelValue', value);
    await flushPromises();
    expect(vm.confirmDiscard()).toBe(false);
    confirm.mockReturnValueOnce(true);
    expect(vm.confirmDiscard()).toBe(true);
  }
  for (const component of wrapper.findAllComponents(CSelect)) {
    component.vm.$emit('update:modelValue', 'other');
    await flushPromises();
    expect(vm.confirmDiscard()).toBe(false);
    confirm.mockReturnValueOnce(true);
    expect(vm.confirmDiscard()).toBe(true);
  }
  await choose(wrapper, [new File(['x'], 'tokenizer.json')]);
  const dirty = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
  const refresh = wrapper.getComponent(RefreshButton).props('query');
  expect(await refresh.refetch()).toEqual({ isError: true });
  confirm.mockReturnValue(true);
  await refresh.refetch();
  await flushPromises();
  expect(vm.confirmDiscard()).toBe(true);
  wrapper.unmount();
});

it('后台查询变化保留映射草稿，失败显示重试提示', async () => {
  const resources = vi.spyOn(tokenizerApi, 'resources').mockResolvedValue({
    ...data,
    user_resources: [{ ...userResource, update_available: false }],
    mappings: { existing: 'r1' },
  });
  const wrapper = render(TokenizerSettings);
  await flushPromises();
  await button(wrapper, '添加映射').trigger('click');
  const snapshot = vi.spyOn(tokenizerApi, 'snapshot').mockResolvedValue(userResource);
  resources.mockResolvedValue({ ...data, user_resources: [userResource] });
  await button(wrapper, '创建快照').trigger('click');
  await flushPromises();
  expect(snapshot).toHaveBeenCalled();
  expect(wrapper.findAllComponents(CInput)).toHaveLength(4);
  wrapper.unmount();
  resources.mockRejectedValue(new Error('失败'));
  const failed = render(TokenizerSettings);
  await flushPromises();
  expect(failed.text()).toContain('加载分词配置失败');
  failed.unmount();
});

it('纯文本和 Anthropic JSON 请求计数、错误及离线处理', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockResolvedValue({ ...data, mappings: { custom: 'r1' } });
  const count = vi
    .spyOn(tokenizerApi, 'count')
    .mockResolvedValue({ input_tokens: 3, method: 'text', source: 'user', revision: 'r1' });
  const wrapper = render(TokenizerConsole);
  await flushPromises();
  await button(wrapper, '计数').trigger('click');
  expect(wrapper.text()).toContain('请选择或输入模型');
  wrapper.getComponent(CSelect).vm.$emit('update:modelValue', 'custom');
  wrapper.findAllComponents(CInput)[1].vm.$emit('update:modelValue', 'hello');
  await button(wrapper, '计数').trigger('click');
  await flushPromises();
  expect(count.mock.calls[0].slice(0, 3)).toEqual([
    'tokenizer',
    { model: 'custom', text: 'hello' },
    90000,
  ]);
  expect(wrapper.text()).toContain('"input_tokens": 3');
  wrapper.getComponent(CRadioGroup).vm.$emit('update:modelValue', 'anthropic');
  await flushPromises();
  for (const json of ['{', 'null', '[]', '42']) {
    wrapper.findAllComponents(CInput)[1].vm.$emit('update:modelValue', json);
    await button(wrapper, '计数').trigger('click');
    await flushPromises();
  }
  expect(wrapper.text()).toContain('请求 JSON 必须是对象');
  wrapper
    .findAllComponents(CInput)[1]
    .vm.$emit('update:modelValue', '{"model":"original","messages":[]}');
  await button(wrapper, '计数').trigger('click');
  await flushPromises();
  expect(count.mock.calls.at(-1)![1]).toEqual({ model: 'custom', messages: [] });
  wrapper.findAllComponents(CInput)[0].vm.$emit('update:modelValue', '');
  await button(wrapper, '计数').trigger('click');
  await flushPromises();
  expect(count.mock.calls.at(-1)![1]).toEqual({ model: 'original', messages: [] });
  count.mockRejectedValueOnce(new Error('失败'));
  await button(wrapper, '计数').trigger('click');
  await flushPromises();
  expect(wrapper.text()).toContain('失败');
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  await button(wrapper, '计数').trigger('click');
  expect(wrapper.text()).toContain('当前离线');
  wrapper.unmount();
});

it('计数期间防重复发送，卸载取消请求', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockResolvedValue(data);
  let resolve!: (value: {
    input_tokens: number;
    method: null;
    source: null;
    revision: null;
  }) => void;
  const count = vi.spyOn(tokenizerApi, 'count').mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const wrapper = render(TokenizerConsole);
  await flushPromises();
  wrapper.findAllComponents(CInput)[0].vm.$emit('update:modelValue', 'custom');
  button(wrapper, '计数').vm.$emit('click');
  button(wrapper, '计数').vm.$emit('click');
  await flushPromises();
  expect(count).toHaveBeenCalledOnce();
  const signal = count.mock.calls[0][3]!;
  wrapper.unmount();
  expect(signal.aborted).toBe(true);
  resolve({ input_tokens: 1, method: null, source: null, revision: null });
  await flushPromises();
});

it('计数配置失败时显示加载错误', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockRejectedValue(new Error('加载失败'));
  const wrapper = render(TokenizerConsole);
  await flushPromises();
  expect(wrapper.text()).toContain('加载本地模型映射失败');
  wrapper.unmount();
});

it('已授权上传完成时页面可以已经卸载', async () => {
  vi.spyOn(tokenizerApi, 'resources').mockResolvedValue(data);
  let resolve!: (value: typeof userResource) => void;
  vi.spyOn(tokenizerApi, 'upload').mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const wrapper = render(TokenizerSettings);
  await flushPromises();
  wrapper.findAllComponents(CInput)[0].vm.$emit('update:modelValue', '词表');
  await choose(wrapper, [new File(['x'], 'tokenizer.json')]);
  await button(wrapper, '上传并验证').trigger('click');
  await flushPromises();
  expect(button(wrapper, '上传并验证').props('disabled')).toBe(true);
  wrapper.unmount();
  resolve(userResource);
  await flushPromises();
});
