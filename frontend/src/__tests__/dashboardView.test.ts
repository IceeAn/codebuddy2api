import { mount } from '@vue/test-utils';
import { ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { copyMock, pushMock, statsOverviewMock, useQueryMock } = vi.hoisted(() => ({
  copyMock: vi.fn<(text: string, successMessage?: string) => Promise<boolean>>(),
  pushMock: vi.fn<(location: unknown) => void>(),
  statsOverviewMock: vi.fn<(query: unknown) => Promise<unknown>>(),
  useQueryMock: vi.fn<(options: unknown) => unknown>(),
}));

const makeQuery = () => ({
  data: ref<any>(),
  error: ref<unknown>(),
  isError: ref(false),
  isLoading: ref(false),
  isFetching: ref(false),
  refetch: vi.fn<() => Promise<unknown>>().mockResolvedValue({ isError: false }),
});
const queries = [makeQuery(), makeQuery()];

vi.mock('@tanstack/vue-query', () => ({
  useQuery: useQueryMock,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock('../composables/useClipboard', () => ({
  useClipboard: () => ({ copy: copyMock }),
}));

vi.mock('../api/admin', () => ({
  adminApi: {
    status: vi.fn<() => Promise<unknown>>(),
    statsOverview: statsOverviewMock,
  },
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
          inheritAttrs: false,
          props: ['label', 'value', 'tone', 'meta'],
          emits: ['click'],
          template:
            '<div class="stat" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}|{{ value }}|{{ tone }}|{{ meta }}<slot name="corner" /></div>',
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
    useQueryMock.mockReset();
    useQueryMock.mockImplementationOnce(() => queries[0]).mockImplementationOnce(() => queries[1]);
    for (const query of queries) {
      query.data.value = undefined;
      query.error.value = undefined;
      query.isError.value = false;
      query.isLoading.value = false;
      query.isFetching.value = false;
      query.refetch.mockReset();
      query.refetch.mockResolvedValue({ isError: false });
    }
    copyMock.mockReset();
    pushMock.mockReset();
    statsOverviewMock.mockReset();
    statsOverviewMock.mockResolvedValue({});
  });

  it('无数据时显示默认状态且复制按钮不执行', async () => {
    const wrapper = mountView();

    expect(wrapper.text()).toContain('异常');
    expect(wrapper.text()).toContain('0/0');
    expect(wrapper.text()).toContain('今日请求|0');
    expect(wrapper.text()).toContain('-');

    const copyButton = wrapper.findAll('button').find((button) => button.text().includes('复制'));
    await copyButton?.trigger('click');
    expect(copyMock).not.toHaveBeenCalled();
  });

  it('展示服务、凭证、今日请求和运行时间并复制入口地址', async () => {
    queries[0].data.value = {
      service: 'codebuddy2api',
      status: 'healthy',
      uptime_seconds: 65,
      api_base_url: 'http://localhost/openai/v1',
      credentials: {
        valid: 2,
        total: 3,
        current: { status: 'auto_rotation' },
      },
    };
    queries[1].data.value = { totals: { request_count: 7 } };

    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.todayRequestCount).toBe(7);
    expect(state.validityPercent).toBe(66);
    expect(wrapper.text()).toContain('运行中');
    expect(wrapper.text()).toContain('2/3');
    expect(wrapper.text()).toContain('自动轮换已启用');
    expect(wrapper.text()).not.toContain('auto_rotation');
    expect(wrapper.text()).toContain('今日请求|7');
    expect(wrapper.text()).toContain('00:01:05');
    expect(wrapper.text()).toContain('服务运行时长');
    expect(wrapper.text()).not.toContain('模型使用');
    expect(wrapper.text()).not.toContain('凭证使用');

    const copyButton = wrapper.findAll('button').find((button) => button.text().includes('复制'));
    await copyButton?.trigger('click');
    expect(copyMock).toHaveBeenCalledWith('http://localhost/openai/v1', '客户端入口地址已复制');

    await wrapper
      .findAll('.stat')
      .find((tile) => tile.text().includes('今日请求'))!
      .trigger('click');
    expect(pushMock).toHaveBeenCalledWith({ name: 'stats' });
  });

  it('按总览页策略配置状态和今日统计查询', () => {
    mountView();

    expect(useQueryMock.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        queryKey: ['admin-status'],
        refetchInterval: 600_000,
        refetchOnMount: 'always',
        refetchOnWindowFocus: true,
        staleTime: 180_000,
      }),
    );
    expect(useQueryMock.mock.calls[1]![0]).toEqual(
      expect.objectContaining({
        queryKey: ['admin-stats-overview', 'dashboard-today'],
        refetchOnMount: 'always',
        refetchOnWindowFocus: 'always',
      }),
    );
    const statsOptions = useQueryMock.mock.calls[1]![0] as any;
    statsOptions.queryFn();
    expect(statsOverviewMock).toHaveBeenCalledWith(
      expect.objectContaining({ traffic: 'all', timezone: expect.any(String) }),
    );
  });

  it('手动刷新同时刷新服务和今日统计', async () => {
    const wrapper = mountView();
    const refresh = wrapper.findAll('button').find((button) => button.text().includes('刷新'))!;
    await refresh.trigger('click');

    expect(queries[0].refetch).toHaveBeenCalledOnce();
    expect(queries[1].refetch).toHaveBeenCalledOnce();

    queries[1].refetch.mockResolvedValueOnce({ isError: true });
    const state = (wrapper.vm.$ as any).setupState;
    await expect(state.refreshDashboard()).resolves.toEqual({ isError: true });
  });

  it('服务运行时间基于后端快照在前端逐秒递增', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    queries[0].data.value = {
      status: 'healthy',
      uptime_seconds: 65,
      credentials: { valid: 1, total: 1, current: {} },
    };

    const wrapper = mountView();
    expect(wrapper.text()).toContain('00:01:05');

    vi.advanceTimersByTime(2_000);
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('00:01:07');
    wrapper.unmount();
  });

  it('服务运行时间刚好一天时数字处显示天数，备注1显示零点时分秒', () => {
    queries[0].data.value = {
      status: 'healthy',
      uptime_seconds: 86_400,
      credentials: { valid: 1, total: 1, current: {} },
    };

    const wrapper = mountView();
    expect(wrapper.text()).toContain('00:00:00|1天|success|服务运行时长');
  });

  it('服务运行时间超过一天时数字处显示天数，备注1显示剩余时分秒', () => {
    queries[0].data.value = {
      status: 'healthy',
      uptime_seconds: 1234 * 86_400 + 3 * 3_600 + 4 * 60 + 5,
      credentials: { valid: 1, total: 1, current: {} },
    };

    const wrapper = mountView();
    expect(wrapper.text()).toContain('03:04:05|1234天|success|服务运行时长');
  });

  it('加载失败时显示错误状态并支持重试', async () => {
    queries[0].isError.value = true;
    const wrapper = mountView();

    expect(wrapper.text()).toContain('加载状态失败');
    expect(wrapper.text()).toContain('加载失败');
    const retry = wrapper.findAll('button').find((button) => button.text().includes('重试'))!;
    await retry.trigger('click');
    expect(queries[0].refetch).toHaveBeenCalledOnce();
    expect(queries[1].refetch).toHaveBeenCalledOnce();
  });

  it('今日统计失败时显示占位符和独立重试入口', async () => {
    queries[1].data.value = { totals: { request_count: 99 } };
    queries[1].isError.value = true;
    queries[1].error.value = new Error('stats failed');
    const wrapper = mountView();

    expect(wrapper.text()).toContain('今日请求|-');
    expect(wrapper.text()).toContain('加载今日请求统计失败');
    const retry = wrapper
      .findAll('button')
      .find((button) => button.text().includes('重试今日统计'))!;
    await retry.trigger('click');
    expect(queries[1].refetch).toHaveBeenCalledOnce();
    expect(queries[0].refetch).not.toHaveBeenCalled();
  });

  it.each([
    [9, 10, 90],
    [2, 10, 20],
  ])('有效率百分比正确传入 CProgress（CProgress 内置阈值色）', (valid, total, percent) => {
    queries[0].data.value = { credentials: { valid, total, current: {} } };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const progress = wrapper.findComponent(CProgress);
    expect(progress.props('percentage')).toBe(percent);
    expect(progress.props('size')).toBe(52);
    expect(progress.props('strokeWidth')).toBe(5);
    expect(state.validityPercent).toBe(percent);
  });

  it('状态从 error 恢复到 success 时服务状态 StatTile 短暂应用 animate-success', async () => {
    vi.useFakeTimers();
    queries[0].isError.value = true;
    const wrapper = mountView();
    await wrapper.vm.$nextTick();

    queries[0].isError.value = false;
    queries[0].data.value = {
      status: 'healthy',
      credentials: { valid: 1, total: 1, current: {} },
    };
    await wrapper.vm.$nextTick();

    const serviceTile = wrapper.findAll('.stat').find((el) => el.text().includes('服务状态'))!;
    expect(serviceTile.classes()).toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.statusRecovered).toBe(true);

    vi.advanceTimersByTime(600);
    await wrapper.vm.$nextTick();
    expect(serviceTile.classes()).not.toContain('animate-success');
    expect((wrapper.vm.$ as any).setupState.statusRecovered).toBe(false);
  });

  it('客户端入口使用自建输入组组件', () => {
    const wrapper = mountView();
    expect(wrapper.findComponent(CButton).exists()).toBe(true);
    expect(wrapper.findComponent(CInput).exists()).toBe(true);
  });
});
