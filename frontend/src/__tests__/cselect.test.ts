import { describe, expect, it, afterEach, vi } from 'vitest';
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils';
import { ListFilter } from '@lucide/vue';
import CSelect from '../components/ui/CSelect.vue';

enableAutoUnmount(afterEach);

describe('CSelect', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('渲染 trigger 按钮', () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    expect(wrapper.find('.c-select-trigger').exists()).toBe(true);
  });

  it('trigger 含 md 尺寸 class', () => {
    const wrapper = mount(CSelect, {
      props: { options: [] },
    });
    const trigger = wrapper.find('.c-select-trigger');
    expect(trigger.classes()).toContain('h-[38px]');
    expect(trigger.classes()).toContain('px-3');
    expect(trigger.classes()).toContain('text-sm');
    expect(trigger.classes()).toContain('rounded-md');
    expect(trigger.classes()).toContain('border');
  });

  it('sm 尺寸使用 32px 高度和紧凑间距', () => {
    const wrapper = mount(CSelect, { props: { options: [], size: 'sm' } });
    const trigger = wrapper.find('.c-select-trigger');
    expect(trigger.classes()).toContain('h-8');
    expect(trigger.classes()).toContain('px-2.5');
    expect(trigger.classes()).toContain('text-[13px]');
    expect(trigger.classes()).not.toContain('h-[38px]');
  });

  it('trigger 使用单层焦点样式', () => {
    const wrapper = mount(CSelect, {
      props: { options: [] },
    });
    const trigger = wrapper.find('.c-select-trigger');
    expect(trigger.classes()).toContain('c-control-focus');
    expect(trigger.classes()).not.toContain('focus:ring-2');
  });

  it('placeholder 透传显示在 trigger', () => {
    const wrapper = mount(CSelect, {
      props: { options: [], placeholder: '请选择' },
    });
    expect(wrapper.find('.c-select-trigger').text()).toContain('请选择');
  });

  it('modelValue 匹配时显示对应 label', () => {
    const wrapper = mount(CSelect, {
      props: {
        modelValue: 'b',
        options: [
          { label: 'A', value: 'a' },
          { label: 'B', value: 'b' },
        ],
      },
    });
    expect(wrapper.find('.c-select-trigger').text()).toContain('B');
  });

  it('trigger 含 ChevronDown 图标', () => {
    const wrapper = mount(CSelect, { props: { options: [] } });
    expect(wrapper.find('svg').exists()).toBe(true);
  });

  it('点击 trigger 打开下拉', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
  });

  it('再次点击 trigger 关闭下拉', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    const trigger = wrapper.find('.c-select-trigger');
    await trigger.trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
    await trigger.trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('打开另一个 Select 时关闭当前 Select', async () => {
    const wrapper = mount(
      {
        components: { CSelect },
        template: `
          <div>
            <CSelect :options="[{ label: 'A', value: 'a' }]" />
            <CSelect :options="[{ label: 'B', value: 'b' }]" />
          </div>
        `,
      },
      { attachTo: document.body },
    );
    const selects = wrapper.findAllComponents(CSelect);

    await selects[0]!.find('.c-select-trigger').trigger('click');
    expect(selects[0]!.find('.c-select-panel').exists()).toBe(true);

    await selects[1]!.find('.c-select-trigger').trigger('click');
    expect(selects[0]!.find('.c-select-panel').exists()).toBe(false);
    expect(selects[1]!.find('.c-select-panel').exists()).toBe(true);
  });

  it('打开时 ChevronDown rotate-180', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const icon = wrapper.find('.c-select-chevron');
    expect(icon.classes()).toContain('rotate-180');
  });

  it('下拉面板含正确 class', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const panel = wrapper.find('.c-select-panel');
    expect(panel.classes()).toContain('bg-surface');
    expect(panel.classes()).toContain('border');
    expect(panel.classes()).toContain('rounded-lg');
    expect(panel.classes()).toContain('absolute');
    expect(panel.classes()).toContain('max-h-[18rem]');
    expect(panel.classes()).toContain('flex');
    expect(panel.classes()).toContain('flex-col');
    expect(panel.classes()).not.toContain('overflow-y-auto');
  });

  it('下拉动画使用淡入位移且关闭采用 ease-in，不缩放内容', () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    const transition = wrapper.findComponent({ name: 'Transition' });
    expect(transition.props('enterActiveClass')).toContain('duration-[var(--duration-base)]');
    expect(transition.props('enterActiveClass')).toContain('ease-[var(--ease-out-quad)]');
    expect(transition.props('leaveActiveClass')).toContain('duration-[var(--duration-base)]');
    expect(transition.props('leaveActiveClass')).toContain('ease-[var(--ease-in-quad)]');
    expect(transition.props('enterFromClass')).toBe('opacity-0 -translate-y-2');
    expect(transition.props('leaveToClass')).toBe('opacity-0 -translate-y-2');
    expect(transition.html()).not.toContain('scale-95');
  });

  it('下方放不下且上方空间更大时面板自动向上展开', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    vi.spyOn(wrapper.find('.c-select-trigger').element, 'getBoundingClientRect').mockReturnValue({
      top: 700,
      bottom: 732,
      height: 32,
      left: 0,
      right: 100,
      width: 100,
      x: 0,
      y: 700,
      toJSON: () => ({}),
    });
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(144);
    vi.stubGlobal('innerHeight', 800);

    await wrapper.find('.c-select-trigger').trigger('click');
    await flushPromises();

    const panel = wrapper.find('.c-select-panel');
    expect(panel.classes()).toContain('bottom-full');
    expect(panel.classes()).toContain('mb-1');
    expect(panel.classes()).not.toContain('top-full');
    expect(panel.classes()).not.toContain('mt-1');
  });

  it('下方能放下时面板保持向下展开', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    vi.spyOn(wrapper.find('.c-select-trigger').element, 'getBoundingClientRect').mockReturnValue({
      top: 100,
      bottom: 132,
      height: 32,
      left: 0,
      right: 100,
      width: 100,
      x: 0,
      y: 100,
      toJSON: () => ({}),
    });
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(144);
    vi.stubGlobal('innerHeight', 800);

    await wrapper.find('.c-select-trigger').trigger('click');
    await flushPromises();

    const panel = wrapper.find('.c-select-panel');
    expect(panel.classes()).toContain('top-full');
    expect(panel.classes()).toContain('mt-1');
    expect(panel.classes()).not.toContain('bottom-full');
  });

  it('选项渲染', async () => {
    const wrapper = mount(CSelect, {
      props: {
        options: [
          { label: 'A', value: 'a' },
          { label: 'B', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const options = wrapper.findAll('.c-select-option');
    expect(options.length).toBe(2);
    expect(options[0].text()).toContain('A');
    expect(options[1].text()).toContain('B');
  });

  it('点击选项 emit update:modelValue 并关闭', async () => {
    const wrapper = mount(CSelect, {
      props: {
        options: [
          { label: 'A', value: 'a' },
          { label: 'B', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    await wrapper.findAll('.c-select-option')[1].trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeTruthy();
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['b']);
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('底部操作显示在选项下方，点击后关闭下拉并发出事件', async () => {
    const wrapper = mount(CSelect, {
      props: {
        options: [{ label: 'A', value: 'a' }],
        footerActionLabel: '查看统计详情',
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const action = wrapper.find('.c-select-footer-action');
    expect(action.text()).toContain('查看统计详情');
    expect(action.findComponent(ListFilter).exists()).toBe(true);
    await action.trigger('click');
    expect(wrapper.emitted('footer-action')).toEqual([[]]);
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('选中项高亮 + Check 图标', async () => {
    const wrapper = mount(CSelect, {
      props: {
        modelValue: 'a',
        options: [
          { label: 'A', value: 'a' },
          { label: 'B', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const options = wrapper.findAll('.c-select-option');
    expect(options[0].classes()).toContain('bg-soft-brand');
    expect(options[0].classes()).toContain('text-tone-brand');
    expect(options[0].find('svg').exists()).toBe(true);
  });

  it('未选中项不含 selected class', async () => {
    const wrapper = mount(CSelect, {
      props: {
        modelValue: 'a',
        options: [
          { label: 'A', value: 'a' },
          { label: 'B', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const options = wrapper.findAll('.c-select-option');
    expect(options[1].classes()).not.toContain('bg-soft-brand');
  });

  it('filterable：面板顶部含 input', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [{ label: 'A', value: 'a' }],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.find('.c-select-filter').exists()).toBe(true);
    expect(wrapper.find('.c-select-options').classes()).toContain('overflow-y-auto');
    const filterArea = wrapper.find('.c-select-filter-area');
    expect(filterArea.classes()).toEqual(expect.arrayContaining(['shrink-0', 'px-1', 'pt-1']));
    expect(wrapper.find('.c-select-filter').classes()).toContain('w-full');
  });

  it('filterable：过滤输入框使用单层焦点样式', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [{ label: 'A', value: 'a' }],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const filterInput = wrapper.find('.c-select-filter');
    expect(filterInput.classes()).toContain('c-control-focus');
    expect(filterInput.classes()).not.toContain('focus:ring-2');
  });

  it('filterable：输入过滤选项', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [
          { label: 'Apple', value: 'a' },
          { label: 'Banana', value: 'b' },
          { label: 'Cherry', value: 'c' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const filterInput = wrapper.find('.c-select-filter');
    await filterInput.setValue('an');
    const options = wrapper.findAll('.c-select-option');
    expect(options.length).toBe(1);
    expect(options[0].text()).toContain('Banana');
  });

  it('filterable：无匹配时不渲染选项', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [{ label: 'Apple', value: 'a' }],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    await wrapper.find('.c-select-filter').setValue('zzz');
    expect(wrapper.findAll('.c-select-option').length).toBe(0);
  });

  it('loading 时 trigger 显示 CSpin', () => {
    const wrapper = mount(CSelect, {
      props: { loading: true, options: [] },
    });
    expect(wrapper.find('[role="status"]').exists()).toBe(true);
  });

  it('非 loading 时不显示 CSpin', () => {
    const wrapper = mount(CSelect, {
      props: { loading: false, options: [] },
    });
    expect(wrapper.find('[role="status"]').exists()).toBe(false);
  });

  it('外部点击关闭下拉', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
      attachTo: document.body,
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
    document.body.click();
    await flushPromises();
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('ESC 关闭下拉', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('disabled 时不打开下拉', async () => {
    const wrapper = mount(CSelect, {
      props: { disabled: true, options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('disabled 时 trigger 含禁用外观，光标交给全局按钮规则', () => {
    const wrapper = mount(CSelect, {
      props: { disabled: true, options: [] },
    });
    const trigger = wrapper.find('.c-select-trigger');
    expect(trigger.classes()).toContain('disabled:bg-surface-2');
    expect(trigger.classes()).not.toContain('disabled:cursor-not-allowed');
  });

  it('点击自身内部的过滤输入框时保持展开', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [{ label: 'A', value: 'a' }],
      },
      attachTo: document.body,
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const filterInput = wrapper.find('.c-select-filter');
    await filterInput.trigger('click');
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
  });

  it('value 为 number 类型时也能匹配', async () => {
    const wrapper = mount(CSelect, {
      props: {
        modelValue: 2,
        options: [
          { label: '一', value: 1 },
          { label: '二', value: 2 },
        ],
      },
    });
    expect(wrapper.find('.c-select-trigger').text()).toContain('二');
    await wrapper.find('.c-select-trigger').trigger('click');
    await wrapper.findAll('.c-select-option')[0].trigger('click');
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([1]);
  });

  it('filterable 搜索后选择，query 清空', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [
          { label: 'Apple', value: 'a' },
          { label: 'Banana', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    await wrapper.find('.c-select-filter').setValue('an');
    await wrapper.findAll('.c-select-option')[0].trigger('click');
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.findAll('.c-select-option').length).toBe(2);
  });

  it('filterable 输入后关闭下拉再打开，query 已清空', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [
          { label: 'Apple', value: 'a' },
          { label: 'Banana', value: 'b' },
        ],
      },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    await wrapper.find('.c-select-filter').setValue('an');
    document.body.click();
    await flushPromises();
    await wrapper.find('.c-select-trigger').trigger('click');
    expect(wrapper.findAll('.c-select-option').length).toBe(2);
  });

  it('非 ESC keydown 不关闭', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    const event = new KeyboardEvent('keydown', { key: 'Enter' });
    document.dispatchEvent(event);
    await flushPromises();
    expect(wrapper.find('.c-select-panel').exists()).toBe(true);
  });

  it('ESC keydown 未打开时不触发错误', async () => {
    const wrapper = mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
    });
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
    expect(wrapper.find('.c-select-panel').exists()).toBe(false);
  });

  it('document click 未打开时不触发错误', async () => {
    mount(CSelect, {
      props: { options: [{ label: 'A', value: 'a' }] },
      attachTo: document.body,
    });
    document.body.click();
    await flushPromises();
    expect(true).toBe(true);
  });

  it('filterable 打开下拉时 filter input 自动聚焦', async () => {
    const wrapper = mount(CSelect, {
      props: {
        filterable: true,
        options: [{ label: 'A', value: 'a' }],
      },
      attachTo: document.body,
    });
    await wrapper.find('.c-select-trigger').trigger('click');
    await flushPromises();
    const filterInput = wrapper.find('.c-select-filter');
    expect(filterInput.element).toBe(document.activeElement);
  });
});
