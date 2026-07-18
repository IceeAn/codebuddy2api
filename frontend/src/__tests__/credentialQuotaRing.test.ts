import { defineComponent } from 'vue';
import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import CredentialQuotaRing, {
  formatQuotaTooltip,
  quotaTone,
} from '../components/CredentialQuotaRing.vue';
import type { CredentialQuota } from '../types';

const TooltipStub = defineComponent({
  name: 'CTooltip',
  props: { content: String },
  template:
    '<span class="tooltip-stub" :data-content="content"><slot /><slot name="content" /></span>',
});

function quota(overrides: Partial<CredentialQuota> = {}): CredentialQuota {
  return {
    status: 'fresh',
    total: 100,
    remaining: 65,
    remaining_percent: 65,
    estimated: false,
    estimated_credit_since_sync: 0,
    last_attempt_at: 1_720_000_000,
    last_success_at: 1_720_000_000,
    last_estimated_at: null,
    error_type: null,
    packages: [],
    ...overrides,
  };
}

function mountRing(value: CredentialQuota) {
  return mount(CredentialQuotaRing, {
    props: { quota: value },
    global: { stubs: { CTooltip: TooltipStub } },
  });
}

describe('CredentialQuotaRing', () => {
  it('在10%和30%边界按剩余额度选择颜色', () => {
    expect(quotaTone(quota({ remaining_percent: 0 }))).toBe('danger');
    expect(quotaTone(quota({ remaining_percent: 10 }))).toBe('danger');
    expect(quotaTone(quota({ remaining_percent: 11 }))).toBe('warning');
    expect(quotaTone(quota({ remaining_percent: 30 }))).toBe('warning');
    expect(quotaTone(quota({ remaining_percent: 31 }))).toBe('success');
    expect(quotaTone(quota({ remaining_percent: 100 }))).toBe('success');
    expect(quotaTone(quota({ status: 'unknown', remaining_percent: null }))).toBe('muted');
    expect(quotaTone(quota({ status: 'error' }))).toBe('muted');
  });

  it('渲染无内部数字的紧凑可访问圆环和估算状态', () => {
    const wrapper = mountRing(
      quota({
        remaining: 62.5,
        remaining_percent: 63,
        estimated: true,
        estimated_credit_since_sync: 2.5,
        last_estimated_at: 1_720_000_100,
      }),
    );

    const ring = wrapper.get('[role="img"]');
    expect(ring.text()).toBe('');
    expect(ring.classes()).toContain('size-4');
    expect(ring.element.tagName.toLowerCase()).toBe('svg');
    expect(ring.attributes('style')).not.toContain('conic-gradient');
    expect(ring.get('.credential-quota-ring-value').attributes('stroke-dasharray')).toBe('63 100');
    expect(wrapper.get('.tooltip-stub').classes()).toContain('align-middle');
    expect(wrapper.get('.tooltip-stub').classes()).toContain('leading-none');
    expect(ring.attributes('aria-label')).toContain('估算剩余额度 62.5 / 100，63%');
    expect(wrapper.get('.tooltip-stub').attributes('data-content')).toContain(
      '当前估算：62.5 / 100',
    );
    const tooltipContent = wrapper.get('.credential-quota-tooltip-content');
    expect(tooltipContent.classes()).toContain('space-y-1');
    expect(tooltipContent.get('.credential-quota-tooltip-section').classes()).toContain(
      'whitespace-pre-line',
    );
    expect(tooltipContent.text()).toContain('当前估算：62.5 / 100\n最近校准：');
  });

  it('未知和错误状态显示灰色占位，陈旧状态保留旧值并提示失败', () => {
    const unknown = mountRing(
      quota({ status: 'unknown', total: null, remaining: null, remaining_percent: null }),
    );
    expect(unknown.get('[role="img"]').text()).toBe('');
    expect(unknown.get('[role="img"]').attributes('aria-label')).toBe('额度尚未探测');

    const error = mountRing(
      quota({
        status: 'error',
        total: null,
        remaining: null,
        remaining_percent: null,
        last_attempt_at: null,
      }),
    );
    expect(error.get('.tooltip-stub').attributes('data-content')).toContain('额度查询失败');
    expect(error.get('.tooltip-stub').attributes('data-content')).toContain('最近尝试：--');
    expect(error.get('.tooltip-stub').attributes('data-content')).toBe(
      '额度查询失败\n最近尝试：--',
    );

    expect(formatQuotaTooltip(quota({ remaining: null }))).toContain('额度查询失败');
    expect(formatQuotaTooltip(quota({ total: null }))).toContain('额度查询失败');

    const stale = mountRing(quota({ status: 'stale', error_type: 'transport_error' }));
    expect(stale.get('[role="img"]').classes()).toContain('credential-quota-ring-stale');
    expect(stale.get('.tooltip-stub').attributes('data-content')).toContain(
      '最近刷新失败，当前值已陈旧',
    );
  });

  it('tooltip显示校准时间和所有套餐的真实值及周期', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-18T00:00:00Z'));
    const value = quota({
      last_success_at: null,
      packages: [
        {
          name: '月度套餐',
          total: 100,
          remaining: 65,
          used: 35,
          cycle_start: '2026-07-01 00:00:00',
          cycle_end: '2026-07-31 23:59:59',
        },
        {
          name: '加量包',
          total: 20,
          remaining: 10,
          used: 10,
          cycle_start: null,
          cycle_end: null,
        },
        {
          name: '仅开始时间',
          total: 10,
          remaining: 9,
          used: 1,
          cycle_start: '2026-07-10 00:00:00',
          cycle_end: null,
        },
        {
          name: '仅结束时间',
          total: 10,
          remaining: 8,
          used: 2,
          cycle_start: null,
          cycle_end: '2026-08-10 00:00:00',
        },
      ],
    });
    const tooltip = formatQuotaTooltip(value);
    expect(tooltip).toContain('最近校准：');
    const sections = tooltip.split('\n\n');
    expect(sections).toHaveLength(5);
    expect(sections[1]).toBe('月度套餐：65 / 100\n2026-07-01 00:00:00 至 2026-07-31 23:59:59');
    expect(tooltip).toContain('加量包：10 / 20');
    expect(sections[3]).toBe('仅开始时间：9 / 10\n周期始于 2026-07-10 00:00:00');
    expect(sections[4]).toBe('仅结束时间：8 / 10\n周期截至 2026-08-10 00:00:00');
    expect(tooltip).toContain('最近校准：--');

    const wrapper = mountRing(value);
    const renderedSections = wrapper.findAll('.credential-quota-tooltip-section');
    expect(renderedSections).toHaveLength(5);
    expect(renderedSections[1]!.text()).toBe(sections[1]);
    vi.useRealTimers();
  });
});
