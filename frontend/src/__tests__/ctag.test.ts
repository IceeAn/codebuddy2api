import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CTag from '../components/ui/CTag.vue';

describe('CTag', () => {
  it('默认 type=default 含 default class', () => {
    const wrapper = mount(CTag);
    expect(wrapper.classes()).toContain('bg-surface-2');
    expect(wrapper.classes()).toContain('text-muted');
  });

  it('type=brand 使用固定语义色', () => {
    const wrapper = mount(CTag, { props: { type: 'brand' } });
    expect(wrapper.classes()).toContain('bg-soft-brand');
    expect(wrapper.classes()).toContain('text-tone-brand');
    expect(wrapper.classes().some((className) => className.startsWith('dark:'))).toBe(false);
  });

  it('type=success 使用固定语义色', () => {
    const wrapper = mount(CTag, { props: { type: 'success' } });
    expect(wrapper.classes()).toContain('bg-soft-success');
    expect(wrapper.classes()).toContain('text-tone-success');
  });

  it('type=warning 使用固定语义色', () => {
    const wrapper = mount(CTag, { props: { type: 'warning' } });
    expect(wrapper.classes()).toContain('bg-soft-warning');
    expect(wrapper.classes()).toContain('text-tone-warning');
  });

  it('type=error 使用固定语义色', () => {
    const wrapper = mount(CTag, { props: { type: 'error' } });
    expect(wrapper.classes()).toContain('bg-soft-error');
    expect(wrapper.classes()).toContain('text-tone-error');
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
