import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CCheckbox from '../components/ui/CCheckbox.vue';

describe('CCheckbox', () => {
  it('默认 modelValue=false，box 未选中态', () => {
    const wrapper = mount(CCheckbox);
    const box = wrapper.find('.c-checkbox-box');
    expect(box.exists()).toBe(true);
    expect(box.classes()).not.toContain('bg-brand-600');
    expect(box.classes()).not.toContain('border-brand-600');
  });

  it('modelValue=true 时 box 选中态含 brand 色与 Check 图标', () => {
    const wrapper = mount(CCheckbox, { props: { modelValue: true } });
    const box = wrapper.find('.c-checkbox-box');
    expect(box.classes()).toContain('bg-brand-600');
    expect(box.classes()).toContain('border-brand-600');
    expect(box.classes()).not.toContain('bg-surface');
    expect(box.find('svg').exists()).toBe(true);
  });

  it('点击 label 触发 update:modelValue（false → true）', async () => {
    const wrapper = mount(CCheckbox, {
      attachTo: document.body,
      props: { modelValue: false },
    });
    wrapper.find('label').element.click();
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')).toHaveLength(1);
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([true]);
    wrapper.unmount();
  });

  it('点击 label 触发 update:modelValue（true → false）', async () => {
    const wrapper = mount(CCheckbox, {
      attachTo: document.body,
      props: { modelValue: true },
    });
    wrapper.find('label').element.click();
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false]);
    wrapper.unmount();
  });

  it('indeterminate=true 时显示横线 div', () => {
    const wrapper = mount(CCheckbox, {
      props: { indeterminate: true, modelValue: false },
    });
    const box = wrapper.find('.c-checkbox-box');
    expect(box.classes()).toContain('bg-brand-600');
    expect(box.classes()).toContain('border-brand-600');
    expect(box.classes()).not.toContain('bg-surface');
    const line = box.find('div');
    expect(line.exists()).toBe(true);
    expect(line.classes()).toContain('bg-white');
    expect(box.find('svg').exists()).toBe(false);
  });

  it('disabled 时含 cursor-not-allowed 且不触发 update', async () => {
    const wrapper = mount(CCheckbox, {
      props: { disabled: true, modelValue: false },
    });
    expect(wrapper.classes()).toContain('cursor-not-allowed');
    expect(wrapper.classes()).toContain('opacity-50');
    await wrapper.find('label').trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('default slot 渲染 label 文本', () => {
    const wrapper = mount(CCheckbox, {
      slots: { default: '记住我' },
    });
    expect(wrapper.text()).toContain('记住我');
  });

  it('box 含 hover 品牌色边框，焦点外发光由全局 peer 规则统一提供', () => {
    const wrapper = mount(CCheckbox);
    // 真实 input 标记为 peer，作为焦点来源
    const input = wrapper.find('input[type="checkbox"]');
    expect(input.classes()).toContain('peer');
    const box = wrapper.find('.c-checkbox-box');
    expect(box.classes()).toContain('hover:border-brand-500');
    expect(box.classes()).not.toContain('peer-focus-visible:ring-2');
    expect(box.classes()).not.toContain('peer-focus-visible:ring-brand-500/30');
    expect(box.classes()).toContain('transition-[background-color,border-color,box-shadow]');
  });

  it('容器含 inline-flex items-center gap-2', () => {
    const wrapper = mount(CCheckbox);
    expect(wrapper.classes()).toContain('inline-flex');
    expect(wrapper.classes()).toContain('items-center');
    expect(wrapper.classes()).toContain('gap-2');
  });

  it('box 尺寸 h-4 w-4 rounded-xs border-[1.5px]', () => {
    const wrapper = mount(CCheckbox);
    const box = wrapper.find('.c-checkbox-box');
    expect(box.classes()).toContain('h-4');
    expect(box.classes()).toContain('w-4');
    expect(box.classes()).toContain('rounded-xs');
    expect(box.classes()).toContain('border-[1.5px]');
    expect(box.classes()).toContain('border-border-strong');
    expect(box.classes()).toContain('bg-surface');
  });

  it('hidden 真实 input[type=checkbox] 含 sr-only 与 peer', () => {
    const wrapper = mount(CCheckbox);
    const input = wrapper.find('input[type="checkbox"]');
    expect(input.exists()).toBe(true);
    expect(input.classes()).toContain('sr-only');
    expect(input.classes()).toContain('peer');
  });
});
