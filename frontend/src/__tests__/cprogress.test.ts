import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CProgress from '../components/ui/CProgress.vue';

describe('CProgress', () => {
  it('默认渲染 SVG（percentage=0）', () => {
    const wrapper = mount(CProgress);
    expect(wrapper.find('svg').exists()).toBe(true);
    expect(wrapper.findAll('circle')).toHaveLength(2);
    expect(wrapper.get('svg').attributes('aria-label')).toBe('进度');
  });

  it('默认 size=80 strokeWidth=8', () => {
    const wrapper = mount(CProgress);
    const svg = wrapper.find('svg');
    expect(svg.attributes('width')).toBe('80');
    expect(svg.attributes('height')).toBe('80');
    const circles = wrapper.findAll('circle');
    expect(circles[1].attributes('stroke-width')).toBe('8');
  });

  it('size prop 控制 SVG 尺寸', () => {
    const wrapper = mount(CProgress, { props: { size: 120 } });
    const svg = wrapper.find('svg');
    expect(svg.attributes('width')).toBe('120');
    expect(svg.attributes('height')).toBe('120');
  });

  it('strokeWidth prop 控制描边宽度', () => {
    const wrapper = mount(CProgress, { props: { strokeWidth: 10 } });
    const circles = wrapper.findAll('circle');
    expect(circles[1].attributes('stroke-width')).toBe('10');
  });

  it('percentage=0 时 dashoffset=circumference', () => {
    const wrapper = mount(CProgress, { props: { percentage: 0, size: 80, strokeWidth: 8 } });
    // r = (size - strokeWidth) / 2 = 36, circumference = 2 * π * 36 ≈ 226.19
    const progressCircle = wrapper.findAll('circle')[1];
    const dashoffset = parseFloat(progressCircle.attributes('stroke-dashoffset') || '0');
    expect(dashoffset).toBeCloseTo(2 * Math.PI * 36, 1);
  });

  it('percentage=50 时 dashoffset=circumference*0.5', () => {
    const wrapper = mount(CProgress, { props: { percentage: 50, size: 80, strokeWidth: 8 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const dashoffset = parseFloat(progressCircle.attributes('stroke-dashoffset') || '0');
    const circumference = 2 * Math.PI * 36;
    expect(dashoffset).toBeCloseTo(circumference * 0.5, 1);
  });

  it('percentage=100 时 dashoffset=0', () => {
    const wrapper = mount(CProgress, { props: { percentage: 100, size: 80, strokeWidth: 8 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const dashoffset = parseFloat(progressCircle.attributes('stroke-dashoffset') || '0');
    expect(dashoffset).toBeCloseTo(0, 1);
  });

  it('dasharray=circumference', () => {
    const wrapper = mount(CProgress, { props: { percentage: 50, size: 80, strokeWidth: 8 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const circumference = 2 * Math.PI * 36;
    const dasharray = parseFloat(progressCircle.attributes('stroke-dasharray') || '0');
    expect(dasharray).toBeCloseTo(circumference, 1);
  });

  it('thresholdColors=true（默认）≥80% 用 brand-500', () => {
    const wrapper = mount(CProgress, { props: { percentage: 80, thresholdColors: true } });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toBe('var(--color-brand-500)');
  });

  it('thresholdColors=true 90% 用 brand-500', () => {
    const wrapper = mount(CProgress, { props: { percentage: 90 } });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toBe('var(--color-brand-500)');
  });

  it('thresholdColors=true 50-79% 用 warning-500', () => {
    const wrapper = mount(CProgress, { props: { percentage: 60 } });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toBe('var(--color-warning-500)');
  });

  it('thresholdColors=true 50% 用 warning-500', () => {
    const wrapper = mount(CProgress, { props: { percentage: 50 } });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toBe('var(--color-warning-500)');
  });

  it('thresholdColors=true <50% 用 error-500', () => {
    const wrapper = mount(CProgress, { props: { percentage: 49 } });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toBe('var(--color-error-500)');
  });

  it.each([
    [80, 'var(--color-success-500)'],
    [79, 'var(--color-warning-500)'],
    [20, 'var(--color-warning-500)'],
    [19, 'var(--color-error-500)'],
  ])('成功率模式在 %s%% 使用对应语义色', (percentage, expectedStroke) => {
    const wrapper = mount(CProgress, {
      props: { percentage, variant: 'success-rate' },
    });

    expect(wrapper.findAll('circle')[1]!.attributes('stroke')).toBe(expectedStroke);
  });

  it('缓存模式使用凭证环主题色和浅色主题轨道', () => {
    const wrapper = mount(CProgress, {
      props: { percentage: 60, variant: 'cache-hit' },
    });
    const circles = wrapper.findAll('circle');

    expect(circles[0]!.attributes('stroke')).toBe(
      'color-mix(in oklch, var(--color-brand-500) 20%, var(--surface))',
    );
    expect(circles[1]!.attributes('stroke')).toBe('var(--color-brand-500)');
  });

  it('thresholdColors=false 用渐变 url', () => {
    const wrapper = mount(CProgress, {
      props: { percentage: 60, thresholdColors: false },
    });
    const progressCircle = wrapper.findAll('circle')[1];
    expect(progressCircle.attributes('stroke')).toContain('url(');
    expect(wrapper.find('linearGradient').exists()).toBe(true);
  });

  it('thresholdColors=false 含 linearGradient brand→accent', () => {
    const wrapper = mount(CProgress, {
      props: { percentage: 60, thresholdColors: false },
    });
    const gradient = wrapper.find('linearGradient');
    expect(gradient.exists()).toBe(true);
    const stops = gradient.findAll('stop');
    expect(stops).toHaveLength(2);
    expect(stops[0].attributes('stop-color')).toBe('var(--color-brand-500)');
    expect(stops[1].attributes('stop-color')).toBe('var(--color-accent-400)');
  });

  it('轨道 circle stroke=surface-3', () => {
    const wrapper = mount(CProgress);
    const trackCircle = wrapper.findAll('circle')[0];
    expect(trackCircle.attributes('stroke')).toBe('var(--color-surface-3)');
  });

  it('SVG rotate(-90) 让起点在12点', () => {
    const wrapper = mount(CProgress);
    const svg = wrapper.find('svg');
    const transform = svg.attributes('style') || '';
    const progressCircle = wrapper.findAll('circle')[1];
    const circleTransform = progressCircle.attributes('transform') || '';
    expect(transform.includes('rotate(-90deg)') || circleTransform.includes('rotate(-90')).toBe(
      true,
    );
  });

  it('中心文字渲染百分比', () => {
    const wrapper = mount(CProgress, { props: { percentage: 42 } });
    const text = wrapper.find('text');
    expect(text.exists()).toBe(true);
    expect(text.text()).toBe('42%');
  });

  it('支持用自定义中心文字表示暂无数据', () => {
    const wrapper = mount(CProgress, { props: { percentage: 0, label: '-' } });

    expect(wrapper.find('text').text()).toBe('-');
  });

  it('中心文字含正确 class', () => {
    const wrapper = mount(CProgress, { props: { percentage: 42 } });
    const text = wrapper.find('text');
    expect(text.classes()).toContain('font-display');
    expect(text.classes()).toContain('font-bold');
    expect(text.classes()).toContain('text-[18px]');
    expect(text.classes()).toContain('tabular-nums');
    expect(text.classes()).toContain('text-text-strong');
  });

  it('中心文字 fill 跟随 currentColor，支持昼夜模式变色', () => {
    const wrapper = mount(CProgress, { props: { percentage: 42 } });
    const text = wrapper.find('text');
    expect(text.attributes('fill')).toBe('currentColor');
  });

  it('小尺寸进度环使用更紧凑的中心文字', () => {
    const wrapper = mount(CProgress, { props: { percentage: 66, size: 52 } });
    const text = wrapper.find('text');
    expect(text.classes()).toContain('text-[13px]');
    expect(text.classes()).not.toContain('text-[18px]');
  });

  it('进度 circle 含过渡 class', () => {
    const wrapper = mount(CProgress, { props: { percentage: 50 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const style = progressCircle.attributes('style') || '';
    expect(style).toContain('transition');
    expect(style).toContain('stroke-dashoffset');
  });

  it('percentage 边界 clamp 到 0-100', () => {
    const wrapper = mount(CProgress, { props: { percentage: 150 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const dashoffset = parseFloat(progressCircle.attributes('stroke-dashoffset') || '0');
    expect(dashoffset).toBeCloseTo(0, 1);
    expect(wrapper.find('text').text()).toBe('100%');
  });

  it('percentage 负数 clamp 到 0', () => {
    const wrapper = mount(CProgress, { props: { percentage: -10 } });
    const progressCircle = wrapper.findAll('circle')[1];
    const circumference = 2 * Math.PI * 36;
    const dashoffset = parseFloat(progressCircle.attributes('stroke-dashoffset') || '0');
    expect(dashoffset).toBeCloseTo(circumference, 1);
    expect(wrapper.find('text').text()).toBe('0%');
  });

  it('提供 progressbar ARIA 语义并把非有限值归一化为 0', () => {
    const wrapper = mount(CProgress, { props: { percentage: Number.NaN } });
    const svg = wrapper.get('svg');
    expect(svg.attributes('role')).toBe('progressbar');
    expect(svg.attributes('aria-valuemin')).toBe('0');
    expect(svg.attributes('aria-valuemax')).toBe('100');
    expect(svg.attributes('aria-valuenow')).toBe('0');
    expect(wrapper.get('text').text()).toBe('0%');
  });

  it('不同实例使用唯一渐变 id', () => {
    const wrapper = mount({
      components: { CProgress },
      template:
        '<div><CProgress :threshold-colors="false" /><CProgress :threshold-colors="false" /></div>',
    });
    const gradients = wrapper.findAll('linearGradient');
    const ids = gradients.map((gradient) => gradient.attributes('id'));
    expect(new Set(ids).size).toBe(2);
    const strokes = wrapper
      .findAll('circle')
      .filter((circle) => circle.attributes('stroke')?.startsWith('url('));
    expect(strokes.map((circle) => circle.attributes('stroke'))).toEqual(
      ids.map((id) => `url(#${id})`),
    );
  });
});
