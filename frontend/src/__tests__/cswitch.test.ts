import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CSwitch from '../components/ui/CSwitch.vue';

describe('CSwitch', () => {
  it('默认 modelValue=false, size=md，渲染 button[role=switch]', () => {
    const wrapper = mount(CSwitch);
    expect(wrapper.element.tagName).toBe('BUTTON');
    expect(wrapper.attributes('role')).toBe('switch');
    expect(wrapper.attributes('aria-checked')).toBe('false');
  });

  it('md 默认 track 尺寸 w-10 h-[22px]', () => {
    const wrapper = mount(CSwitch);
    expect(wrapper.classes()).toContain('w-10');
    expect(wrapper.classes()).toContain('h-[22px]');
  });

  it('modelValue=false 时 track off class（bg-slate-300 dark:bg-slate-600）', () => {
    const wrapper = mount(CSwitch, { props: { modelValue: false } });
    expect(wrapper.classes()).toContain('bg-slate-300');
    expect(wrapper.classes()).toContain('dark:bg-slate-600');
  });

  it('modelValue=true 时 track on class（bg-brand-600 dark:bg-brand-500）且 aria-checked=true', () => {
    const wrapper = mount(CSwitch, { props: { modelValue: true } });
    expect(wrapper.classes()).toContain('bg-brand-600');
    expect(wrapper.classes()).toContain('dark:bg-brand-500');
    expect(wrapper.attributes('aria-checked')).toBe('true');
  });

  it('modelValue=true md 时 thumb translate-x-[20px]', () => {
    const wrapper = mount(CSwitch, { props: { modelValue: true } });
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.exists()).toBe(true);
    expect(thumb.classes()).toContain('translate-x-[20px]');
  });

  it('modelValue=false md 时 thumb translate-x-[2px]', () => {
    const wrapper = mount(CSwitch, { props: { modelValue: false } });
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.classes()).toContain('translate-x-[2px]');
  });

  it('点击 toggle（false → true，emit update:modelValue）', async () => {
    const wrapper = mount(CSwitch, { props: { modelValue: false } });
    await wrapper.trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([true]);
  });

  it('点击 toggle（true → false）', async () => {
    const wrapper = mount(CSwitch, { props: { modelValue: true } });
    await wrapper.trigger('click');
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false]);
  });

  it('size=sm 时 track w-8 h-5，thumb w-3.5 h-3.5', () => {
    const wrapper = mount(CSwitch, { props: { size: 'sm' } });
    expect(wrapper.classes()).toContain('w-8');
    expect(wrapper.classes()).toContain('h-5');
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.classes()).toContain('w-3.5');
    expect(thumb.classes()).toContain('h-3.5');
  });

  it('size=sm modelValue=true 时 thumb translate-x-[16px]', () => {
    const wrapper = mount(CSwitch, { props: { size: 'sm', modelValue: true } });
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.classes()).toContain('translate-x-[16px]');
  });

  it('size=sm modelValue=false 时 thumb translate-x-[2px]', () => {
    const wrapper = mount(CSwitch, { props: { size: 'sm', modelValue: false } });
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.classes()).toContain('translate-x-[2px]');
  });

  it('disabled 时不触发 toggle，光标交给全局按钮规则', async () => {
    const wrapper = mount(CSwitch, {
      props: { disabled: true, modelValue: false },
    });
    expect(wrapper.classes()).toContain('opacity-50');
    expect(wrapper.classes()).not.toContain('cursor-not-allowed');
    expect(wrapper.classes()).not.toContain('cursor-pointer');
    await wrapper.trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('焦点外发光由全局规则统一提供', () => {
    const wrapper = mount(CSwitch);
    expect(wrapper.classes()).not.toContain('focus-visible:ring-2');
    expect(wrapper.classes()).not.toContain('focus-visible:ring-brand-500/30');
    expect(wrapper.classes()).toContain('transition-[background-color,box-shadow]');
  });

  it('thumb 含 bg-white shadow-sm 与过渡 class', () => {
    const wrapper = mount(CSwitch);
    const thumb = wrapper.find('.c-switch-thumb');
    expect(thumb.classes()).toContain('bg-white');
    expect(thumb.classes()).toContain('shadow-sm');
    expect(thumb.classes()).toContain('transition-transform');
  });
});
