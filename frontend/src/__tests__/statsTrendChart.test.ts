import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import StatsTrendChart from '../components/StatsTrendChart.vue';
import CTooltip from '../components/ui/CTooltip.vue';

const chartSource = readFileSync(
  resolve(process.cwd(), 'src/components/StatsTrendChart.vue'),
  'utf8',
);

afterEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('趋势图滚动提示', () => {
  const points = Array.from({ length: 90 }, (_, index) => ({
    period_start: 1_767_225_600 + index * 86_400,
    request_count: index + 1,
  }));
  const leftSelector = 'button[aria-label="向左滚动趋势图"]';
  const rightSelector = 'button[aria-label="向右滚动趋势图"]';
  let resize: () => void;
  const observe = vi.fn<(element: Element) => void>();
  const disconnect = vi.fn<() => void>();

  beforeEach(() => {
    observe.mockClear();
    disconnect.mockClear();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: () => void) {
          resize = callback;
        }
        observe = observe;
        disconnect = disconnect;
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function renderChart(initialPoints = points) {
    const wrapper = mount(StatsTrendChart, {
      attachTo: document.body,
      props: { points: initialPoints, metric: 'request_count', timezone: 'UTC' },
    });
    await nextTick();
    return wrapper;
  }

  function setDimensions(element: Element, width: number, contentWidth: number, left = 0) {
    Object.defineProperties(element, {
      clientWidth: { configurable: true, value: width },
      scrollWidth: { configurable: true, value: contentWidth },
      scrollLeft: { configurable: true, writable: true, value: left },
    });
  }

  it('按实际溢出和滚动边界显示方向，容忍亚像素误差与回弹', async () => {
    const wrapper = await renderChart();
    const viewport = wrapper.get('.stats-trend-scroll');
    expect(observe).toHaveBeenCalledWith(viewport.element);
    expect(observe).toHaveBeenCalledWith(wrapper.get('.stats-trend-plot').element);

    setDimensions(viewport.element, 400, 2160);
    resize();
    await nextTick();
    expect(wrapper.get(leftSelector).isVisible()).toBe(false);
    expect(wrapper.get(rightSelector).isVisible()).toBe(true);
    expect(viewport.attributes('tabindex')).toBe('0');
    expect(viewport.attributes('role')).toBe('region');
    expect(wrapper.get(rightSelector).attributes('aria-controls')).toBe(viewport.attributes('id'));

    for (const [position, left, right] of [
      [800, true, true],
      [1760, true, false],
      [1759.5, true, false],
      [1770, true, false],
      [0.5, false, true],
      [-20, false, true],
    ] as const) {
      (viewport.element as HTMLElement).scrollLeft = position;
      await viewport.trigger('scroll');
      expect(wrapper.get(leftSelector).isVisible()).toBe(left);
      expect(wrapper.get(rightSelector).isVisible()).toBe(right);
      expect(wrapper.get('.stats-trend-edge-left').element.hasAttribute('inert')).toBe(!left);
      expect(wrapper.get('.stats-trend-edge-right').element.hasAttribute('inert')).toBe(!right);
    }

    for (const position of [0, -20, 20]) {
      setDimensions(viewport.element, 2160, 2160, position);
      resize();
      await nextTick();
      expect(wrapper.get(leftSelector).isVisible()).toBe(false);
      expect(wrapper.get(rightSelector).isVisible()).toBe(false);
      expect(viewport.attributes('tabindex')).toBe('-1');
    }
    wrapper.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('左右按钮各滚动八成可视宽度，缩放后使用当前宽度', async () => {
    const wrapper = await renderChart();
    const viewport = wrapper.get('.stats-trend-scroll');
    const scrollBy = vi.fn<(options: ScrollToOptions) => void>();
    Object.defineProperty(viewport.element, 'scrollBy', { value: scrollBy });
    setDimensions(viewport.element, 400, 2160, 800);
    resize();
    await nextTick();
    await wrapper.get(rightSelector).trigger('click');
    expect(scrollBy).toHaveBeenLastCalledWith({ left: 320 });
    await wrapper.get(leftSelector).trigger('click');
    expect(scrollBy).toHaveBeenLastCalledWith({ left: -320 });

    setDimensions(viewport.element, 300, 2160, 800);
    resize();
    await nextTick();
    await wrapper.get(rightSelector).trigger('click');
    expect(scrollBy).toHaveBeenLastCalledWith({ left: 240 });
    wrapper.unmount();
  });

  it('聚焦的箭头到达边界后将焦点交回图表，不抢占其他节点焦点', async () => {
    const wrapper = await renderChart();
    const viewport = wrapper.get('.stats-trend-scroll');
    setDimensions(viewport.element, 400, 2160, 800);
    resize();
    await nextTick();

    (wrapper.get(rightSelector).element as HTMLButtonElement).focus();
    (viewport.element as HTMLElement).scrollLeft = 1760;
    await viewport.trigger('scroll');
    expect(document.activeElement).toBe(viewport.element);

    (wrapper.get(leftSelector).element as HTMLButtonElement).focus();
    (viewport.element as HTMLElement).scrollLeft = 0;
    await viewport.trigger('scroll');
    expect(document.activeElement).toBe(viewport.element);

    const point = wrapper.findAll('.stats-trend-point-trigger')[0]!.element as HTMLButtonElement;
    point.focus();
    await viewport.trigger('scroll');
    expect(document.activeElement).toBe(point);
    wrapper.unmount();
  });

  it('数据变化、空状态和恢复显示时重算提示并清理观察器', async () => {
    const wrapper = await renderChart([]);
    expect(observe).not.toHaveBeenCalled();
    await wrapper.setProps({ points });
    const viewport = wrapper.get('.stats-trend-scroll');
    setDimensions(viewport.element, 400, 2160);
    resize();
    await nextTick();
    expect(wrapper.get(rightSelector).isVisible()).toBe(true);

    await wrapper.setProps({ points: points.slice(0, 2) });
    setDimensions(viewport.element, 400, 400);
    resize();
    await nextTick();
    expect(wrapper.get(rightSelector).isVisible()).toBe(false);

    await wrapper.setProps({ metric: 'total_tokens' });
    expect(wrapper.text()).toContain('该指标暂无已知数据');
    expect(disconnect).toHaveBeenCalledOnce();
    await wrapper.setProps({ metric: 'request_count' });
    expect(observe).toHaveBeenCalledTimes(4);
    wrapper.unmount();
    expect(disconnect).toHaveBeenCalledTimes(2);
  });
});

describe('StatsTrendChart', () => {
  it('无数据时显示空状态', () => {
    const wrapper = mount(StatsTrendChart, {
      props: { points: [], metric: 'request_count', timezone: 'Asia/Taipei' },
    });

    expect(wrapper.text()).toContain('当前筛选范围内暂无趋势数据');
    expect(wrapper.find('svg').exists()).toBe(false);
    expect((wrapper.vm.$ as any).setupState.firstPoint).toBeUndefined();
    expect((wrapper.vm.$ as any).setupState.lastPoint).toBeUndefined();
  });

  it('绘制响应式 SVG 并格式化首尾刻度与数据点', () => {
    const wrapper = mount(StatsTrendChart, {
      props: {
        points: [
          { period_start: 1_767_225_600, request_count: 2 },
          { period_start: 1_767_312_000, request_count: 8 },
        ],
        metric: 'request_count',
        timezone: 'UTC',
      },
    });

    expect(wrapper.get('svg').attributes('viewBox')).toBe('0 0 800 240');
    expect(wrapper.get('.stats-trend-line').attributes('d')).toContain('L');
    expect(wrapper.findAll('.stats-trend-point-trigger')).toHaveLength(2);
    expect(wrapper.text()).toContain('最大值');
    expect(wrapper.findAll('.stats-trend-axis time')).toHaveLength(2);
  });

  it('数据点使用即时 Tooltip，并支持点击或触摸切换详情', async () => {
    vi.useFakeTimers();
    const wrapper = mount(StatsTrendChart, {
      attachTo: document.body,
      props: {
        points: [
          {
            period_start: 1_767_225_600,
            period: '2026-01-01T00:00:00+00:00',
            request_count: 8,
          },
        ],
        metric: 'request_count',
        timezone: 'UTC',
      },
    });
    const tooltip = wrapper.getComponent(CTooltip);

    expect(tooltip.props('delay')).toBe(300);
    expect(tooltip.props('clickable')).toBe(true);
    expect(wrapper.find('title').exists()).toBe(false);
    await tooltip.trigger('mouseenter');
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')).toBeNull();
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain('8');
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain(
      '2026/01/01 00:00',
    );
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).not.toContain(
      '00:00:00',
    );

    const pointButton = wrapper.get('.stats-trend-point-trigger');
    (pointButton.element as HTMLButtonElement).focus();
    await pointButton.trigger('click');
    await tooltip.trigger('mouseleave');
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain('8');
    await pointButton.trigger('click');
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')).toBeNull();
    expect(document.activeElement).not.toBe(pointButton.element);

    await pointButton.trigger('click');
    await tooltip.trigger('mouseleave');
    await flushPromises();
    expect(document.body.querySelector('.c-tooltip-popover')?.textContent).toContain('8');

    expect(pointButton.element.tagName).toBe('BUTTON');
    expect(pointButton.classes()).toContain('rounded-full');
    expect(pointButton.classes()).not.toContain('c-control-focus');
    expect(wrapper.find('g[tabindex]').exists()).toBe(false);
  });

  it('数据点与焦点环使用相同的固定像素直径，并保留柔和发光', () => {
    expect(chartSource).toMatch(/\.stats-trend-point-trigger::before\s*\{/);
    expect(chartSource).toMatch(/width:\s*8px/);
    expect(chartSource).toMatch(/\.stats-trend-point-trigger::after\s*\{/);
    expect(chartSource).not.toMatch(/width:\s*14px/);
    expect(chartSource.match(/width:\s*8px/g)).toHaveLength(2);
    expect(chartSource.match(/height:\s*8px/g)).toHaveLength(2);
    expect(chartSource).toMatch(/\.stats-trend-point-trigger:focus::after\s*\{/);
    expect(chartSource).toMatch(/box-shadow:/);
  });

  it('数据点使用自定义焦点环时不叠加全局焦点样式', () => {
    expect(chartSource).toMatch(
      /\.stats-trend-point-trigger:focus-visible\s*\{[^}]*outline:\s*none[^}]*box-shadow:\s*none/s,
    );
  });

  it('强制颜色模式下保留可见的键盘焦点轮廓', () => {
    expect(chartSource).toMatch(
      /@media\s*\(forced-colors:\s*active\)\s*\{[^}]*\.stats-trend-point-trigger:focus-visible\s*\{[^}]*outline:\s*2px solid Highlight[^}]*outline-offset:\s*2px/s,
    );
  });

  it('按数据点数量设置最小宽度并允许横向滚动', () => {
    const points = Array.from({ length: 20 }, (_, index) => ({
      period_start: 1_767_225_600 + index * 86_400,
      period: `2026-01-${String(index + 1).padStart(2, '0')}`,
      request_count: index + 1,
    }));
    const wrapper = mount(StatsTrendChart, {
      props: { points, metric: 'request_count', timezone: 'UTC' },
    });

    expect(wrapper.get('.stats-trend-scroll').classes()).toContain('overflow-x-auto');
    expect(wrapper.get('.stats-trend-plot').attributes('style')).toContain('min-width: 480px');
    expect(wrapper.get('.stats-trend-canvas').classes()).toContain('stats-trend-fixed-gutter');
    expect(wrapper.get('svg').classes()).toContain('block');
    expect(wrapper.get('svg').classes()).toContain('h-60');
  });

  it('长时间范围使用固定左右留白，且数据点锚点与折线坐标严格重合', () => {
    const points = Array.from({ length: 90 }, (_, index) => ({
      period_start: 1_767_225_600 + index * 86_400,
      period: `day-${index}`,
      request_count: 1,
    }));
    const wrapper = mount(StatsTrendChart, {
      props: { points, metric: 'request_count', timezone: 'UTC' },
    });
    const anchors = wrapper.findAll('.stats-trend-point-anchor');

    expect(wrapper.get('.stats-trend-plot').attributes('style')).toContain('min-width: 2160px');
    expect(anchors[0]!.attributes('style')).toContain('left: 0%');
    expect(anchors.at(-1)!.attributes('style')).toContain('left: 100%');
    expect(anchors[0]!.classes()).toEqual(
      expect.arrayContaining(['flex', 'h-6', 'w-6', '-translate-x-1/2', '-translate-y-1/2']),
    );
    expect(chartSource).toMatch(/\.stats-trend-fixed-gutter\s*\{[^}]*margin-inline:\s*12px/s);
  });

  it('日粒度 Tooltip 和坐标轴只显示日期', async () => {
    const wrapper = mount(StatsTrendChart, {
      attachTo: document.body,
      props: {
        points: [
          { period_start: 1_767_225_600, period: '2026-01-01', request_count: 8 },
          { period_start: 1_767_312_000, period: '2026-01-02', request_count: 4 },
        ],
        metric: 'request_count',
        timezone: 'UTC',
      },
    });

    await wrapper.findAll('.stats-trend-point-trigger')[0]!.trigger('click');
    await flushPromises();
    const tooltipText = document.body.querySelector('.c-tooltip-popover')?.textContent ?? '';
    expect(tooltipText).toContain('2026/01/01');
    expect(tooltipText).not.toContain('00:00');
    expect(wrapper.findAll('.stats-trend-axis time')[0]!.text()).toBe('2026/01/01');
  });

  it('全部指标未知时显示专用空状态', () => {
    const wrapper = mount(StatsTrendChart, {
      props: {
        points: [
          { period_start: 1_767_225_600, total_tokens: null },
          { period_start: 1_767_312_000 },
        ],
        metric: 'total_tokens',
        timezone: 'UTC',
      },
    });

    expect(wrapper.text()).toContain('该指标暂无已知数据');
    expect(wrapper.find('svg').exists()).toBe(false);
  });

  it('跳过未知点、保留原横坐标并在缺口处断线', () => {
    const wrapper = mount(StatsTrendChart, {
      props: {
        points: [
          { period_start: 1_767_225_600, total_tokens: 2 },
          { period_start: 1_767_312_000, total_tokens: null },
          { period_start: 1_767_398_400, total_tokens: 8 },
          { period_start: 1_767_484_800, total_tokens: 4 },
        ],
        metric: 'total_tokens',
        timezone: 'UTC',
      },
    });

    const path = wrapper.get('.stats-trend-line').attributes('d') ?? '';
    expect(path.match(/M /g)).toHaveLength(2);
    expect(path.match(/L /g)).toHaveLength(1);
    expect(wrapper.findAll('.stats-trend-point-trigger')).toHaveLength(3);
    expect(wrapper.findAll('.stats-trend-point-anchor')[1]!.attributes('style')).not.toContain(
      'left: 50%',
    );
  });
});
