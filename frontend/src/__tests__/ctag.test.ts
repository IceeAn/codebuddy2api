import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CTag from '../components/ui/CTag.vue';

describe('CTag', () => {
  it('默认 type=default 含 default class', () => {
    const wrapper = mount(CTag);
    expect(wrapper.classes()).toContain('bg-surface-2');
    expect(wrapper.classes()).toContain('text-muted');
  });

  it('type=brand 含 brand class 与暗色变体', () => {
    const wrapper = mount(CTag, { props: { type: 'brand' } });
    expect(wrapper.classes()).toContain('bg-brand-50');
    expect(wrapper.classes()).toContain('text-brand-700');
    expect(wrapper.classes()).toContain('dark:bg-brand-500/15');
    expect(wrapper.classes()).toContain('dark:text-brand-300');
  });

  it('type=success 含 success class 与暗色变体', () => {
    const wrapper = mount(CTag, { props: { type: 'success' } });
    expect(wrapper.classes()).toContain('bg-success-500/12');
    expect(wrapper.classes()).toContain('text-success-600');
    expect(wrapper.classes()).toContain('dark:bg-success-500/15');
    expect(wrapper.classes()).toContain('dark:text-success-400');
  });

  it('type=warning 含 warning class 与暗色变体', () => {
    const wrapper = mount(CTag, { props: { type: 'warning' } });
    expect(wrapper.classes()).toContain('bg-warning-500/15');
    expect(wrapper.classes()).toContain('text-warning-600');
    expect(wrapper.classes()).toContain('dark:text-warning-400');
  });

  it('type=error 含 error class 与暗色变体', () => {
    const wrapper = mount(CTag, { props: { type: 'error' } });
    expect(wrapper.classes()).toContain('bg-error-500/12');
    expect(wrapper.classes()).toContain('text-error-600');
    expect(wrapper.classes()).toContain('dark:bg-error-500/15');
    expect(wrapper.classes()).toContain('dark:text-error-400');
  });

  it('dot=true 时渲染圆点 span', () => {
    const wrapper = mount(CTag, { props: { dot: true } });
    const dot = wrapper.find('span span');
    expect(dot.exists()).toBe(true);
    expect(dot.classes()).toContain('rounded-full');
    expect(dot.classes()).toContain('w-1.5');
    expect(dot.classes()).toContain('h-1.5');
  });

  it('dot=true 时 type=brand 圆点含 brand 背景色', () => {
    const wrapper = mount(CTag, { props: { dot: true, type: 'brand' } });
    const dot = wrapper.find('span span');
    expect(dot.classes()).toContain('bg-brand-500');
  });

  it('default slot 渲染内容', () => {
    const wrapper = mount(CTag, { slots: { default: '可用' } });
    expect(wrapper.text()).toBe('可用');
  });

  it('容器含基础 class（高度、padding、字体、圆角）', () => {
    const wrapper = mount(CTag);
    expect(wrapper.classes()).toContain('inline-flex');
    expect(wrapper.classes()).toContain('items-center');
    expect(wrapper.classes()).toContain('gap-1.5');
    expect(wrapper.classes()).toContain('h-[22px]');
    expect(wrapper.classes()).toContain('px-2');
    expect(wrapper.classes()).toContain('text-xs');
    expect(wrapper.classes()).toContain('font-semibold');
    expect(wrapper.classes()).toContain('rounded-sm');
    expect(wrapper.classes()).toContain('whitespace-nowrap');
    expect(wrapper.classes()).toContain('overflow-hidden');
  });
});
