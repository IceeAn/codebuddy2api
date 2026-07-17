import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import CRadioButton from '../components/ui/CRadioButton.vue';
import CForm from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
    expect(wrapper.classes()).toContain('relative');
    expect(wrapper.attributes('role')).toBe('radiogroup');
  });

  it('共享指示器使用自然内容宽度定位，并随选中项横向滑动', async () => {
    vi.spyOn(HTMLElement.prototype, 'offsetLeft', 'get').mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.textContent === '较长选项' ? 46 : 2;
    });
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.textContent === '较长选项' ? 78 : 44;
    });

    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">较长选项</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    await wrapper.vm.$nextTick();

    const indicator = wrapper.get('.c-radio-group-indicator');
    expect(indicator.classes()).toContain('bg-segment-active');
    expect(indicator.classes()).toContain('transition-transform');
    expect(indicator.classes()).not.toContain('dark:bg-surface-3');
    expect(indicator.attributes('style')).toContain('width: 44px');
    expect(indicator.attributes('style')).toContain('translateX(2px)');

    await wrapper.setProps({ modelValue: 'b' });
    await wrapper.vm.$nextTick();
    expect(indicator.attributes('style')).toContain('width: 78px');
    expect(indicator.attributes('style')).toContain('translateX(46px)');
  });

  it('没有匹配选项时隐藏指示器', async () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: '' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.get('.c-radio-group-indicator').attributes('style')).toContain('display: none');
  });

  it('监听按钮尺寸并在卸载时释放观察器', () => {
    const observe = vi.fn<ResizeObserver['observe']>();
    const unobserve = vi.fn<ResizeObserver['unobserve']>();
    const disconnect = vi.fn<ResizeObserver['disconnect']>();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = observe;
        unobserve = unobserve;
        disconnect = disconnect;
      },
    );
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

    expect(observe).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    expect(unobserve).toHaveBeenCalledTimes(2);
    expect(disconnect).toHaveBeenCalledOnce();
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

  it('modelValue 变化后文字选中态与 ARIA 状态跟随', async () => {
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
    expect(buttons[0].classes()).toContain('text-text-strong');
    expect(buttons[0].attributes('aria-checked')).toBe('true');
    expect(buttons[0].attributes('role')).toBe('radio');
    await wrapper.setProps({ modelValue: 'b' });
    expect(buttons[0].classes()).toContain('text-muted');
    expect(buttons[0].attributes('aria-checked')).toBe('false');
    expect(buttons[1].classes()).toContain('text-text-strong');
    expect(buttons[1].attributes('aria-checked')).toBe('true');
  });

  it('暴露 radiogroup/radio 语义与 roving tabindex', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'b', ariaLabel: '状态筛选' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    expect(wrapper.attributes('role')).toBe('radiogroup');
    expect(wrapper.attributes('aria-label')).toBe('状态筛选');
    const radios = wrapper.findAll('[role="radio"]');
    expect(radios.map((radio) => radio.attributes('aria-checked'))).toEqual(['false', 'true']);
    expect(radios.map((radio) => radio.attributes('tabindex'))).toEqual(['-1', '0']);
  });

  it('方向键与 Home/End 循环移动焦点并选择', async () => {
    const wrapper = mount(CRadioGroup, {
      attachTo: document.body,
      props: { modelValue: 'a' },
      slots: {
        default: `
          <CRadioButton value="a">A</CRadioButton>
          <CRadioButton value="b">B</CRadioButton>
          <CRadioButton value="c">C</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    const radios = wrapper.findAll<HTMLElement>('[role="radio"]');
    radios[0].element.focus();
    await radios[0].trigger('keydown', { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(radios[2].element);
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['c']);
    await radios[2].trigger('keydown', { key: 'Home' });
    expect(document.activeElement).toBe(radios[0].element);
    await radios[0].trigger('keydown', { key: 'End' });
    expect(document.activeElement).toBe(radios[2].element);
    await radios[2].trigger('keydown', { key: 'ArrowRight' });
    expect(document.activeElement).toBe(radios[0].element);
    await radios[0].trigger('keydown', { key: 'ArrowDown' });
    await radios[1].trigger('keydown', { key: 'ArrowUp' });
    await radios[0].trigger('keydown', { key: 'Enter' });
    await flushPromises();
    expect(document.activeElement).toBe(radios[0].element);
  });

  it('忽略容器自身的非 Radio 键盘事件，并处理重复值注册', async () => {
    const wrapper = mount(CRadioGroup, {
      slots: {
        default: `
          <CRadioButton value="a">A1</CRadioButton>
          <CRadioButton value="a">A2</CRadioButton>
        `,
      },
      global: { components: { CRadioButton } },
    });
    await wrapper.trigger('keydown', { key: 'ArrowRight' });
    await wrapper.trigger('focusout', { relatedTarget: document.body });
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
    wrapper.unmount();
  });

  it('在表单项中继承标签名称，并忽略组内焦点移动的 blur', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CRadioGroup, CRadioButton },
        data: () => ({ model: { state: 'a' } }),
        template: `
          <CForm :model="model">
            <CFormItem label="状态" path="state">
              <CRadioGroup v-model="model.state">
                <CRadioButton value="a">A</CRadioButton>
                <CRadioButton value="b">B</CRadioButton>
              </CRadioGroup>
            </CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const group = wrapper.get('[role="radiogroup"]');
    const radios = wrapper.findAll('[role="radio"]');
    expect(group.attributes('aria-labelledby')).toBe(wrapper.get('label').attributes('id'));
    await group.trigger('focusout', { relatedTarget: radios[1].element });
    await group.trigger('focusout', { relatedTarget: document.body });
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
    expect(btn.classes()).toContain('transition-[color]');
    expect(btn.classes()).not.toContain('transition-colors');
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

  it('选中背景和阴影交由共享指示器绘制', () => {
    const wrapper = mount(CRadioGroup, {
      props: { modelValue: 'a' },
      slots: {
        default: '<CRadioButton value="a">A</CRadioButton>',
      },
      global: { components: { CRadioButton } },
    });
    const btn = wrapper.findComponent(CRadioButton);
    expect(btn.classes()).not.toContain('bg-segment-active');
    expect(btn.classes()).not.toContain('shadow-[var(--shadow-xs)]');
    expect(wrapper.get('.c-radio-group-indicator').classes()).toContain(
      'shadow-[var(--shadow-xs)]',
    );
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
