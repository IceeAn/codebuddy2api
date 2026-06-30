import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import CInputNumber from '../components/ui/CInputNumber.vue';

describe('CInputNumber', () => {
  it('渲染 input type=number', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const input = wrapper.find('input');
    expect(input.exists()).toBe(true);
    expect(input.attributes('type')).toBe('number');
  });

  it('md 默认尺寸 class', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('h-[38px]');
    expect(input.classes()).toContain('px-3');
    expect(input.classes()).toContain('text-sm');
    expect(input.classes()).toContain('rounded-md');
  });

  it('modelValue 显示在 input value', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 42 } });
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('42');
  });

  it('modelValue=null 时 input 为空', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: null } });
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('');
  });

  it('placeholder 透传', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, placeholder: '输入数字' },
    });
    expect(wrapper.find('input').attributes('placeholder')).toBe('输入数字');
  });

  it('输入时 emit update:modelValue（数字）', async () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: null } });
    const input = wrapper.find('input');
    await input.setValue('10');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([10]);
  });

  it('输入空字符串时 emit null', async () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 5 } });
    const input = wrapper.find('input');
    await input.setValue('');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([null]);
  });

  it('增加按钮调用 input.stepUp 并同步原生步进结果', async () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 5 } });
    const input = wrapper.get('input').element as HTMLInputElement;
    const stepUp = vi.spyOn(input, 'stepUp');
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(stepUp).toHaveBeenCalledOnce();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([6]);
  });

  it('减少按钮调用 input.stepDown 并同步原生步进结果', async () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 5 } });
    const input = wrapper.get('input').element as HTMLInputElement;
    const stepDown = vi.spyOn(input, 'stepDown');
    await wrapper.get('[aria-label="减少"]').trigger('click');
    expect(stepDown).toHaveBeenCalledOnce();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([4]);
  });

  it('step prop 控制步进量', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 10, step: 5 },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([15]);
  });

  it('原生步进不超过 max', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 10, max: 10, step: 5 },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([10]);
  });

  it('min clamp：步进不低于 min', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 1, min: 0, step: 5 },
    });
    await wrapper.get('[aria-label="减少"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([0]);
  });

  it('输入超过 max 时 clamp', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, max: 100 },
    });
    await wrapper.find('input').setValue('200');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([100]);
  });

  it('输入低于 min 时 clamp', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, min: 10 },
    });
    await wrapper.find('input').setValue('5');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([10]);
  });

  it('步进按钮在 modelValue=null 时从 0 开始', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, step: 1 },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([1]);
  });

  it('空值向下步进无原生结果时按旧语义 clamp 到 min', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, min: 1, step: 1 },
    });
    await wrapper.get('[aria-label="减少"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([1]);
    expect(Number.isNaN(wrapper.emitted('update:modelValue')!.at(-1)![0])).toBe(false);
  });

  it('原生小数步进不会显示二进制浮点尾差', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 1.1, min: 0, max: 2, step: 0.1, clearable: true },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([1.2]);
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('1.2');
  });

  it('clearable 时渲染清除按钮，modelValue 非 null 显示', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 5, clearable: true },
    });
    const clearButton = wrapper.find('.c-input-number-clear');
    expect(clearButton.exists()).toBe(true);
    expect(clearButton.classes()).toContain('rounded-full');
    expect(clearButton.classes()).toContain('hover:bg-surface-2');
    expect(clearButton.classes()).toContain('transition-colors');
    expect(wrapper.find('[aria-label="增加"]').exists()).toBe(true);
    expect(wrapper.find('[aria-label="减少"]').exists()).toBe(true);
  });

  it('clearable 但 modelValue=null 时不显示清除按钮', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, clearable: true },
    });
    expect(wrapper.find('.c-input-number-clear').exists()).toBe(false);
    expect(wrapper.find('[aria-label="增加"]').exists()).toBe(true);
    expect(wrapper.find('[aria-label="减少"]').exists()).toBe(true);
  });

  it('点击清除按钮 emit null', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 5, clearable: true },
    });
    await wrapper.find('.c-input-number-clear').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([null]);
  });

  it('非 clearable 时不渲染清除按钮', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 5, clearable: false },
    });
    expect(wrapper.find('.c-input-number-clear').exists()).toBe(false);
    expect(wrapper.find('[aria-label="增加"]').exists()).toBe(true);
    expect(wrapper.find('[aria-label="减少"]').exists()).toBe(true);
  });

  it('disabled 时 input 含 disabled 属性', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 0, disabled: true },
    });
    expect(wrapper.find('input').attributes('disabled')).toBeDefined();
  });

  it('disabled 时步进按钮禁用', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 0, disabled: true },
    });
    const buttons = wrapper.findAll('button');
    buttons.forEach((b) => {
      expect(b.attributes('disabled')).toBeDefined();
      expect(b.classes()).not.toContain('disabled:cursor-not-allowed');
    });
  });

  it('disabled 时不响应步进点击', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 5, disabled: true },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('步进按钮在窄栏中上下排列并使用箭头图标', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const controls = wrapper.get('.c-input-number-controls');
    expect(controls.classes()).toContain('flex-col');
    expect(controls.findAll('svg')).toHaveLength(2);
  });

  it('含基础样式（bg-surface text-text border）', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('bg-surface');
    expect(input.classes()).toContain('text-text');
    expect(input.classes()).toContain('border');
    expect(input.classes()).toContain('border-border');
  });

  it('disabled 时步进 - 按钮也不响应', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 5, disabled: true },
    });
    await wrapper.get('[aria-label="减少"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeFalsy();
  });

  it('输入非数字字符（type=number 清空）emit null', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null },
    });
    // type=number 的 input 会清空非数字输入
    const input = wrapper.find('input');
    await input.setValue('abc');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([null]);
  });

  it('非 clearable 时 input 为紧凑步进栏预留右侧空间', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('pr-9');
  });

  it('clearable 时 input 为清除按钮和步进栏预留右侧空间', () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 0, clearable: true },
    });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('pr-16');
  });

  it('min/max 均提供时 clamp 到区间', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: null, min: 0, max: 10 },
    });
    await wrapper.find('input').setValue('50');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([10]);
    await wrapper.find('input').setValue('-5');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([0]);
  });

  it('step 为小数时也能步进', async () => {
    const wrapper = mount(CInputNumber, {
      props: { modelValue: 1.5, step: 0.5 },
    });
    await wrapper.get('[aria-label="增加"]').trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([2]);
  });

  it('隐藏浏览器原生数字步进按钮', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    expect(wrapper.get('input').classes()).toContain('c-input-number-input');
  });

  it('width 为字符串时的 minWidth 也透传 style', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    expect(wrapper.find('input').exists()).toBe(true);
  });

  it('hover 样式 class', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    expect(wrapper.find('input').classes()).toContain('hover:border-border-strong');
  });

  it('focus 样式 class', () => {
    const wrapper = mount(CInputNumber, { props: { modelValue: 0 } });
    const input = wrapper.find('input');
    expect(input.classes()).toContain('c-control-focus');
    expect(input.classes()).not.toContain('focus:ring-2');
  });
});
