import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import CTooltip from '../components/ui/CTooltip.vue';

function getPopover(): HTMLElement | null {
  return document.body.querySelector('.c-tooltip-popover');
}

function getPopoverClasses(): string[] {
  return Array.from(getPopover()?.classList ?? []);
}

describe('CTooltip', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '';
  });
  afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  it('默认渲染触发元素', () => {
    const wrapper = mount(CTooltip, {
      slots: { default: '<button>触发</button>' },
    });
    expect(wrapper.find('button').exists()).toBe(true);
    expect(wrapper.element.tagName).toBe('SPAN');
    expect(wrapper.classes()).toContain('relative');
    expect(wrapper.classes()).toContain('inline-flex');
  });

  it('初始不显示浮层', () => {
    mount(CTooltip, {
      props: { content: '提示文本' },
      slots: { default: '<button>触发</button>' },
    });
    expect(getPopover()).toBeNull();
  });

  it('clickable 模式支持点击切换并点击外部关闭', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '触摸提示', clickable: true },
      slots: { default: '<button>触发</button>' },
    });

    await wrapper.trigger('click');
    await flushPromises();
    expect(getPopover()?.textContent).toBe('触摸提示');
    await wrapper.trigger('click');
    await flushPromises();
    expect(getPopover()).toBeNull();

    await wrapper.trigger('click');
    await flushPromises();
    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('clickable 模式处理悬浮计时、内部点击和完整键盘分支', async () => {
    const passive = mount(CTooltip, {
      props: { content: '普通提示' },
      slots: { default: '<button>普通触发</button>' },
    });
    await passive.trigger('click');
    await passive.trigger('keydown', { key: 'Enter' });
    expect(getPopover()).toBeNull();

    const wrapper = mount(CTooltip, {
      props: { content: '交互提示', clickable: true, delay: 300 },
      slots: { default: '<button>交互触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(100);
    await wrapper.trigger('click');
    await flushPromises();
    expect(getPopover()?.textContent).toBe('交互提示');

    (wrapper.vm.$ as any).setupState.handleOutsidePointer({ target: null });
    await wrapper.trigger('pointerdown');
    getPopover()?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    await wrapper.trigger('keydown', { key: 'a' });
    await flushPromises();
    expect(getPopover()).not.toBeNull();

    await wrapper.trigger('keydown', { key: ' ' });
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('clickable 模式可将悬浮提示固定到点击后再关闭', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '固定提示', clickable: true, delay: 300 },
      slots: { default: '<button>触发</button>' },
    });

    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
    await wrapper.trigger('click');
    await wrapper.trigger('mouseleave');
    await flushPromises();
    expect(getPopover()?.textContent).toBe('固定提示');
    await wrapper.trigger('click');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('关闭点击固定的提示时一并移除触发器焦点', async () => {
    const wrapper = mount(CTooltip, {
      attachTo: document.body,
      props: { content: '焦点提示', clickable: true },
      slots: { default: '<button>触发</button>' },
    });
    const button = wrapper.get('button');

    (button.element as HTMLButtonElement).focus();
    await button.trigger('click');
    await flushPromises();
    expect(document.activeElement).toBe(button.element);
    await button.trigger('click');
    await flushPromises();
    expect(getPopover()).toBeNull();
    expect(document.activeElement).not.toBe(button.element);
  });

  it('clickable 模式支持键盘操作', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '数据点', clickable: true },
      attrs: { tabindex: '0', role: 'button' },
      slots: { default: '<span>数据点</span>' },
    });

    expect(wrapper.element.tagName.toLowerCase()).toBe('span');
    expect(wrapper.attributes('tabindex')).toBe('0');
    await wrapper.trigger('keydown', { key: 'Enter' });
    await flushPromises();
    expect(getPopover()?.textContent).toBe('数据点');
    await wrapper.trigger('keydown', { key: 'Escape' });
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('鼠标 enter 后 delay(300ms) 显示浮层', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示文本', delay: 300 },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    expect(getPopover()).toBeNull();
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(getPopover()).toBeNull();
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
    expect(getPopover()?.textContent).toBe('提示文本');
  });

  it('鼠标 leave 后立即隐藏', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示文本', delay: 300 },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
    await wrapper.trigger('mouseleave');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('leave 取消未触发的显示定时器', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示', delay: 300 },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(100);
    await wrapper.trigger('mouseleave');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('自定义 delay prop', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示', delay: 100 },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(99);
    await flushPromises();
    expect(getPopover()).toBeNull();
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
  });

  it('placement=top 浮层在上方', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示', placement: 'top' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    const popover = getPopover();
    expect(popover).not.toBeNull();
    expect(getPopoverClasses()).toContain('fixed');
    expect(getPopoverClasses()).toContain('c-tooltip-placement-top');
    expect(popover?.style.left).toBeTruthy();
    expect(popover?.style.top).toBeTruthy();
  });

  it('placement=bottom 浮层在下方', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示', placement: 'bottom' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    const popover = getPopover();
    expect(popover).not.toBeNull();
    expect(getPopoverClasses()).toContain('fixed');
    expect(getPopoverClasses()).toContain('c-tooltip-placement-bottom');
    expect(popover?.style.left).toBeTruthy();
    expect(popover?.style.top).toBeTruthy();
  });

  it('浮层与触发按钮间距为 4px', async () => {
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        if (this.classList.contains('c-tooltip-popover')) {
          return {
            x: 0,
            y: 0,
            left: 0,
            top: 0,
            right: 80,
            bottom: 24,
            width: 80,
            height: 24,
            toJSON: () => ({}),
          };
        }
        return {
          x: 100,
          y: 100,
          left: 100,
          top: 100,
          right: 140,
          bottom: 132,
          width: 40,
          height: 32,
          toJSON: () => ({}),
        };
      });
    const wrapper = mount(CTooltip, {
      props: { content: '提示', placement: 'bottom' },
      slots: { default: '<button>触发</button>' },
    });

    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(getPopover()?.style.top).toBe('136px');
    rectSpy.mockRestore();
  });

  it('content slot 覆盖 content prop', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: 'prop文本' },
      slots: {
        default: '<button>触发</button>',
        content: '<span class="custom-content">slot文本</span>',
      },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    const popover = getPopover();
    expect(popover).not.toBeNull();
    expect(popover?.querySelector('.custom-content')).not.toBeNull();
    expect(popover?.textContent).not.toContain('prop文本');
  });

  it('浮层含样式 class', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    const classes = getPopoverClasses();
    expect(classes).toContain('fixed');
    expect(classes).toContain('px-2.5');
    expect(classes).toContain('py-1.5');
    expect(classes).toContain('rounded-md');
    expect(classes).toContain('bg-tooltip');
    expect(classes).toContain('text-tooltip-text');
    expect(classes).toContain('text-xs');
    expect(classes).toContain('w-max');
    expect(classes).toContain('max-w-[20rem]');
    expect(classes.some((className) => className.startsWith('dark:'))).toBe(false);
  });

  it('短中文提示按内容宽度展开，避免在表格图标按钮上单字换行', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '测试凭证' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();

    const popover = getPopover();
    expect(popover).not.toBeNull();
    expect(popover?.textContent).toBe('测试凭证');
    expect(getPopoverClasses()).toContain('w-max');
    expect(getPopoverClasses()).toContain('max-w-[20rem]');
  });

  it('长连续文本在最大宽度内允许换行', async () => {
    const wrapper = mount(CTooltip, {
      props: {
        content: 'codebuddy_token_extremely_long_filename_without_breaks_and_more_segments.json',
      },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();

    const classes = getPopoverClasses();
    expect(classes).toContain('max-w-[20rem]');
    expect(classes).toContain('whitespace-normal');
    expect(classes).toContain('break-words');
  });

  it('浮层含 Transition 组件', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
  });

  it('未 enter 直接 leave 时不报错（showTimer=null 分支）', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseleave');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('显示后再次 leave（timer 已执行，仍走 clear 分支）', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示', delay: 100 },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(100);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
    await wrapper.trigger('mouseleave');
    await flushPromises();
    expect(getPopover()).toBeNull();
    await wrapper.trigger('mouseleave');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('卸载时清理尚未触发的显示定时器', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });
    await wrapper.trigger('mouseenter');
    wrapper.unmount();
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(getPopover()).toBeNull();
  });

  it('异步显示过程中组件已卸载时跳过定位', async () => {
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });

    await wrapper.trigger('mouseenter');
    vi.advanceTimersByTime(300);
    wrapper.unmount();
    await flushPromises();

    expect(getPopover()).toBeNull();
  });

  it('未启动定时器时卸载也会清理全局监听', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const wrapper = mount(CTooltip, {
      props: { content: '提示' },
      slots: { default: '<button>触发</button>' },
    });

    wrapper.unmount();

    expect(removeSpy).toHaveBeenCalledWith('scroll', expect.any(Function), true);
    expect(removeSpy).toHaveBeenCalledWith('resize', expect.any(Function));
    removeSpy.mockRestore();
  });
});
