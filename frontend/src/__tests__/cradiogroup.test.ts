import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import CRadioButton from '../components/ui/CRadioButton.vue';

describe('CRadioGroup', () => {
  it('渲染容器含分段控制器 class', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    expect(wrapper.classes()).toContain('inline-flex');
    expect(wrapper.classes()).toContain('bg-surface-2');
    expect(wrapper.classes()).toContain('rounded-md');
    expect(wrapper.classes()).toContain('p-0.5');
  });

  it('子项选中时含选中 class', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    const buttons = wrapper.findAllComponents(CRadioButton);
    expect(buttons[0].classes()).toContain('bg-surface');
    expect(buttons[0].classes()).toContain('text-text-strong');
    expect(buttons[1].classes()).not.toContain('bg-surface');
  });

  it('点击未选中项 emit update:modelValue', async () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    const buttons = wrapper.findAllComponents(CRadioButton);
    await buttons[1].trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['b']);
  });

  it('点击已选中项不 emit（保持当前值）', async () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    await wrapper.findComponent(CRadioButton).trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('modelValue 变化后选中态跟随', async () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    const buttons = wrapper.findAllComponents(CRadioButton);
    expect(buttons[0].classes()).toContain('bg-surface');
    await wrapper.setProps({ modelValue: 'b' });
    expect(buttons[0].classes()).not.toContain('bg-surface');
    expect(buttons[1].classes()).toContain('bg-surface');
  });
});

describe('CRadioButton', () => {
  it('渲染 label prop 文本', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a" label="选项A" />',
      },
      global: { components: { CRadioButton } },
    });
    expect(wrapper.text()).toContain('选项A');
  });

  it('default slot 优先于 label prop', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a" label="prop文案">slot文案</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    expect(wrapper.text()).toContain('slot文案');
  });

  it('含基础 class（高度、padding、字号、圆角）', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    const btn = wrapper.findComponent(CRadioButton);
    expect(btn.classes()).toContain('h-7');
    expect(btn.classes()).toContain('px-3');
    expect(btn.classes()).toContain('text-xs');
    expect(btn.classes()).toContain('font-medium');
    expect(btn.classes()).toContain('rounded-sm');
    expect(btn.classes()).not.toContain('cursor-pointer');
  });

  it('未选中时含 text-muted', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    const buttons = wrapper.findAllComponents(CRadioButton);
    expect(buttons[1].classes()).toContain('text-muted');
  });

  it('选中时含 shadow-xs', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    const btn = wrapper.findComponent(CRadioButton);
    expect(btn.classes()).toContain('shadow-[var(--shadow-xs)]');
  });

  it('点击 emit 事件（通过 CRadioGroup 验证联动）', async () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: '' },
      slots: {
        default: '<CRadioButton value="x">X</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    await wrapper.findComponent(CRadioButton).trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')![0]).toEqual(['x']);
  });

  it('单独使用（无 CRadioGroup 父级）时抛错', () => {
    expect(() => mount(CRadioButton, { props: { value: 'x' } })).toThrow(
      'CRadioButton 必须在 CRadioGroup 内使用',
    );
  });
});
