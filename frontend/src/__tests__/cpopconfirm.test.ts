import { describe, expect, it, afterEach, vi } from 'vitest';
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils';
import CPopconfirm from '../components/ui/CPopconfirm.vue';

const attach = (): HTMLElement => {
  const el = document.createElement('div');
  document.body.appendChild(el);
  return el;
};

const getPopover = (): HTMLElement | null => document.body.querySelector('.c-popconfirm-popover');

const getPopoverButtons = (): HTMLButtonElement[] =>
  Array.from(document.body.querySelectorAll('.c-popconfirm-actions button'));

const openPopover = async (wrapper: ReturnType<typeof mount>): Promise<void> => {
  await wrapper.find('button').trigger('click');
  await flushPromises();
};

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    x: left,
    y: top,
    toJSON: () => ({}),
  } as DOMRect;
}

enableAutoUnmount(afterEach);

describe('CPopconfirm', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('默认渲染触发元素', () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    expect(wrapper.find('button').exists()).toBe(true);
    expect(wrapper.element.tagName).toBe('SPAN');
    expect(wrapper.classes()).toContain('relative');
    expect(wrapper.classes()).toContain('inline-flex');
  });

  it('初始不显示浮层', () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    expect(wrapper.find('.c-popconfirm-popover').exists()).toBe(false);
    expect(getPopover()).toBeNull();
  });

  it('点击触发元素显示浮层', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(wrapper.find('.c-popconfirm-popover').exists()).toBe(false);
    expect(getPopover()).not.toBeNull();
  });

  it('再次点击触发元素关闭浮层（toggle）', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(getPopover()).not.toBeNull();
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('快速打开后关闭时不执行过期定位', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    const button = wrapper.find('button').element as HTMLButtonElement;

    button.click();
    button.click();
    await flushPromises();

    expect(getPopover()).toBeNull();
  });

  it('title prop 渲染描述', async () => {
    const wrapper = mount(CPopconfirm, {
      props: { title: '确定删除吗？' },
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const desc = getPopover()?.querySelector('.c-popconfirm-desc');
    expect(desc).not.toBeNull();
    expect(desc?.textContent).toBe('确定删除吗？');
    expect(desc?.classList.contains('text-sm')).toBe(true);
    expect(desc?.classList.contains('text-text')).toBe(true);
  });

  it('含 AlertTriangle 图标（warning 色）', async () => {
    const wrapper = mount(CPopconfirm, {
      props: { title: '提示' },
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const icon = getPopover()?.querySelector('.c-popconfirm-icon');
    expect(icon).not.toBeNull();
    expect(icon?.querySelector('svg')).not.toBeNull();
    expect(icon?.classList.contains('text-warning-500')).toBe(true);
  });

  it('默认 confirmText=确认 cancelText=取消', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].textContent).toBe('取消');
    expect(buttons[1].textContent).toBe('确认');
  });

  it('confirmText/cancelText 自定义', async () => {
    const wrapper = mount(CPopconfirm, {
      props: { confirmText: '删除', cancelText: '算了' },
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    expect(buttons[0].textContent).toBe('算了');
    expect(buttons[1].textContent).toBe('删除');
  });

  it('点击确认 emit confirm 并关闭', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    buttons[1].click();
    await flushPromises();
    expect(wrapper.emitted('confirm')).toBeTruthy();
    expect(getPopover()).toBeNull();
  });

  it('点击取消 emit cancel 并关闭', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    buttons[0].click();
    await flushPromises();
    expect(wrapper.emitted('cancel')).toBeTruthy();
    expect(getPopover()).toBeNull();
  });

  it('外部点击关闭', async () => {
    const wrapper = mount(CPopconfirm, {
      attachTo: attach(),
      slots: { default: '<button class="trigger">删除</button>' },
    });
    await wrapper.find('button.trigger').trigger('click');
    await flushPromises();
    expect(getPopover()).not.toBeNull();
    document.body.click();
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('ESC 关闭', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(getPopover()).not.toBeNull();
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
    expect(getPopover()).toBeNull();
  });

  it('非 ESC 键不关闭', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(getPopover()).not.toBeNull();
    const event = new KeyboardEvent('keydown', { key: 'Enter' });
    document.dispatchEvent(event);
    await flushPromises();
    expect(getPopover()).not.toBeNull();
  });

  it('confirmVariant=danger 默认，确认按钮为 danger variant', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    expect(buttons[1].classList.contains('bg-error-600')).toBe(true);
  });

  it('confirmVariant=primary，确认按钮为 primary variant', async () => {
    const wrapper = mount(CPopconfirm, {
      props: { confirmVariant: 'primary' },
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const buttons = getPopoverButtons();
    expect(buttons[1].classList.contains('bg-brand-600')).toBe(true);
  });

  it('浮层含正确 class', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const popover = getPopover();
    expect(popover).not.toBeNull();
    expect(popover?.classList.contains('fixed')).toBe(true);
    expect(popover?.classList.contains('z-50')).toBe(true);
    expect(popover?.classList.contains('w-[15rem]')).toBe(true);
    expect(popover?.classList.contains('p-3.5')).toBe(true);
    expect(popover?.classList.contains('rounded-lg')).toBe(true);
    expect(popover?.classList.contains('border')).toBe(true);
    expect(popover?.classList.contains('border-border')).toBe(true);
    expect(popover?.classList.contains('bg-surface')).toBe(true);
    expect(popover?.classList.contains('shadow-[var(--shadow-popover)]')).toBe(true);
    expect(popover?.style.left).toBeTruthy();
    expect(popover?.style.top).toBeTruthy();
  });

  it('滚动和窗口缩放时更新浮层坐标', async () => {
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        if (this.classList.contains('c-popconfirm-popover')) {
          return rect(0, 0, 240, 80);
        }
        return rect(100, 50, 32, 32);
      });
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });

    await openPopover(wrapper);
    expect(getPopover()?.style.left).toBe('8px');
    expect(getPopover()?.style.top).toBe('90px');

    rectSpy.mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('c-popconfirm-popover')) {
        return rect(0, 0, 240, 80);
      }
      return rect(360, 120, 32, 32);
    });
    window.dispatchEvent(new Event('resize'));
    await flushPromises();
    expect(getPopover()?.style.left).toBe('256px');
    expect(getPopover()?.style.top).toBe('160px');

    window.dispatchEvent(new Event('scroll'));
    await flushPromises();
    expect(getPopover()?.style.left).toBe('256px');
    rectSpy.mockRestore();
  });

  it('actions 右对齐', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    const actions = getPopover()?.querySelector('.c-popconfirm-actions');
    expect(actions).not.toBeNull();
    expect(actions?.classList.contains('flex')).toBe(true);
    expect(actions?.classList.contains('justify-end')).toBe(true);
    expect(actions?.classList.contains('gap-2')).toBe(true);
    expect(actions?.classList.contains('mt-3')).toBe(true);
  });

  it('组件卸载时移除事件监听', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(getPopover()).not.toBeNull();
    wrapper.unmount();
    // 卸载后外部点击/ESC 不报错
    document.body.click();
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
  });

  it('打开状态下再次点击触发元素关闭时移除监听', async () => {
    const wrapper = mount(CPopconfirm, {
      slots: { default: '<button>删除</button>' },
    });
    await openPopover(wrapper);
    expect(getPopover()).not.toBeNull();
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(getPopover()).toBeNull();
  });
});
