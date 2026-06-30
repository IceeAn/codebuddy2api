import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CSpin from '../components/ui/CSpin.vue';

describe('CSpin', () => {
  it('默认 size=md，渲染 span', () => {
    const wrapper = mount(CSpin);
    expect(wrapper.element.tagName).toBe('SPAN');
    expect(wrapper.classes()).toContain('w-5');
    expect(wrapper.classes()).toContain('h-5');
  });

  it('size=sm 尺寸 14px', () => {
    const wrapper = mount(CSpin, { props: { size: 'sm' } });
    expect(wrapper.classes()).toContain('w-[14px]');
    expect(wrapper.classes()).toContain('h-[14px]');
  });

  it('size=lg 尺寸 28px', () => {
    const wrapper = mount(CSpin, { props: { size: 'lg' } });
    expect(wrapper.classes()).toContain('w-7');
    expect(wrapper.classes()).toContain('h-7');
  });

  it('inherit=false（默认）时含 text-brand-500', () => {
    const wrapper = mount(CSpin);
    expect(wrapper.classes()).toContain('text-brand-500');
  });

  it('inherit=true 时不含 text-brand-500（继承父色）', () => {
    const wrapper = mount(CSpin, { props: { inherit: true } });
    expect(wrapper.classes()).not.toContain('text-brand-500');
  });

  it('sm/md 用 border-2', () => {
    const sm = mount(CSpin, { props: { size: 'sm' } });
    const md = mount(CSpin, { props: { size: 'md' } });
    expect(sm.classes()).toContain('border-2');
    expect(md.classes()).toContain('border-2');
  });

  it('lg 用 border-[3px]', () => {
    const wrapper = mount(CSpin, { props: { size: 'lg' } });
    expect(wrapper.classes()).toContain('border-[3px]');
  });

  it('含 animate-spin 与 rounded-full', () => {
    const wrapper = mount(CSpin);
    expect(wrapper.classes()).toContain('animate-spin');
    expect(wrapper.classes()).toContain('rounded-full');
  });

  it('含 role=status 和 aria-label', () => {
    const wrapper = mount(CSpin);
    expect(wrapper.attributes('role')).toBe('status');
    expect(wrapper.attributes('aria-label')).toBe('加载中');
  });
});
