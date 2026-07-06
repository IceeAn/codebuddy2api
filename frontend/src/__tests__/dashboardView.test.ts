import { mount } from '@vue/test-utils';
import { ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { query, copyMock, useQueryMock } = vi.hoisted(() => {
  const query = {
    data: { __v_isRef: true, value: undefined as unknown },
    error: { __v_isRef: true, value: undefined as unknown },
    isError: { __v_isRef: true, value: false },
    isLoading: { __v_isRef: true, value: false },
    isFetching: { __v_isRef: true, value: false },
    refetch: vi.fn<() => Promise<unknown>>(),
  };
  return {
    query,
    copyMock: vi.fn<(text: string, successMessage?: string) => Promise<boolean>>(),
    useQueryMock: vi.fn<(options: Record<string, unknown>) => typeof query>(() => query),
  };
});

// 用真正的响应式 ref 替换 mock 中的 isError，以便 watch 能跟踪变化
query.isError = ref(false) as unknown as { __v_isRef: true; value: boolean };

vi.mock('@tanstack/vue-query', () => ({
  useQuery: useQueryMock,
}));

vi.mock('../composables/useClipboard', () => ({
  useClipboard: () => ({ copy: copyMock }),
}));

import DashboardView from '../views/DashboardView.vue';
import CButton from '../components/ui/CButton.vue';
import CInput from '../components/ui/CInput.vue';
import CProgress from '../components/ui/CProgress.vue';
import { RefreshButtonStub } from './refreshButtonStub';

function mountView() {
  return mount(DashboardView, {
    global: {
      stubs: {
        RefreshButton: RefreshButtonStub,
        StatTile: {
          props: ['label', 'value', 'tone', 'meta'],
          template:
            '<div class="stat">{{ label }}|{{ value }}|{{ tone }}|{{ meta }}<slot name="corner" /></div>',
        },
        CAlert: {
          inheritAttrs: false,
          template: '<div class="c-alert"><slot /></div>',
        },
        CCard: {
          props: ['title', 'size'],
          template:
            '<section class="c-card"><div v-if="title" class="c-card-title">{{ title }}</div><slot /></section>',
        },
        CDataTable: {
          props: ['columns', 'data', 'loading', 'error', 'bordered', 'size', 'rowKey'],
          template:
            '<div class="c-data-table" :data-error="String(error)" :data-loading="String(loading)"><slot name="empty" /></div>',
        },
        Activity: true,
        CheckCircle2: true,
        Clock3: true,
        KeyRound: true,
        Link: true,
      },
    },
  });
}

describe('DashboardView', () => {
  beforeEach(() => {
    vi.useRealTimers();
    query.data.value = undefined;
    query.error.value = undefined;
    query.isError.value = false;
    query.isLoading.value = false;
    query.isFetching.value = false;
    query.refetch.mockReset();
    copyMock.mockReset();
    useQueryMock.mockClear();
  });

  it('无数据时显示默认状态且复制按钮不执行', async () => {
    const wrapper = mountView();

    expect(wrapper.text()).toContain('异常');
    expect(wrapper.text()).toContain('0/0');
    expect(wrapper.text()).toContain('0');
    expect(wrapper.text()).toContain('-');

    const copyButton = wrapper.findAll('button').find((button) => button.text().includes('复制'));
    await copyButton?.trigger('click');
    expect(copyMock).not.toHaveBeenCalled();
  });

  it('计算调用统计、排序、有效率并复制入口地址', async () => {
    query.data.value = {
      service: 'codebuddy2api',
      status: 'healthy',
      uptime_seconds: 65,
      api_base_url: 'http://localhost/openai/v1',
      credentials: {
        valid: 2,
        total: 3,
        current: { status: 'auto_rotation' },
      },
      usage: {
        model_usage: { small: 2, large: 5 },
        credential_usage: { '/tmp/a.json': 1, '/tmp/b.json': 4 },
      },
    };

    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.totalCalls).toBe(7);
    expect(state.modelRows).toEqual([
      { model: 'large', count: 5 },
      { model: 'small', count: 2 },
    ]);
    expect(state.credentialRows).toEqual([
      { credential: 'b.json', count: 4 },
      { credential: 'a.json', count: 1 },
    ]);
    expect(state.validityPercent).toBe(66);
    expect(wrapper.text()).toContain('运行中');
    expect(wrapper.text()).toContain('2/3');
    expect(wrapper.text()).toContain('自动轮换已启用');
    expect(wrapper.text()).not.toContain('auto_rotation');
    expect(wrapper.text()).toContain('00:01:05');
    expect(wrapper.text()).toContain('服务运行时长');
    expect(wrapper.text()).not.toContain('凭证有效率');

    const copyButton = wrapper.findAll('button').find((button) => button.text().includes('复制'));
    await copyButton?.trigger('click');
    expect(copyMock).toHaveBeenCalledWith('http://localhost/openai/v1', '客户端入口地址已复制');
  });

  it('按总览页刷新策略配置状态查询', () => {
    mountView();

    expect(useQueryMock).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['admin-status'],
        refetchInterval: 600_000,
        refetchOnMount: 'always',
        refetchOnWindowFocus: true,
        staleTime: 180_000,
      }),
    );
  });

  it('服务运行时间基于后端快照在前端逐秒递增', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    query.data.value = {
      status: 'healthy',
      uptime_seconds: 65,
      credentials: { valid: 1, total: 1, current: {} },
      usage: { model_usage: {}, credential_usage: {} },
    };

    const wrapper = mountView();
    expect(wrapper.text()).toContain('00:01:05');

    vi.advanceTimersByTime(2_000);
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('00:01:07');
    wrapper.unmount();
    vi.useRealTimers();
  });

  it('服务运行时间刚好一天时数字处显示天数，备注1显示零点时分秒', () => {
    query.data.value = {
      status: 'healthy',
      uptime_seconds: 86_400,
      credentials: { valid: 1, total: 1, current: {} },
      usage: { model_usage: {}, credential_usage: {} },
    };

    const wrapper = mountView();

    expect(wrapper.text()).toContain('00:00:00|1天|success|服务运行时长');
  });

  it('服务运行时间超过一天时数字处显示天数，备注1显示剩余时分秒', () => {
    query.data.value = {
      status: 'healthy',
      uptime_seconds: 1234 * 86_400 + 3 * 3_600 + 4 * 60 + 5,
      credentials: { valid: 1, total: 1, current: {} },
      usage: { model_usage: {}, credential_usage: {} },
    };

    const wrapper = mountView();

    expect(wrapper.text()).toContain('03:04:05|1234天|success|服务运行时长');
  });

  it('加载失败时显示错误状态并支持重试', async () => {
    query.isError.value = true;
    const wrapper = mountView();

    expect(wrapper.text()).toContain('加载状态失败');
    expect(wrapper.text()).toContain('加载失败');
    const retry = wrapper.findAll('button').find((button) => button.text().includes('重试'));
    await retry?.trigger('click');
    expect(query.refetch).toHaveBeenCalledOnce();
    expect(
      wrapper.findAll('.c-data-table').every((table) => table.attributes('data-error') === 'true'),
    ).toBe(true);
  });

  it('后台刷新时两个统计表格都进入加载状态', () => {
    query.isFetching.value = true;
    const wrapper = mountView();

    expect(
      wrapper
        .findAll('.c-data-table')
        .every((table) => table.attributes('data-loading') === 'true'),
    ).toBe(true);
  });

  it.each([
    [9, 10, 90],
    [2, 10, 20],
  ])('有效率百分比正确传入 CProgress（CProgress 内置阈值色）', (valid, total, percent) => {
    query.data.value = {
      credentials: { valid, total, current: {} },
      usage: {
        model_usage: {},
        credential_usage: { '/tmp/': 1 },
      },
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.credentialRows[0].credential).toBe('/tmp/');
    const progress = wrapper.findComponent(CProgress);
    expect(progress.props('percentage')).toBe(percent);
    expect(progress.props('size')).toBe(52);
    expect(progress.props('strokeWidth')).toBe(5);
    expect(state.validityPercent).toBe(percent);
  });

  it('状态从 error 恢复到 success 时服务状态 StatTile 短暂应用 animate-success', async () => {
    vi.useFakeTimers();
    query.isError.value = true;
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    query.isError.value = false;
    query.data.value = {
      status: 'healthy',
      credentials: { valid: 1, total: 1, current: {} },
      usage: { model_usage: {}, credential_usage: {} },
    };
    await wrapper.vm.$nextTick();

    const serviceTile = wrapper.findAll('.stat').find((el) => el.text().includes('服务状态'))!;
    expect(serviceTile.classes()).toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.statusRecovered).toBe(true);

    vi.advanceTimersByTime(600);
    await wrapper.vm.$nextTick();

    expect(serviceTile.classes()).not.toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.statusRecovered).toBe(false);
    vi.useRealTimers();
  });

  it('客户端入口使用自建输入组组件', () => {
    const wrapper = mountView();
    expect(wrapper.findComponent(CButton).exists()).toBe(true);
    expect(wrapper.findComponent(CInput).exists()).toBe(true);
  });
});
