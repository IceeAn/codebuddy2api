import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const {
  query,
  mutationOptions,
  mutationStates,
  invalidateQueries,
  toastMock,
  copyMock,
  routeHooks,
} = vi.hoisted(() => ({
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
    isPending: { __v_isRef: true; value: boolean };
    mutate: Mock<(variables?: unknown) => void>;
  }>,
  invalidateQueries: vi.fn<(filters?: unknown) => Promise<void>>(),
  toastMock: {
    success: vi.fn<(message: string, duration?: number) => void>(),
    error: vi.fn<(message: string, duration?: number) => void>(),
    warning: vi.fn<(message: string, duration?: number) => void>(),
    info: vi.fn<(message: string, duration?: number) => void>(),
  },
  copyMock: vi.fn<(text: string, successMessage?: string) => Promise<boolean>>(),
  routeHooks: {
    leaveGuard: undefined as undefined | (() => boolean),
  },
}));

vi.mock('@tanstack/vue-query', () => ({
  useQuery: () => query,
  useQueryClient: () => ({ invalidateQueries }),
  useMutation: (options: Record<string, (...args: any[]) => any>) => {
    mutationOptions.push(options);
    const state = {
      isPending: { __v_isRef: true as const, value: false },
      mutate: vi.fn<(variables?: unknown) => void>(),
    };
    mutationStates.push(state);
    return state;
  },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../composables/useClipboard', () => ({
  useClipboard: () => ({ copy: copyMock }),
}));

vi.mock('vue-router', () => ({
  onBeforeRouteLeave: (guard: () => boolean) => {
    routeHooks.leaveGuard = guard;
  },
}));

import ApiKeysView from '../views/ApiKeysView.vue';
import { adminApi } from '../api/admin';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CInput from '../components/ui/CInput.vue';
import { RefreshButtonStub } from './refreshButtonStub';

const mountedWrappers: Array<ReturnType<typeof mount>> = [];

function mountView() {
  mutationOptions.length = 0;
  mutationStates.length = 0;
  const wrapper = mount(ApiKeysView, {
    global: {
      stubs: {
        RefreshButton: RefreshButtonStub,
        CAlert,
        CCard: {
          props: ['title', 'size'],
          template:
            '<section class="c-card"><div v-if="title" class="c-card-title font-display font-semibold text-md text-text-strong">{{ title }}</div><slot name="header" /><slot name="header-extra" /><slot /></section>',
        },
        CDataTable: {
          props: ['columns', 'data', 'loading', 'error', 'bordered', 'size', 'rowKey'],
          template:
            '<div class="c-data-table" :data-error="String(error)" :data-loading="String(loading)" :data-row-key="rowKey"><slot name="empty" /></div>',
        },
        Copy: true,
        Plus: true,
        Trash2: true,
      },
    },
  });
  mountedWrappers.push(wrapper);
  return wrapper;
}

function createdApiKey(id: string, name: string, apiKey: string) {
  return {
    id,
    name,
    api_key: apiKey,
    preview: `${apiKey.slice(0, 10)}...`,
    created_at: 1,
    last_used_at: null,
  };
}

describe('ApiKeysView', () => {
  beforeEach(() => {
    query.data.value = undefined;
    query.isError.value = false;
    query.isLoading.value = false;
    query.isFetching.value = false;
    query.refetch.mockReset();
    invalidateQueries.mockReset();
    invalidateQueries.mockResolvedValue(undefined);
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    toastMock.warning.mockReset();
    copyMock.mockReset();
    copyMock.mockResolvedValue(true);
    routeHooks.leaveGuard = undefined;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    mountedWrappers.splice(0).forEach((wrapper) => {
      if (wrapper.exists()) wrapper.unmount();
    });
    vi.useRealTimers();
  });

  it('空名称提示并不创建，合法名称创建且防重复提交', () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    state.handleCreate();
    expect(mutationStates[0].mutate).not.toHaveBeenCalled();
    expect(toastMock.warning).toHaveBeenCalledWith('请输入 API Key 名称');

    state.name = ' robot ';
    state.handleCreate();
    expect(mutationStates[0].mutate).toHaveBeenCalledOnce();

    mutationStates[0].isPending.value = true;
    state.handleCreate();
    expect(mutationStates[0].mutate).toHaveBeenCalledOnce();
  });

  it('mutationFn 使用当前名称，输入回车触发创建', async () => {
    const createSpy = vi.spyOn(adminApi, 'createApiKey').mockResolvedValue({} as never);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.name = 'robot';

    await mutationOptions[0].mutationFn();
    expect(createSpy).toHaveBeenCalledWith('robot');

    const nameInput = wrapper.findComponent(CInput);
    nameInput.vm.$emit('update:modelValue', 'robot');
    nameInput.vm.$emit('enter', new KeyboardEvent('keyup', { key: 'Enter' }));
    await wrapper.vm.$nextTick();
    expect(mutationStates[0].mutate).toHaveBeenCalledOnce();
  });

  it('名称限制为 80 字符并显示长度，程序化超长输入也拒绝创建', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const input = wrapper.findComponent(CInput);

    expect(input.props('maxlength')).toBe(80);
    expect(wrapper.text()).toContain('0/80');
    input.vm.$emit('update:modelValue', 'x'.repeat(80));
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain('80/80');
    state.handleCreate();
    expect(mutationStates[0].mutate).toHaveBeenCalledOnce();

    mutationStates[0].mutate.mockClear();
    state.name = 'x'.repeat(81);
    state.handleCreate();
    expect(mutationStates[0].mutate).not.toHaveBeenCalled();
    expect(toastMock.warning).toHaveBeenCalledWith('API Key 名称不能超过 80 个字符');
  });

  it('创建成功追加带名称的一次性 key，不覆盖尚未保存的 key', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.name = 'robot';

    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-secret-1'));
    await mutationOptions[0].onSuccess(createdApiKey('key-2', 'ci', 'sk-secret-2'));
    await wrapper.vm.$nextTick();

    expect(state.pendingApiKeys).toEqual([
      expect.objectContaining({ id: 'key-1', name: 'robot', apiKey: 'sk-secret-1' }),
      expect.objectContaining({ id: 'key-2', name: 'ci', apiKey: 'sk-secret-2' }),
    ]);
    expect(state.name).toBe('');
    expect(toastMock.success).toHaveBeenCalledTimes(2);
    expect(invalidateQueries).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('仅显示一次');
    expect(wrapper.text()).toContain('robot');
    expect(wrapper.text()).toContain('ci');

    state.dismissNewKey('key-1');
    expect(state.pendingApiKeys).toEqual([
      expect.objectContaining({ id: 'key-2', apiKey: 'sk-secret-2' }),
    ]);
  });

  it('生成成功时 alert 短暂应用 animate-success 动效 class', async () => {
    vi.useFakeTimers();
    const wrapper = mountView();

    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-secret'));
    await wrapper.vm.$nextTick();

    const alert = wrapper.findComponent(CAlert);
    expect(alert.classes()).toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.pendingApiKeys[0].justCreated).toBe(true);

    vi.advanceTimersByTime(600);
    await wrapper.vm.$nextTick();

    expect(alert.classes()).not.toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.pendingApiKeys[0].justCreated).toBe(false);
  });

  it('删除成功提示并刷新列表', async () => {
    mountView();
    await mutationOptions[1].onSuccess();

    expect(toastMock.success).toHaveBeenCalledWith('API Key 已删除');
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['admin', 'test-user', 'api-keys'],
    });
  });

  it('删除成功同步移除同 ID 的一次性明文卡片', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'one', 'sk-one'));
    await mutationOptions[0].onSuccess(createdApiKey('key-2', 'two', 'sk-two'));

    await mutationOptions[1].onSuccess({ deleted: true }, 'key-1');

    expect(state.pendingApiKeys).toEqual([
      expect.objectContaining({ id: 'key-2', apiKey: 'sk-two' }),
    ]);
  });

  it('已创建的 API Key 标题使用卡片标题样式', () => {
    const wrapper = mountView();
    const title = wrapper
      .findAll('.c-card-title')
      .find((node) => node.text() === '已创建的 API Key');

    expect(title?.classes()).toEqual(
      expect.arrayContaining(['font-display', 'font-semibold', 'text-md', 'text-text-strong']),
    );
  });

  it('表格接收查询错误状态，避免错误时显示空状态', () => {
    query.isError.value = true;
    const wrapper = mountView();

    expect(wrapper.find('.c-data-table').attributes('data-error')).toBe('true');
    expect(wrapper.find('.c-data-table').attributes('data-row-key')).toBe('id');
  });

  it('后台刷新时表格进入加载状态', () => {
    query.isFetching.value = true;
    const wrapper = mountView();

    expect(wrapper.find('.c-data-table').attributes('data-loading')).toBe('true');
  });

  it('行数据和列渲染覆盖有无最近使用时间', () => {
    query.data.value = {
      api_keys: [
        { id: '1', name: 'one', preview: 'sk-a', created_at: 1, last_used_at: null },
        { id: '2', name: 'two', preview: 'sk-b', created_at: 2, last_used_at: 3 },
      ],
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.rows).toHaveLength(2);
    expect(state.columns[2].render(state.rows[0])).toContain('1970');
    expect(state.columns[3].render(state.rows[0])).toBe('-');
    expect(state.columns[3].render(state.rows[1])).toContain('1970');

    expect(state.columns[4].title).toBe('操作');
    expect(state.columns[4].align).toBe('left');
    expect(state.columns[4].headerClassName).toBe('table-action-header');
    const actions = state.columns[4].render(state.rows[0]);
    expect((actions.props as any).class).toContain('items-center');
    expect((actions.props as any).class).toContain('justify-start');
    expect((actions.props as any).class).toContain('table-action-group');

    const actionNodes = actions.children as any as unknown[];
    expect(actionNodes).toHaveLength(1);
    const deleteTooltip = actionNodes[0] as any;
    expect(deleteTooltip.props.content).toBe('删除 API Key');

    const popconfirm = deleteTooltip.children.default();
    expect(String(popconfirm.props.title)).toContain('one');
    const trigger = popconfirm.children.default() as { props: Record<string, unknown> };
    expect(trigger.props['aria-label']).toBe('删除 API Key');
    expect(trigger.props.shape).toBe('circle');
    expect(trigger.props.class).toBe('table-action-button');
    ((trigger as any).children as any).icon();
    (popconfirm.props as Record<string, () => void>).onConfirm();
    expect(mutationStates[1].mutate).toHaveBeenCalledWith('1');
  });

  it('最近使用时间只格式化到分钟', () => {
    const formatSpy = vi.spyOn(Date.prototype, 'toLocaleString').mockReturnValue('formatted');
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(
      state.columns[3].render({
        id: '1',
        name: 'one',
        preview: 'sk-a',
        created_at: 1,
        last_used_at: 60,
      }),
    ).toBe('formatted');
    expect(formatSpy).toHaveBeenLastCalledWith(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  });

  it('旧 key 复制完成后只关闭自身，不清除期间生成的新 key', async () => {
    query.isError.value = true;
    let finishCopy: (copied: boolean) => void = () => undefined;
    copyMock.mockReturnValue(
      new Promise<boolean>((resolve) => {
        finishCopy = resolve;
      }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-copy-1'));
    await wrapper.vm.$nextTick();

    const buttons = wrapper.findAll('button');
    await buttons.find((button) => button.text().includes('重试'))?.trigger('click');
    const copying = buttons
      .find((button) => button.text().includes('复制并关闭'))
      ?.trigger('click');

    await mutationOptions[0].onSuccess(createdApiKey('key-2', 'ci', 'sk-copy-2'));
    finishCopy(true);
    await copying;
    await wrapper.vm.$nextTick();

    expect(copyMock).toHaveBeenCalledWith('sk-copy-1', 'API Key 已复制');
    expect(state.pendingApiKeys).toEqual([
      expect.objectContaining({ id: 'key-2', apiKey: 'sk-copy-2' }),
    ]);
  });

  it('复制新 key 失败时只更新对应提示并允许手动关闭', async () => {
    copyMock.mockResolvedValue(false);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-copy-1'));
    await mutationOptions[0].onSuccess(createdApiKey('key-2', 'ci', 'sk-copy-2'));
    await wrapper.vm.$nextTick();

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('复制并关闭'))
      ?.trigger('click');
    await wrapper.vm.$nextTick();

    expect(state.pendingApiKeys[0].copyFailed).toBe(true);
    expect(state.pendingApiKeys[1].copyFailed).toBe(false);
    const labels = wrapper.findAll('button').map((button) => button.text().trim());
    expect(labels).toEqual(expect.arrayContaining(['复制', '我已保存']));
    expect(labels).toContain('复制并关闭');

    copyMock.mockResolvedValue(true);
    await wrapper
      .findAll('button')
      .find((button) => button.text().trim() === '复制')
      ?.trigger('click');
    expect(copyMock).toHaveBeenLastCalledWith('sk-copy-1', 'API Key 已复制');

    await wrapper
      .findAll('button')
      .find((button) => button.text().trim() === '我已保存')
      ?.trigger('click');
    expect(state.pendingApiKeys).toEqual([
      expect.objectContaining({ id: 'key-2', apiKey: 'sk-copy-2' }),
    ]);
  });

  it('有待保存 key 时阻止路由离开，确认后允许离开', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mountView();

    expect(routeHooks.leaveGuard?.()).toBe(true);

    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-secret'));
    expect(routeHooks.leaveGuard?.()).toBe(false);
    expect(confirm).toHaveBeenCalledWith(
      '仍有未保存的 API Key，离开后将无法再次查看。确定要离开吗？',
    );

    confirm.mockReturnValue(true);
    expect(routeHooks.leaveGuard?.()).toBe(true);
  });

  it('有待保存 key 时触发刷新或关闭提示，全部关闭后不再提示', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    const cleanEvent = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(cleanEvent);
    expect(cleanEvent.defaultPrevented).toBe(false);

    await mutationOptions[0].onSuccess(createdApiKey('key-1', 'robot', 'sk-secret'));
    const pendingEvent = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(pendingEvent);
    expect(pendingEvent.defaultPrevented).toBe(true);

    state.dismissNewKey('key-1');
    const savedEvent = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(savedEvent);
    expect(savedEvent.defaultPrevented).toBe(false);

    await mutationOptions[0].onSuccess(createdApiKey('key-2', 'ci', 'sk-secret-2'));
    wrapper.unmount();
    const unmountedEvent = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unmountedEvent);
    expect(unmountedEvent.defaultPrevented).toBe(false);
  });

  it('使用自建输入与按钮组件', () => {
    const wrapper = mountView();
    expect(wrapper.findComponent(CButton).exists()).toBe(true);
    expect(wrapper.findComponent(CInput).exists()).toBe(true);
  });
});
