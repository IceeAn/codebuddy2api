import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
