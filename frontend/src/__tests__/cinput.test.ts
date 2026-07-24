import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import CInput from '../components/ui/CInput.vue';

describe('CInput', () => {
  it('默认渲染 input，type=text', () => {
    const wrapper = mount(CInput);
    expect(wrapper.find('input').exists()).toBe(true);
    expect(wrapper.find('input').attributes('type')).toBe('text');
  });

  it('md 默认尺寸 class', () => {
    const wrapper = mount(CInput);
    const input = wrapper.find('input');
    expect(input.classes()).toContain('h-[38px]');
    expect(input.classes()).toContain('px-3');
    expect(input.classes()).toContain('text-sm');
    expect(input.classes()).toContain('rounded-md');
  });

  it('size=sm 尺寸 class', () => {
    const wrapper = mount(CInput, { props: { size: 'sm' } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('h-8');
    expect(input.classes()).toContain('text-[13px]');
    expect(input.classes()).toContain('rounded-sm');
  });

  it('modelValue 双向绑定（input 事件 emit update:modelValue）', async () => {
    const wrapper = mount(CInput, { props: { modelValue: '' } });
    const input = wrapper.find('input');
    await input.setValue('hello');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['hello']);
  });

  it('placeholder 透传', () => {
    const wrapper = mount(CInput, { props: { placeholder: '请输入' } });
    expect(wrapper.find('input').attributes('placeholder')).toBe('请输入');
  });

  it('maxlength 透传到真实 input 与 textarea', () => {
    const input = mount(CInput, { props: { maxlength: 80 } });
    const textarea = mount(CInput, { props: { type: 'textarea', maxlength: 120 } });
    expect(input.get('input').attributes('maxlength')).toBe('80');
    expect(textarea.get('textarea').attributes('maxlength')).toBe('120');
  });

  it('type=password 时渲染 password toggle 按钮', () => {
    const wrapper = mount(CInput, { props: { type: 'password' } });
    const button = wrapper.get('button');
    const input = wrapper.get('input');
    expect(input.attributes('type')).toBe('password');
    expect(input.attributes('id')).toBeDefined();
    expect(button.attributes('aria-label')).toBe('显示密码');
    expect(button.attributes('aria-pressed')).toBe('false');
    expect(button.attributes('aria-controls')).toBe(input.attributes('id'));
    expect(button.attributes('tabindex')).toBeUndefined();
  });

  it('点击 password toggle 切换 input type 为 text', async () => {
    const wrapper = mount(CInput, { props: { type: 'password' } });
    await wrapper.find('button').trigger('click');
    expect(wrapper.find('input').attributes('type')).toBe('text');
    expect(wrapper.find('button').attributes('aria-label')).toBe('隐藏密码');
    expect(wrapper.find('button').attributes('aria-pressed')).toBe('true');
    await wrapper.find('button').trigger('click');
    expect(wrapper.find('input').attributes('type')).toBe('password');
  });

  it('type=password 且 showPasswordToggle=false 时不渲染 toggle', () => {
    const wrapper = mount(CInput, {
      props: { type: 'password', showPasswordToggle: false },
    });
    expect(wrapper.find('button').exists()).toBe(false);
  });

  it('type=text 时不渲染 toggle 按钮', () => {
    const wrapper = mount(CInput, { props: { type: 'text' } });
    expect(wrapper.find('button').exists()).toBe(false);
  });

  it('readonly 时 class 含 readonly 相关', () => {
    const wrapper = mount(CInput, { props: { readonly: true } });
    const input = wrapper.find('input');
    expect(input.attributes('readonly')).toBeDefined();
    expect(input.classes()).toContain('readonly:bg-surface-2');
    expect(input.classes()).toContain('readonly:font-mono');
  });

  it('disabled 时 disabled 属性', () => {
    const wrapper = mount(CInput, { props: { disabled: true } });
    const input = wrapper.find('input');
    expect(input.attributes('disabled')).toBeDefined();
    expect(input.classes()).toContain('disabled:bg-surface-2');
    expect(input.classes()).toContain('disabled:cursor-not-allowed');
  });

  it('error=true 时 class 含 error 相关', () => {
    const wrapper = mount(CInput, { props: { error: true } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('border-error-500');
    expect(input.classes()).toContain('ring-error-500/20');
    expect(input.attributes('aria-invalid')).toBe('true');
  });

  it('error=false 时不输出 aria-invalid', () => {
    const inputWrapper = mount(CInput);
    const textareaWrapper = mount(CInput, { props: { type: 'textarea' } });

    expect(inputWrapper.find('input').attributes('aria-invalid')).toBeUndefined();
    expect(textareaWrapper.find('textarea').attributes('aria-invalid')).toBeUndefined();
  });

  it('type=textarea 时渲染 textarea 而非 input', () => {
    const wrapper = mount(CInput, { props: { type: 'textarea' } });
    expect(wrapper.find('textarea').exists()).toBe(true);
    expect(wrapper.find('input').exists()).toBe(false);
  });

  it('textarea autosize minRows 影响 min-height', () => {
    const wrapper = mount(CInput, {
      props: { type: 'textarea', autosize: { minRows: 4 } },
    });
    const ta = wrapper.find('textarea');
    // 每行约 1.6rem，4 行约 6.4rem
    expect(ta.attributes('style')).toContain('min-height: 6.4rem');
    expect(ta.classes()).not.toContain('min-h-[6.4rem]');
    expect(ta.classes()).toContain('resize-y');
    expect(ta.classes()).toContain('leading-relaxed');
  });

  it('textarea 默认 minRows=3', () => {
    const wrapper = mount(CInput, { props: { type: 'textarea' } });
    const ta = wrapper.find('textarea');
    expect(ta.attributes('style')).toContain('min-height: 4.8rem');
    expect(ta.classes()).not.toContain('min-h-[4.8rem]');
  });

  it('textarea + error 时含 error class', () => {
    const wrapper = mount(CInput, {
      props: { type: 'textarea', error: true },
    });
    const ta = wrapper.find('textarea');
    expect(ta.classes()).toContain('border-error-500');
    expect(ta.classes()).toContain('ring-error-500/20');
    expect(ta.attributes('aria-invalid')).toBe('true');
  });

  it('keyup 事件 emit', async () => {
    const wrapper = mount(CInput);
    await wrapper.find('input').trigger('keyup');
    expect(wrapper.emitted('keyup')).toBeTruthy();
  });

  it('enter 事件 emit（keyup.enter）', async () => {
    const wrapper = mount(CInput);
    await wrapper.find('input').trigger('keyup', { key: 'Enter' });
    expect(wrapper.emitted('enter')).toBeTruthy();
  });

  it('输入法组合输入期间按 Enter 只转发 keyup，不触发 enter', async () => {
    const wrapper = mount(CInput);
    await wrapper.find('input').trigger('keyup', { key: 'Enter', isComposing: true });

    expect(wrapper.emitted('keyup')).toHaveLength(1);
    expect(wrapper.emitted('enter')).toBeUndefined();
  });

  it('autofocus 在 onMounted 时 focus', () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus');
    mount(CInput, { props: { autofocus: true } });
    expect(focusSpy).toHaveBeenCalled();
    focusSpy.mockRestore();
  });

  it('autocomplete 透传', () => {
    const wrapper = mount(CInput, { props: { autocomplete: 'off' } });
    expect(wrapper.find('input').attributes('autocomplete')).toBe('off');
  });

  it('default class 含基础样式（bg-surface text-text border border-border）', () => {
    const wrapper = mount(CInput);
    const input = wrapper.find('input');
    expect(input.classes()).toContain('bg-surface');
    expect(input.classes()).toContain('text-text');
    expect(input.classes()).toContain('border');
    expect(input.classes()).toContain('border-border');
  });

  it('focus class 使用单层焦点样式', () => {
    const wrapper = mount(CInput);
    const input = wrapper.find('input');
    expect(input.classes()).toContain('c-control-focus');
    expect(input.classes()).not.toContain('focus:ring-2');
    expect(input.classes()).not.toContain('focus:ring-brand-500/20');
  });

  it('textarea 也使用单层焦点样式', () => {
    const wrapper = mount(CInput, { props: { type: 'textarea' } });
    const textarea = wrapper.find('textarea');
    expect(textarea.classes()).toContain('c-control-focus');
    expect(textarea.classes()).not.toContain('focus:ring-2');
    expect(textarea.classes()).not.toContain('focus:ring-brand-500/20');
  });

  it('hover class', () => {
    const wrapper = mount(CInput);
    const input = wrapper.find('input');
    expect(input.classes()).toContain('hover:border-border-strong');
  });

  it('placeholder class', () => {
    const wrapper = mount(CInput);
    const input = wrapper.find('input');
    expect(input.classes()).toContain('placeholder:text-muted/60');
  });

  it('password toggle 按钮含 ghost 样式与正确尺寸', () => {
    const wrapper = mount(CInput, { props: { type: 'password' } });
    const btn = wrapper.find('button');
    expect(btn.classes()).toContain('w-[38px]');
    expect(btn.find('svg').exists()).toBe(true);
  });
});
