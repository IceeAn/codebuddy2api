import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, nextTick, ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BotMessageSquare, MessageSquareText } from '@lucide/vue';

const { useQueryMock, statsOverviewMock, statsRequestsMock, statsDetailMock, statsDimensionsMock } =
  vi.hoisted(() => ({
    useQueryMock: vi.fn<(options: unknown) => unknown>(),
    statsOverviewMock: vi.fn<(query: unknown) => Promise<unknown>>(),
    statsRequestsMock: vi.fn<(query: unknown) => Promise<any>>(),
    statsDetailMock: vi.fn<(requestId: number) => Promise<any>>(),
    statsDimensionsMock: vi.fn<(dimension: string, query: unknown) => Promise<any>>(),
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

vi.mock('../api/admin', () => ({
  adminApi: {
    statsOverview: statsOverviewMock,
    statsRequests: statsRequestsMock,
    statsRequestDetail: statsDetailMock,
    statsDimensions: statsDimensionsMock,
  },
}));

import StatsView from '../views/StatsView.vue';
import StatTile from '../components/StatTile.vue';
import CProgress from '../components/ui/CProgress.vue';
import CTooltip from '../components/ui/CTooltip.vue';

const overview = {
  totals: {
    request_count: 12,
    success_rate: 0.916,
    input_tokens: 9000,
    output_tokens: 3345,
    total_tokens: 12345,
    cache_hit_tokens: 6000,
    cache_miss_tokens: 3000,
    total_credit: 3.5,
    p95_first_output_ms: 10000,
    p95_total_ms: 30000,
    usage_coverage: 0.75,
  },
  series: [
    {
      period_start: 1_767_225_600,
      request_count: 4,
      success_rate: 1,
      total_tokens: 100,
      total_credit: 1,
      p95_first_output_ms: 500,
      p95_total_ms: 2000,
    },
  ],
  dimensions: {
    models: ['glm'],
    api_keys: [{ id: 'key-1', name: '机器人' }],
    credentials: [{ id: 'cred-1', label: '账号 A' }],
    outcomes: ['success', 'failure', 'cancelled'],
  },
  breakdowns: {
    models: [
      {
        model: 'glm',
        request_count: 10,
        success_rate: 0.9,
        total_tokens: 1000,
        total_credit: 2,
        p95_total_ms: 1000,
        usage_coverage: 1,
      },
    ],
    api_keys: [
      {
        id: 'key-1',
        name: '机器人',
        request_count: 8,
        success_rate: 1,
        total_tokens: 800,
        total_credit: 1,
        p95_total_ms: 1000,
        usage_coverage: 1,
      },
    ],
    credentials: [
      {
        id: 'cred-1',
        label: '账号 A',
        request_count: 7,
        success_rate: 0.8,
        total_tokens: 700,
        total_credit: 0.5,
        p95_total_ms: 1000,
        usage_coverage: 1,
      },
    ],
  },
  data_quality: {
    usage_coverage: 0.75,
    dropped_events: 2,
    detail_retention_days: 90,
    boundary_precision: 'hourly_approximate',
  },
};

const firstRequest = {
  id: 10,
  started_at: 1_767_225_600,
  source: 'external_api',
  requested_model: 'provider/glm',
  upstream_model: 'glm',
  api_key_id: 'key-1',
  api_key_name: '机器人',
  credential_id: 'cred-1',
  credential_label: '账号 A',
  outcome: 'success',
  http_status: 200,
  result_status: 'completed',
  error_type: null,
  client_stream: true,
  thinking_mode: 'enabled',
  message_count: 2,
  tool_count: 1,
  request_bytes: 100,
  response_bytes: 200,
  retry_count: 0,
  tool_call_count: 1,
  finish_reason: 'stop',
  input_tokens: 10,
  output_tokens: 20,
  total_tokens: 30,
  reasoning_tokens: 5,
  cache_hit_tokens: null,
  cache_miss_tokens: null,
  cache_write_tokens: null,
  credit: 0.5,
  duration_ms: 1500,
  first_event_ms: 100,
  first_reasoning_ms: 200,
  first_content_ms: 300,
  first_output_ms: 200,
};

function dimensionItem(id: string, label = id) {
  return {
    id,
    label,
    request_count: 2,
    success_rate: 1,
    total_tokens: 10,
    total_credit: 0.1,
    p95_first_output_ms: 100,
    p95_first_output_ms_overflow: false,
    p95_total_ms: 250,
    p95_total_ms_overflow: false,
    usage_coverage: 1,
  };
}

const PassthroughStub = defineComponent({
  inheritAttrs: false,
  template:
    '<section v-bind="$attrs"><slot name="header-extra"/><slot/><slot name="empty"/></section>',
});
const CardStub = defineComponent({
  inheritAttrs: false,
  props: ['title'],
  template:
    '<section v-bind="$attrs"><slot name="header"><h2 v-if="title">{{ title }}</h2></slot><slot name="header-extra"/><slot/><slot name="empty"/></section>',
});
const DrawerStub = defineComponent({
  inheritAttrs: false,
  props: ['title', 'open'],
  emits: ['update:open'],
  template:
    '<aside v-if="open" v-bind="$attrs"><h2>{{ title }}</h2><button class="drawer-close" @click="$emit(\'update:open\', false)">关闭</button><slot/></aside>',
});
const RadioGroupStub = defineComponent({
  inheritAttrs: false,
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<div class="radio-group" v-bind="$attrs" @click="$emit(\'update:modelValue\', \'external\')"><slot/></div>',
});

function mountView() {
  return mount(StatsView, {
    global: {
      stubs: {
        CAlert: PassthroughStub,
        CCard: CardStub,
        CButton: {
          inheritAttrs: false,
          props: ['loading', 'disabled', 'variant', 'size'],
          emits: ['click'],
          template:
            '<button v-bind="$attrs" :disabled="disabled || loading" @click="$emit(\'click\', $event)"><slot name="icon"/><slot/></button>',
        },
        CSelect: {
          props: ['modelValue', 'options', 'placeholder', 'footerActionLabel'],
          emits: ['update:modelValue', 'footer-action'],
          template:
            '<div><select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option value="">全部</option><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select><button v-if="footerActionLabel" class="c-select-footer-action" @click="$emit(\'footer-action\')">{{ footerActionLabel }}</button></div>',
        },
        CRadioGroup: RadioGroupStub,
        CRadioButton: { props: ['value', 'label'], template: '<button>{{ label }}</button>' },
        CDrawer: DrawerStub,
        CTag: PassthroughStub,
        RefreshButton: {
          props: ['query', 'label'],
          template:
            '<button class="refresh" @click="query.refetch()">{{ label || \'刷新\' }}</button>',
        },
        StatsTrendChart: {
          props: ['points', 'metric', 'timezone'],
          template: '<div class="chart">{{ metric }}|{{ points.length }}|{{ timezone }}</div>',
        },
        Activity: true,
        BadgeCheck: true,
        CircleDollarSign: true,
        Clock3: true,
        Gauge: true,
        TimerReset: true,
      },
    },
  });
}

describe('StatsView', () => {
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
      query.refetch.mockClear();
      query.refetch.mockResolvedValue({ isError: false });
    }
    statsOverviewMock.mockReset();
    statsRequestsMock.mockReset();
    statsRequestsMock.mockResolvedValue({
      items: [],
      page: 1,
      page_size: 20,
      total: 0,
      total_pages: 0,
      snapshot_id: 0,
      snapshot_time: 1,
    });
    statsDetailMock.mockReset();
    statsDimensionsMock.mockReset();
    statsDimensionsMock.mockResolvedValue({ items: [], next_cursor: null });
  });

  it('按默认七天、浏览器时区和焦点策略创建两个查询', () => {
    const wrapper = mountView();

    expect(useQueryMock).toHaveBeenCalledTimes(2);
    for (const call of useQueryMock.mock.calls) {
      expect(call[0]).toEqual(
        expect.objectContaining({ refetchOnMount: 'always', refetchOnWindowFocus: 'always' }),
      );
    }
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.rangePreset).toBe('7d');
    expect(state.filters.traffic).toBe('all');
    expect(state.queryParams.timezone).toBeTruthy();
    expect(state.queryParams.end_at - state.queryParams.start_at).toBe(7 * 86_400);
    const overviewOptions = useQueryMock.mock.calls[0]![0] as any;
    const requestsOptions = useQueryMock.mock.calls[1]![0] as any;
    expect(overviewOptions.queryKey.value[0]).toBe('admin-stats-overview');
    expect(requestsOptions.queryKey.value[0]).toBe('admin-stats-requests');
    const previousOverview = { marker: 'overview' };
    const previousRequests = { marker: 'requests' };
    expect(overviewOptions.placeholderData(previousOverview)).toBe(previousOverview);
    expect(requestsOptions.placeholderData(previousRequests)).toBe(previousRequests);
    overviewOptions.queryFn();
    requestsOptions.queryFn();
    const overviewParams = statsOverviewMock.mock.calls[0]![0] as any;
    expect(overviewParams).toEqual(
      expect.objectContaining({ timezone: state.queryParams.timezone, traffic: 'all' }),
    );
    expect(overviewParams.end_at - overviewParams.start_at).toBe(7 * 86_400);
    expect(statsRequestsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        timezone: state.queryParams.timezone,
        traffic: 'all',
        page: 1,
        page_size: 20,
      }),
    );
    expect(state.combinedFetching).toBe(false);
    queries[1].isFetching.value = true;
    expect(state.combinedFetching).toBe(true);
    queries[0].isFetching.value = true;
    expect(state.combinedFetching).toBe(true);
  });

  it('预设范围在手动或聚焦刷新时滚动，自定义范围保持不变', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-08T00:00:00Z'));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const overviewOptions = useQueryMock.mock.calls[0]![0] as any;
    const requestsOptions = useQueryMock.mock.calls[1]![0] as any;

    overviewOptions.queryFn();
    const firstOverviewRange = statsOverviewMock.mock.calls.at(-1)![0] as any;
    vi.setSystemTime(new Date('2026-01-08T01:00:00Z'));
    requestsOptions.queryFn();
    const refreshedRequestRange = statsRequestsMock.mock.calls.at(-1)![0] as any;
    expect(refreshedRequestRange.end_at).toBe(firstOverviewRange.end_at + 3600);
    expect(refreshedRequestRange.end_at - refreshedRequestRange.start_at).toBe(7 * 86_400);

    state.selectRange('custom');
    state.customStart = '2026-01-01T00:00';
    state.customEnd = '2026-01-02T00:00';
    state.applyCustomRange();
    const customRange = { start_at: state.queryParams.start_at, end_at: state.queryParams.end_at };
    vi.setSystemTime(new Date('2026-01-09T00:00:00Z'));
    overviewOptions.queryFn();
    expect(statsOverviewMock).toHaveBeenLastCalledWith(expect.objectContaining(customRange));
  });

  it('展示六项 KPI，将成功率与缓存命中率放入对应卡片的环形图', async () => {
    queries[0].data.value = overview;
    queries[1].data.value = { items: [firstRequest], next_cursor: 9 };
    const wrapper = mountView();

    expect(wrapper.text()).toContain('请求数');
    expect(wrapper.text()).toContain('12');
    const tiles = wrapper.findAllComponents(StatTile);
    const tileLabels = tiles.map((tile) => tile.props('label'));
    expect(tileLabels).toContain('请求数');
    expect(tileLabels).not.toContain('成功率');
    expect(tileLabels).toContain('输入 Token');
    expect(tileLabels).toContain('输出 Token');
    expect(tiles.find((tile) => tile.props('label') === '输入 Token')!.props('icon')).toBe(
      MessageSquareText,
    );
    expect(tiles.find((tile) => tile.props('label') === '输出 Token')!.props('icon')).toBe(
      BotMessageSquare,
    );
    expect(tiles.find((tile) => tile.props('label') === '输入 Token')!.classes()).toEqual(
      expect.arrayContaining(['sm:order-3', 'xl:order-2']),
    );
    expect(tiles.find((tile) => tile.props('label') === '输出 Token')!.classes()).toEqual(
      expect.arrayContaining(['sm:order-4', 'xl:order-3']),
    );
    expect(tiles.find((tile) => tile.props('label') === '积分消耗')!.classes()).toEqual(
      expect.arrayContaining(['sm:order-2', 'xl:order-4']),
    );
    expect(tiles.find((tile) => tile.props('label') === 'p95 首个有效输出')!.classes()).toEqual(
      expect.arrayContaining(['sm:order-5', 'xl:order-5']),
    );
    expect(tiles.find((tile) => tile.props('label') === 'p95 总耗时')!.classes()).toEqual(
      expect.arrayContaining(['sm:order-6', 'xl:order-6']),
    );
    expect(wrapper.text()).toContain('9.00k');
    expect(wrapper.text()).toContain('3.35k');
    const progressRings = wrapper.findAllComponents(CProgress);
    expect(progressRings).toHaveLength(2);
    expect(progressRings[0]!.props()).toEqual(
      expect.objectContaining({
        percentage: 92,
        variant: 'success-rate',
        size: 52,
        strokeWidth: 5,
      }),
    );
    expect(progressRings[1]!.props()).toEqual(
      expect.objectContaining({ percentage: 67, variant: 'cache-hit', size: 52, strokeWidth: 5 }),
    );
    expect(wrapper.text()).toContain('命中 6.00k / 未命中 3.00k');
    expect(wrapper.text()).toContain('积分消耗');
    expect(wrapper.text()).not.toContain('credit 消耗');
    expect(wrapper.text()).toContain('p95 首个有效输出');
    expect(wrapper.text()).toContain('p95 总耗时');
    expect(wrapper.text()).toContain('< 10 s');
    expect(wrapper.text()).toContain('< 30 s');
    expect(wrapper.text()).not.toContain('10.00 s');
    expect(wrapper.text()).not.toContain('30.00 s');
    expect(wrapper.text()).toContain('usage 覆盖率 75.0%');
    expect(wrapper.text()).not.toContain('数据质量提示');
    expect(wrapper.text()).not.toContain('2 条统计事件写入失败');
    expect(wrapper.text()).not.toContain('按 Asia/Taipei 聚合显示');
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.modelOptions[0]).toEqual({ value: '', label: '全部模型' });
    expect(state.apiKeyOptions[0]).toEqual({ value: '', label: '全部 API Key' });
    expect(state.credentialOptions[0]).toEqual({ value: '', label: '全部凭证' });
    expect(state.outcomeOptions[0]).toEqual({ value: '', label: '全部结果' });
    expect(wrapper.text()).toContain('模型排行');
    expect(wrapper.text()).toContain('glm');
    expect(wrapper.text()).toContain('provider/glm');
    expect(wrapper.find('.chart').text()).toContain('request_count|1');
    expect(wrapper.get('.stats-trend-metric-select').classes()).toContain('lg:hidden');
    expect(wrapper.get('.stats-trend-metric-buttons').classes()).toEqual(
      expect.arrayContaining(['hidden', 'lg:flex']),
    );

    const selects = wrapper.findAll('select');
    expect(selects.some((select) => select.text().includes('glm'))).toBe(true);
    expect(selects.some((select) => select.text().includes('机器人'))).toBe(true);
    expect(selects.some((select) => select.text().includes('账号 A'))).toBe(true);
    await selects[0]!.setValue('glm');
    await selects[1]!.setValue('key-1');
    await selects[2]!.setValue('cred-1');
    await selects[3]!.setValue('failure');
    expect(state.queryParams).toEqual(
      expect.objectContaining({
        model: 'glm',
        api_key_id: 'key-1',
        credential_id: 'cred-1',
        outcome: 'failure',
      }),
    );
    expect(state.breakdownRows('api_keys')[0].label).toBe('机器人');
    expect(state.breakdownRows('credentials')[0].label).toBe('账号 A');
    expect(state.outcomeLabel('cancelled')).toBe('客户端中断');
    expect(state.outcomeLabel('future')).toBe('future');
    expect(state.outcomeTag('success')).toBe('success');
    expect(state.outcomeTag('cancelled')).toBe('warning');
    expect(state.outcomeTag('failure')).toBe('error');
    expect(state.outcomeTag('future')).toBe('default');

    const tokenButton = wrapper.findAll('button').find((button) => button.text() === 'Token');
    await tokenButton?.trigger('click');
    expect(wrapper.find('.chart').text()).toContain('total_tokens|1');
    await wrapper.get('.stats-trend-metric-select select').setValue('total_credit');
    expect(wrapper.find('.chart').text()).toContain('total_credit|1');
  });

  it('成功率与缓存数据未知时环形图显示占位符', () => {
    queries[0].data.value = {
      ...overview,
      totals: {
        ...overview.totals,
        success_rate: null,
        cache_hit_tokens: null,
        cache_miss_tokens: null,
      },
    };
    const wrapper = mountView();
    const progressRings = wrapper.findAllComponents(CProgress);

    expect(progressRings).toHaveLength(2);
    expect(progressRings[0]!.props('percentage')).toBe(0);
    expect(progressRings[0]!.props('label')).toBe('-');
    expect(progressRings[1]!.props('percentage')).toBe(0);
    expect(progressRings[1]!.props('label')).toBe('-');
    expect(wrapper.text()).toContain('暂无缓存命中数据');
  });

  it('两个百分比环悬浮短延迟后显示具体统计内容', async () => {
    vi.useFakeTimers();
    queries[0].data.value = overview;
    const wrapper = mountView();
    const tooltips = wrapper.findAllComponents(CTooltip);

    expect(tooltips).toHaveLength(2);
    expect(tooltips[0]!.props('delay')).toBe(300);
    expect(tooltips[0]!.props('content')).toBe('成功 11 / 总请求 12（91.6%）');
    expect(tooltips[1]!.props('content')).toBe('命中 6.00k / 未命中 3.00k（66.7%）');

    await tooltips[0]!.trigger('mouseenter');
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')).toBeNull();
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain(
      '成功 11 / 总请求 12（91.6%）',
    );

    await tooltips[0]!.trigger('mouseleave');
    await tooltips[1]!.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain(
      '命中 6.00k / 未命中 3.00k（66.7%）',
    );
    wrapper.unmount();
  });

  it('切换预设范围、流量和自定义范围并校验输入', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    await wrapper
      .findAll('button')
      .find((button) => button.text() === '今日')!
      .trigger('click');
    expect(state.rangePreset).toBe('today');
    expect(state.queryParams.start_at).toBeLessThanOrEqual(state.queryParams.end_at);
    state.selectRange('30d');
    expect(state.queryParams.end_at - state.queryParams.start_at).toBe(30 * 86_400);
    state.selectRange('90d');
    expect(state.queryParams.end_at - state.queryParams.start_at).toBe(90 * 86_400);
    state.selectRange('all');
    expect(state.queryParams.start_at).toBe(0);
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '自定义')!
      .trigger('click');
    expect(state.rangePreset).toBe('custom');
    await nextTick();
    expect(wrapper.text()).toContain('应用范围');

    const dateInputs = wrapper.findAll('input[type="datetime-local"]');
    await dateInputs[0]!.setValue('');
    await dateInputs[1]!.setValue('');
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '应用范围')!
      .trigger('click');
    expect(state.customRangeError).toContain('有效');
    await nextTick();
    expect(wrapper.text()).toContain('请输入有效');
    state.customStart = '2026-01-02T00:00';
    state.customEnd = '2026-01-01T00:00';
    state.applyCustomRange();
    expect(state.customRangeError).toContain('晚于');
    state.customEnd = '2026-01-03T00:00';
    state.applyCustomRange();
    expect(state.customRangeError).toBe('');
    expect(state.queryParams.start_at).toBeLessThan(state.queryParams.end_at);

    state.setTraffic('external');
    expect(state.filters.traffic).toBe('external');
    state.setTraffic('admin');
    expect(state.filters.traffic).toBe('admin');
    await wrapper.get('[data-traffic="external"]').trigger('click');
    expect(state.filters.traffic).toBe('external');
  });

  it('刷新期间保留统计内容并显示遮罩和 spinner', async () => {
    queries[0].data.value = overview;
    queries[1].data.value = { items: [firstRequest], next_cursor: null };
    const wrapper = mountView();

    expect(wrapper.text()).toContain('9.00k');
    expect(wrapper.find('.stats-loading-overlay').exists()).toBe(false);

    queries[0].isFetching.value = true;
    await nextTick();
    const overlay = wrapper.get('.stats-loading-overlay');
    expect(overlay.attributes('role')).toBe('status');
    expect(overlay.attributes('aria-label')).toBe('正在刷新统计数据');
    expect(overlay.find('[aria-hidden="true"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('9.00k');

    queries[0].isFetching.value = false;
    await nextTick();
    expect(wrapper.find('.stats-loading-overlay').exists()).toBe(false);
  });

  it('流量筛选复用通用自然宽度分段组件', async () => {
    const wrapper = mountView();

    expect(wrapper.find('.traffic-tab-indicator').exists()).toBe(false);
    expect(wrapper.get('.radio-group').attributes('aria-label')).toBe('流量');
    expect(wrapper.findAll('.radio-group > button').map((button) => button.text())).toEqual([
      '全部',
      '外部 API',
      '管理台请求',
    ]);
    await wrapper.get('.radio-group').trigger('click');
    expect((wrapper.vm.$ as any).setupState.filters.traffic).toBe('external');
  });

  it('刷新概览与明细并处理整体错误和空状态', async () => {
    queries[0].isError.value = true;
    queries[1].isError.value = true;
    queries[0].error.value = new Error('overview failed');
    const wrapper = mountView();

    expect(wrapper.text()).toContain('加载统计失败');
    expect(wrapper.text()).toContain('overview failed');
    await wrapper.get('button.refresh').trigger('click');
    expect(queries[0].refetch).toHaveBeenCalledOnce();
    expect(queries[1].refetch).toHaveBeenCalledOnce();

    queries[0].isError.value = false;
    queries[1].isError.value = false;
    queries[0].data.value = {
      ...overview,
      series: [],
      data_quality: {
        usage_coverage: 1,
        dropped_events: 0,
        detail_retention_days: 90,
        boundary_precision: 'exact',
      },
    };
    queries[1].data.value = { items: [], next_cursor: null };
    await nextTick();
    expect(wrapper.text()).toContain('暂无请求明细');
    expect(wrapper.text()).not.toContain('统计事件写入失败');
    expect(wrapper.text()).not.toContain('历史边界按 UTC 小时近似');

    queries[0].error.value = undefined;
    queries[1].error.value = new Error('requests failed');
    queries[1].isError.value = true;
    await nextTick();
    expect(wrapper.text()).toContain('requests failed');
    const state = (wrapper.vm.$ as any).setupState;
    queries[0].refetch.mockResolvedValueOnce({ isError: false });
    queries[1].refetch.mockResolvedValueOnce({ isError: true });
    await expect(state.refreshAll()).resolves.toEqual({ isError: true });
  });

  it('显示明细加载态、空身份与全部结果标签', async () => {
    queries[1].isLoading.value = true;
    const wrapper = mountView();
    expect(wrapper.text()).toContain('正在加载请求明细');

    queries[1].isLoading.value = false;
    queries[1].data.value = {
      items: [
        { ...firstRequest, id: 11, api_key_name: null, credential_label: null, outcome: 'failure' },
        { ...firstRequest, id: 12, outcome: 'cancelled' },
      ],
      next_cursor: null,
    };
    await nextTick();
    expect(wrapper.text()).toContain('失败');
    expect(wrapper.text()).toContain('客户端中断');
    expect(wrapper.findAll('tbody tr')[0]!.text()).toContain('-');
  });

  it('请求明细表头显示积分消耗', () => {
    const wrapper = mountView();

    expect(wrapper.findAll('th').map((header) => header.text())).toContain('积分消耗');
  });

  it('请求明细 Token 列可悬浮查看输入输出与缓存命中率', async () => {
    vi.useFakeTimers();
    queries[1].data.value = {
      items: [
        {
          ...firstRequest,
          input_tokens: 100,
          output_tokens: 25,
          total_tokens: 125,
          cache_hit_tokens: 75,
          cache_miss_tokens: 25,
        },
        { ...firstRequest, id: 11 },
      ],
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 1,
      snapshot_id: 10,
      snapshot_time: 20,
    };
    const wrapper = mountView();

    const tokenTooltip = wrapper.get('.request-token-tooltip');
    expect(tokenTooltip.text()).toBe('125');

    await tokenTooltip.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain(
      '输入：100（75%缓存命中）',
    );
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain('输出：25');

    await tokenTooltip.trigger('mouseleave');
    const unknownCacheTooltip = wrapper.findAll('.request-token-tooltip')[1]!;
    await unknownCacheTooltip.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain(
      '输入：10（缓存命中率未知）',
    );
    wrapper.unmount();
  });

  it('支持快照内前后翻页、首页、末页和跳页', async () => {
    queries[1].data.value = {
      items: [firstRequest],
      page: 1,
      page_size: 20,
      total: 95,
      total_pages: 5,
      snapshot_id: 100,
      snapshot_time: 1_767_225_700,
    };
    statsRequestsMock.mockResolvedValueOnce({
      items: [{ ...firstRequest, id: 9, requested_model: 'deepseek' }],
      page: 2,
      page_size: 20,
      total: 95,
      total_pages: 5,
      snapshot_id: 100,
      snapshot_time: 1_767_225_700,
    });
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const paginationButton = (label: string) => wrapper.get(`button[aria-label="${label}"]`);

    await paginationButton('下一页').trigger('click');
    expect(statsRequestsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 2,
        page_size: 20,
        snapshot_id: 100,
        snapshot_time: 1_767_225_700,
      }),
    );
    expect(wrapper.text()).toContain('deepseek');
    expect(state.currentRequestPage).toBe(2);
    expect(wrapper.find('button[aria-current="page"]').text()).toBe('2');
    expect(wrapper.findAll('.request-pagination-nav')).toHaveLength(4);
    expect(wrapper.findAll('.request-pagination-nav').every((button) => button.text() === '')).toBe(
      true,
    );

    await paginationButton('上一页').trigger('click');
    expect(statsRequestsMock).toHaveBeenCalledTimes(1);
    expect(state.currentRequestPage).toBe(1);
    expect(wrapper.text()).toContain('provider/glm');

    statsRequestsMock.mockResolvedValueOnce({
      items: [{ ...firstRequest, id: 3, requested_model: 'numbered-page' }],
      page: 3,
      page_size: 20,
      total: 95,
      total_pages: 5,
      snapshot_id: 100,
      snapshot_time: 1_767_225_700,
    });
    await wrapper.get('button[aria-label="第 3 页"]').trigger('click');
    expect(state.currentRequestPage).toBe(3);
    expect(wrapper.text()).toContain('numbered-page');
    await paginationButton('首页').trigger('click');

    statsRequestsMock.mockResolvedValueOnce({
      items: [{ ...firstRequest, id: 1, requested_model: 'last-page' }],
      page: 5,
      page_size: 20,
      total: 95,
      total_pages: 5,
      snapshot_id: 100,
      snapshot_time: 1_767_225_700,
    });
    await paginationButton('末页').trigger('click');
    expect(state.currentRequestPage).toBe(5);
    expect(wrapper.text()).toContain('last-page');
    expect(wrapper.text()).toContain('共 95 条');

    await paginationButton('首页').trigger('click');
    statsRequestsMock.mockResolvedValueOnce({
      items: [{ ...firstRequest, id: 2, requested_model: 'jump-page' }],
      page: 5,
      page_size: 20,
      total: 95,
      total_pages: 5,
      snapshot_id: 100,
      snapshot_time: 1_767_225_700,
    });
    await wrapper.get('.c-input-number-input').setValue(5);
    await paginationButton('跳转').trigger('click');
    expect(wrapper.text()).toContain('jump-page');

    await state.goToRequestPage(6);
    state.requestJumpPage = null;
    await state.jumpToRequestPage();
    expect(statsRequestsMock).toHaveBeenCalledTimes(4);

    queries[1].data.value = {
      ...queries[1].data.value,
      total: 1980,
      total_pages: 99,
    };
    await nextTick();
    const ellipsisButton = wrapper.get('button[aria-label="跳转到第 4 页"]');
    expect(ellipsisButton.text()).toBe('…');
    statsRequestsMock.mockResolvedValueOnce({
      ...queries[1].data.value,
      items: [{ ...firstRequest, id: 4, requested_model: 'ellipsis-page' }],
      page: 4,
    });
    await ellipsisButton.trigger('click');
    expect(statsRequestsMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 4 }));
    expect(wrapper.text()).toContain('ellipsis-page');
  });

  it('分页失败保留当前页，切换分页大小后重置快照和页码', async () => {
    queries[1].data.value = {
      items: [firstRequest],
      page: 1,
      page_size: 20,
      total: 21,
      total_pages: 2,
      snapshot_id: 10,
      snapshot_time: 20,
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    statsRequestsMock.mockRejectedValueOnce(new Error('page failed'));

    await state.goToRequestPage(2);
    expect(state.requestPageError).toBe('page failed');
    expect(state.currentRequestPage).toBe(1);

    statsRequestsMock.mockResolvedValueOnce({
      ...queries[1].data.value,
      items: [],
      page: 2,
      snapshot_id: 11,
    });
    await state.goToRequestPage(2);
    expect(state.requestPageError).toBe('请求分页快照不匹配');
    statsRequestsMock.mockResolvedValueOnce({
      ...queries[1].data.value,
      items: [],
      page: 2,
      snapshot_time: 21,
    });
    await state.goToRequestPage(2);
    expect(state.requestPageError).toBe('请求分页快照不匹配');

    state.changeRequestPageSize('50');
    expect(state.requestPageSize).toBe(50);
    expect(state.currentRequestPage).toBe(1);
    expect(state.requestSnapshot).toBeNull();
    expect(state.requestPageError).toBe('');
    expect((useQueryMock.mock.calls[1]![0] as any).queryKey.value).toContain(50);
    state.changeRequestPageSize('15');
    expect(state.requestPageSize).toBe(50);
    expect(wrapper.text()).toContain('10 条/页');
    expect(wrapper.text()).toContain('100 条/页');
  });

  it('首屏刷新立即清空旧分页，并让后续页复用范围与事件快照', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-08T00:00:00Z'));
    queries[1].data.value = {
      items: [firstRequest],
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 2,
      snapshot_id: 10,
      snapshot_time: 30,
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.requestPageData = { ...queries[1].data.value, page: 2 };

    queries[1].isFetching.value = true;
    await nextTick();
    expect(state.requestPageData).toBeNull();
    expect(state.requestSnapshot).toBeNull();

    vi.setSystemTime(new Date('2026-01-08T01:00:00Z'));
    queries[1].isFetching.value = false;
    statsRequestsMock.mockResolvedValueOnce({
      items: [{ ...firstRequest, id: 20, requested_model: 'new-first-page' }],
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 2,
      snapshot_id: 20,
      snapshot_time: 40,
    });
    const requestsOptions = useQueryMock.mock.calls[1]![0] as any;
    const refreshedPage = await requestsOptions.queryFn();
    queries[1].data.value = refreshedPage;
    await nextTick();
    expect(state.requestSnapshot).toEqual({ id: 20, time: 40 });
    expect(state.requestItems.map((item: typeof firstRequest) => item.id)).toEqual([20]);

    const firstPageParams = statsRequestsMock.mock.calls.at(-1)![0] as any;
    vi.setSystemTime(new Date('2026-01-08T03:00:00Z'));
    statsRequestsMock.mockResolvedValueOnce({ ...refreshedPage, items: [], page: 2 });
    await state.goToRequestPage(2);
    const nextPageParams = statsRequestsMock.mock.calls.at(-1)![0] as any;
    expect(nextPageParams).toEqual(
      expect.objectContaining({
        start_at: firstPageParams.start_at,
        end_at: firstPageParams.end_at,
        snapshot_id: 20,
        snapshot_time: 40,
        page: 2,
      }),
    );
    expect(nextPageParams.end_at).not.toBe(Math.floor(Date.now() / 1000));
  });

  it('首屏刷新发生在翻页期间时丢弃旧分页响应', async () => {
    queries[1].data.value = {
      items: [firstRequest],
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 2,
      snapshot_id: 10,
      snapshot_time: 20,
    };
    let resolvePage!: (value: any) => void;
    statsRequestsMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    const loadPromise = state.goToRequestPage(2);
    await nextTick();
    queries[1].isFetching.value = true;
    resolvePage({ ...queries[1].data.value, items: [{ ...firstRequest, id: 9 }], page: 2 });
    await loadPromise;

    expect(state.requestPageData).toBeNull();
    expect(state.requestSnapshot).toBeNull();
  });

  it('首屏刷新后忽略旧分页请求的失败结果', async () => {
    queries[1].data.value = {
      items: [firstRequest],
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 2,
      snapshot_id: 10,
      snapshot_time: 20,
    };
    let rejectPage!: (error: Error) => void;
    statsRequestsMock.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectPage = reject;
        }),
    );
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    const loadPromise = state.goToRequestPage(2);
    await nextTick();
    queries[1].isFetching.value = true;
    rejectPage(new Error('stale page failure'));
    await loadPromise;

    expect(state.requestPageError).toBe('');
    expect(state.requestPageLoading).toBe(false);
  });

  it('打开请求详情抽屉，展示脱敏字段并处理失败和关闭', async () => {
    queries[1].data.value = { items: [firstRequest], next_cursor: null };
    statsDetailMock.mockResolvedValueOnce(firstRequest);
    const wrapper = mountView();
    const row = wrapper.get('tbody tr');

    await row.trigger('click');
    await vi.waitFor(() => expect(statsDetailMock).toHaveBeenCalledWith(10));
    expect(wrapper.text()).toContain('请求详情');
    expect(wrapper.text()).toContain('输入 Token');
    expect(wrapper.text()).toContain('积分');
    expect(wrapper.text()).toContain('首个 SSE 事件');
    expect(wrapper.text()).toContain('外部 API');

    statsDetailMock.mockResolvedValueOnce({ ...firstRequest, client_stream: false });
    await row.trigger('keyup.enter');
    await vi.waitFor(() => expect(wrapper.text()).toContain('流式否'));

    await wrapper.get('.drawer-close').trigger('click');
    expect((wrapper.vm.$ as any).setupState.detailOpen).toBe(false);

    const state = (wrapper.vm.$ as any).setupState;
    state.closeDetail();
    expect(state.detailOpen).toBe(false);
    statsDetailMock.mockRejectedValueOnce('broken');
    await state.openDetail(firstRequest);
    expect(state.detailError).toBe('加载请求详情失败');
  });

  it('详情中的可空流式、计数和大小字段统一显示占位符', async () => {
    queries[1].data.value = { items: [firstRequest], next_cursor: null };
    statsDetailMock.mockResolvedValueOnce({
      ...firstRequest,
      result_status: null,
      client_stream: null,
      message_count: null,
      tool_count: null,
      request_bytes: null,
      response_bytes: null,
      retry_count: null,
      tool_call_count: null,
    });
    const wrapper = mountView();

    await wrapper.get('tbody tr').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('请求详情'));
    expect(wrapper.text()).toContain('流式-');
    expect(wrapper.text()).toContain('逻辑状态-');
    expect(wrapper.text()).toContain('消息数-');
    expect(wrapper.text()).toContain('请求大小-');
    expect(wrapper.text()).not.toContain('null B');
  });

  it('较早的详情响应不能覆盖当前抽屉或提前结束加载态', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    let resolveFirst!: (value: any) => void;
    let resolveSecond!: (value: any) => void;
    statsDetailMock
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)));

    const first = state.openDetail(firstRequest);
    const second = state.openDetail({ ...firstRequest, id: 11 });
    resolveFirst({ ...firstRequest, id: 10 });
    await first;
    expect(state.detailLoading).toBe(true);
    expect(state.detail).toBeNull();
    resolveSecond({ ...firstRequest, id: 11 });
    await second;

    expect(state.detail.id).toBe(11);
    expect(state.detailLoading).toBe(false);
  });

  it('完整维度抽屉支持搜索分页并可从筛选模式选择项目', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.filters.model = 'current-model';
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('other-model', 'Other')],
      next_cursor: 'next',
    });

    await state.openDimensionExplorer('models', 'select');

    expect(statsDimensionsMock).toHaveBeenCalledWith(
      'models',
      expect.not.objectContaining({ model: 'current-model' }),
    );
    expect(state.dimensionItems[0].id).toBe('other-model');
    expect(state.dimensionNextCursor).toBe('next');
    await nextTick();
    expect(wrapper.text()).toContain('other-model');
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '选择')!
      .trigger('click');
    expect(state.filters.model).toBe('other-model');
    expect(state.dimensionOpen).toBe(false);
  });

  it('筛选候选始终可清除、保留失配值，并可从所有入口打开完整列表', async () => {
    queries[0].data.value = overview;
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    state.filters.model = 'missing-model';
    state.filters.apiKeyId = 'missing-key';
    state.filters.credentialId = 'missing-credential';
    state.filters.outcome = 'future';
    await nextTick();
    expect(state.modelOptions.at(-1)).toEqual({
      value: 'missing-model',
      label: 'missing-model',
    });
    expect(state.apiKeyOptions.at(-1)).toEqual({ value: 'missing-key', label: 'missing-key' });
    expect(state.credentialOptions.at(-1)).toEqual({
      value: 'missing-credential',
      label: 'missing-credential',
    });
    expect(state.outcomeOptions.at(-1)).toEqual({ value: 'future', label: 'future' });
    state.filters.model = 'glm';
    await nextTick();
    expect(state.modelOptions.filter((option: any) => option.value === 'glm')).toHaveLength(1);

    state.setDetailOpen(true);
    expect(state.detailOpen).toBe(true);
    state.setDetailOpen(false);
    expect(state.detailOpen).toBe(false);
    state.setDimensionOpen(true);
    expect(state.dimensionOpen).toBe(true);
    state.setDimensionOpen(false);
    expect(state.dimensionOpen).toBe(false);

    const detailButtons = wrapper.findAll('.c-select-footer-action');
    expect(detailButtons.map((button) => button.text())).toEqual([
      '从详细列表选择…',
      '从详细列表选择…',
      '从详细列表选择…',
    ]);
    const rankingButtons = wrapper
      .findAll('button')
      .filter((button) => button.text() === '查看全部');
    expect(rankingButtons).toHaveLength(3);
    const buttons = [...detailButtons, ...rankingButtons];
    for (const [index, button] of buttons.entries()) {
      await button.trigger('click');
      await vi.waitFor(() => expect(statsDimensionsMock).toHaveBeenCalledTimes(index + 1));
      state.closeDimensionExplorer();
    }
  });

  it('完整维度列表支持搜索、前后翻页、空状态和三个维度选择', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('key-1')],
      next_cursor: 'next-page',
    });
    await state.openDimensionExplorer('api_keys', 'ranking');
    await nextTick();
    expect(wrapper.text()).toContain('key-1');
    expect(wrapper.text()).not.toContain('操作');

    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('key-2', '机器人 2')],
      next_cursor: null,
    });
    await state.nextDimensionPage();
    expect(statsDimensionsMock).toHaveBeenLastCalledWith(
      'api_keys',
      expect.objectContaining({ cursor: 'next-page', limit: 50 }),
    );
    expect(state.dimensionCursorHistory).toEqual([null]);
    expect(state.dimensionItems[0].id).toBe('key-2');

    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('key-1')],
      next_cursor: 'next-page',
    });
    await state.previousDimensionPage();
    expect(state.dimensionCursorHistory).toEqual([]);
    expect(state.dimensionItems[0].id).toBe('key-1');

    await wrapper.get('input[type="search"]').setValue(' robot ');
    statsDimensionsMock.mockResolvedValueOnce({ items: [], next_cursor: null });
    await wrapper.get('form').trigger('submit');
    await vi.waitFor(() =>
      expect(statsDimensionsMock).toHaveBeenLastCalledWith(
        'api_keys',
        expect.objectContaining({ search: 'robot', limit: 50 }),
      ),
    );
    await nextTick();
    expect(wrapper.text()).toContain('暂无匹配数据');

    const callCount = statsDimensionsMock.mock.calls.length;
    await state.nextDimensionPage();
    await state.previousDimensionPage();
    state.dimensionNextCursor = 'blocked-next';
    state.dimensionCursorHistory = [null];
    state.dimensionLoading = true;
    await state.nextDimensionPage();
    await state.previousDimensionPage();
    expect(statsDimensionsMock).toHaveBeenCalledTimes(callCount);

    state.dimensionLoading = false;
    state.dimensionKind = 'api_keys';
    state.selectDimensionItem(dimensionItem('selected-key'));
    expect(state.filters.apiKeyId).toBe('selected-key');
    state.dimensionKind = 'credentials';
    state.selectDimensionItem(dimensionItem('selected-credential'));
    expect(state.filters.credentialId).toBe('selected-credential');
  });

  it('完整维度分页复用打开或搜索成功时的范围与搜索快照', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-08T00:00:00Z'));
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('model-a')],
      next_cursor: 'open-next',
    });
    await state.openDimensionExplorer('models');
    const openParams = statsDimensionsMock.mock.calls.at(-1)![1] as any;

    state.dimensionSearch = '尚未提交';
    vi.setSystemTime(new Date('2026-01-08T01:00:00Z'));
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('model-b')],
      next_cursor: null,
    });
    await state.nextDimensionPage();
    const openNextParams = statsDimensionsMock.mock.calls.at(-1)![1] as any;
    expect(openNextParams).toEqual(
      expect.objectContaining({
        start_at: openParams.start_at,
        end_at: openParams.end_at,
        cursor: 'open-next',
      }),
    );
    expect(openNextParams).not.toHaveProperty('search');

    state.dimensionSearch = ' model ';
    vi.setSystemTime(new Date('2026-01-08T02:00:00Z'));
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('model-a')],
      next_cursor: 'search-next',
    });
    await state.searchDimensions();
    const searchParams = statsDimensionsMock.mock.calls.at(-1)![1] as any;
    expect(searchParams.search).toBe('model');
    expect(searchParams.end_at).not.toBe(openParams.end_at);

    vi.setSystemTime(new Date('2026-01-08T03:00:00Z'));
    statsDimensionsMock.mockResolvedValueOnce({ items: [], next_cursor: null });
    await state.nextDimensionPage();
    expect(statsDimensionsMock).toHaveBeenLastCalledWith(
      'models',
      expect.objectContaining({
        start_at: searchParams.start_at,
        end_at: searchParams.end_at,
        search: 'model',
        cursor: 'search-next',
      }),
    );
  });

  it('完整维度翻页或搜索失败时不提前修改游标历史', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('model-a')],
      next_cursor: 'next-page',
    });
    await state.openDimensionExplorer('models');

    statsDimensionsMock.mockRejectedValueOnce(new Error('next failed'));
    await state.nextDimensionPage();
    expect(state.dimensionCursor).toBeNull();
    expect(state.dimensionCursorHistory).toEqual([]);
    expect(state.dimensionItems[0].id).toBe('model-a');

    statsDimensionsMock.mockResolvedValueOnce({
      items: [dimensionItem('model-b')],
      next_cursor: null,
    });
    await state.nextDimensionPage();
    expect(state.dimensionCursor).toBe('next-page');
    expect(state.dimensionCursorHistory).toEqual([null]);

    statsDimensionsMock.mockRejectedValueOnce(new Error('previous failed'));
    await state.previousDimensionPage();
    expect(state.dimensionCursor).toBe('next-page');
    expect(state.dimensionCursorHistory).toEqual([null]);
    expect(state.dimensionItems[0].id).toBe('model-b');

    state.dimensionSearch = 'new search';
    statsDimensionsMock.mockRejectedValueOnce(new Error('search failed'));
    await state.searchDimensions();
    expect(state.dimensionCursor).toBe('next-page');
    expect(state.dimensionCursorHistory).toEqual([null]);
  });

  it('完整维度请求失败或过期时保持当前抽屉状态', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    statsDimensionsMock.mockRejectedValueOnce(new Error('dimension failed'));
    await state.openDimensionExplorer('credentials');
    expect(state.dimensionError).toBe('dimension failed');
    expect(state.dimensionLoading).toBe(false);
    await nextTick();
    expect(wrapper.text()).toContain('dimension failed');

    let resolveStale!: (value: any) => void;
    statsDimensionsMock.mockImplementationOnce(
      () => new Promise((resolve) => (resolveStale = resolve)),
    );
    const staleSuccess = state.openDimensionExplorer('models');
    await nextTick();
    expect(wrapper.text()).toContain('正在加载');
    state.closeDimensionExplorer();
    resolveStale({ items: [dimensionItem('stale')], next_cursor: null });
    await staleSuccess;
    expect(state.dimensionItems).toEqual([]);

    let rejectStale!: (error: Error) => void;
    statsDimensionsMock.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (rejectStale = reject)),
    );
    const staleFailure = state.openDimensionExplorer('models');
    state.closeDimensionExplorer();
    rejectStale(new Error('stale error'));
    await staleFailure;
    expect(state.dimensionError).toBe('');
    expect(state.dimensionLoading).toBe(false);
  });

  it('较早的失败详情请求不会覆盖后来成功的详情', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    let rejectFirst!: (error: Error) => void;
    statsDetailMock
      .mockImplementationOnce(() => new Promise((_resolve, reject) => (rejectFirst = reject)))
      .mockResolvedValueOnce({ ...firstRequest, id: 11 });

    const first = state.openDetail(firstRequest);
    await state.openDetail({ ...firstRequest, id: 11 });
    rejectFirst(new Error('old failure'));
    await first;

    expect(state.detail.id).toBe(11);
    expect(state.detailError).toBe('');
    expect(state.detailLoading).toBe(false);
  });
});
