import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CDynamicTags from '../components/ui/CDynamicTags.vue';

describe('CDynamicTags', () => {
  it('渲染已有标签', () => {
    const wrapper = mount(CDynamicTags, {
      props: { modelValue: ['vue', 'react'] },
    });
    expect(wrapper.text()).toContain('vue');
    expect(wrapper.text()).toContain('react');
  });

  it('容器含 flex-wrap 与 gap class', () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    expect(wrapper.classes()).toContain('flex');
    expect(wrapper.classes()).toContain('min-w-0');
    expect(wrapper.classes()).toContain('flex-wrap');
    expect(wrapper.classes()).toContain('items-center');
    expect(wrapper.classes()).toContain('gap-1.5');
  });

  it('渲染输入框', () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    expect(wrapper.find('input').exists()).toBe(true);
  });

  it('placeholder 默认为"添加..."', () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    expect(wrapper.find('input').attributes('placeholder')).toBe('添加...');
  });

  it('placeholder prop 透传', () => {
    const wrapper = mount(CDynamicTags, {
      props: { modelValue: [], placeholder: '输入标签' },
    });
    expect(wrapper.find('input').attributes('placeholder')).toBe('输入标签');
  });

  it('回车确认添加标签（emit update:modelValue）', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    const input = wrapper.find('input');
    await input.setValue('新标签');
    await input.trigger('keyup', { key: 'Enter' });
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['新标签']]);
  });

  it('回车时输入为空不添加', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['a'] } });
    const input = wrapper.find('input');
    await input.setValue('');
    await input.trigger('keyup', { key: 'Enter' });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('回车时输入为纯空格不添加', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['a'] } });
    const input = wrapper.find('input');
    await input.setValue('   ');
    await input.trigger('keyup', { key: 'Enter' });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('回车添加后清空输入框', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    const input = wrapper.find('input');
    await input.setValue('x');
    await input.trigger('keyup', { key: 'Enter' });
    expect((input.element as HTMLInputElement).value).toBe('');
  });

  it('输入框失焦时提交待定标签并清空输入', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['已有标签'] } });
    const input = wrapper.find('input');
    await input.setValue('待保存标签');
    await input.trigger('blur');

    expect(wrapper.emitted('update:modelValue')).toEqual([[['已有标签', '待保存标签']]]);
    expect((input.element as HTMLInputElement).value).toBe('');
  });

  it('Backspace 空输入时删除最后一个标签', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['a', 'b'] } });
    const input = wrapper.find('input');
    await input.trigger('keydown', { key: 'Backspace' });
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['a']]);
  });

  it('Backspace 有输入时不删除标签', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['a', 'b'] } });
    const input = wrapper.find('input');
    await input.setValue('x');
    await input.trigger('keydown', { key: 'Backspace' });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('点击标签的 X 删除指定标签', async () => {
    const wrapper = mount(CDynamicTags, {
      props: { modelValue: ['a', 'b', 'c'] },
    });
    const removeBtns = wrapper.findAll('.c-dynamic-tags-remove');
    expect(removeBtns.length).toBe(3);
    await removeBtns[1].trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['a', 'c']]);
  });

  it('输入框含透明样式并与标签使用相同圆角和内边距', () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('h-[22px]');
    expect(input.classes()).toContain('w-[5rem]');
    expect(input.classes()).toContain('bg-transparent');
    expect(input.classes()).toContain('rounded-sm');
    expect(input.classes()).toContain('px-1');
  });

  it('非 Enter 键不触发添加', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    const input = wrapper.find('input');
    await input.setValue('x');
    await input.trigger('keyup', { key: 'Space' });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('粘贴包含多个逗号且以逗号结尾的内容时，过滤空项并将其余分段转为标签', async () => {
    const wrapper = mount(CDynamicTags, {
      attachTo: document.body,
      props: { modelValue: ['x', 'y', 'z'] },
    });
    const input = wrapper.find('input');
    const inputElement = input.element as HTMLInputElement;
    inputElement.focus();

    inputElement.value = ',a,,b,,';
    await input.trigger('input', { inputType: 'insertFromPaste' });

    expect(wrapper.emitted('update:modelValue')).toEqual([[['x', 'y', 'z', 'a', 'b']]]);
    expect(inputElement.value).toBe('');
    expect(document.activeElement).toBe(input.element);
    wrapper.unmount();
  });

  it('拖拽插入包含多个逗号且未以逗号结尾的内容时，最后一段保留在输入框', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x', 'y', 'z'] } });
    const input = wrapper.find('input');
    const inputElement = input.element as HTMLInputElement;

    inputElement.value = ',a,,b';
    await input.trigger('input', { inputType: 'insertFromDrop' });

    expect(wrapper.emitted('update:modelValue')).toEqual([[['x', 'y', 'z', 'a']]]);
    expect(inputElement.value).toBe('b');
  });

  it('在现有输入末尾输入逗号时等同于回车提交', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x', 'y', 'z'] } });
    const input = wrapper.find('input');

    await input.setValue('a');
    await input.setValue('a,');

    expect(wrapper.emitted('update:modelValue')).toEqual([[['x', 'y', 'z', 'a']]]);
    expect((input.element as HTMLInputElement).value).toBe('');
  });

  it('在现有输入前输入逗号时仅移除逗号', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x', 'y', 'z'] } });
    const input = wrapper.find('input');

    await input.setValue('a');
    await input.setValue(',a');

    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
    expect((input.element as HTMLInputElement).value).toBe('a');
  });

  it('在现有输入中间输入逗号时提交前段并保留后段', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x', 'y', 'z'] } });
    const input = wrapper.find('input');

    await input.setValue('ab');
    await input.setValue('a,b');

    expect(wrapper.emitted('update:modelValue')).toEqual([[['x', 'y', 'z', 'a']]]);
    expect((input.element as HTMLInputElement).value).toBe('b');
  });

  it('只规范化待标签化的分段，最后一段输入保持原样', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x'] } });
    const input = wrapper.find('input');

    await input.setValue('  a  ,   ,, b ,  current  ');

    expect(wrapper.emitted('update:modelValue')).toEqual([[['x', 'a', 'b']]]);
    expect((input.element as HTMLInputElement).value).toBe('  current  ');
  });

  it('输入仅包含逗号和空白时不生成标签', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['x'] } });
    const input = wrapper.find('input');

    await input.setValue(' ,, ,');

    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
    expect((input.element as HTMLInputElement).value).toBe('');
  });

  it('非 Backspace 键不触发删除', async () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: ['a'] } });
    const input = wrapper.find('input');
    await input.trigger('keydown', { key: 'ArrowLeft' });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('删除按钮含 aria-label', () => {
    const wrapper = mount(CDynamicTags, {
      props: { modelValue: ['tag1'] },
    });
    const btn = wrapper.find('.c-dynamic-tags-remove');
    expect(btn.attributes('aria-label')).toBe('删除标签 tag1');
  });

  it('输入框含 placeholder 样式 class', () => {
    const wrapper = mount(CDynamicTags, { props: { modelValue: [] } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('placeholder:text-muted/60');
  });
});
