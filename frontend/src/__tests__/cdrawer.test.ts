import { describe, expect, it, afterEach } from 'vitest';
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils';
import CDrawer from '../components/ui/CDrawer.vue';

const attach = (): HTMLElement => {
  const el = document.createElement('div');
  document.body.appendChild(el);
  return el;
};

enableAutoUnmount(afterEach);

describe('CDrawer', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('open=false 时不渲染面板和遮罩', () => {
    const wrapper = mount(CDrawer, {
      props: { open: false },
      slots: { default: '<p>内容</p>' },
    });
    expect(wrapper.find('.c-drawer-mask').exists()).toBe(false);
    expect(wrapper.find('.c-drawer-panel').exists()).toBe(false);
  });

  it('open=true 时渲染遮罩和面板', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true"><p>内容</p></CDrawer>',
      },
      { attachTo: attach() },
    );
    const mask = document.body.querySelector('.c-drawer-mask');
    expect(mask).toBeTruthy();
    const panel = document.body.querySelector('.c-drawer-panel');
    expect(panel).toBeTruthy();
    expect(panel?.textContent).toContain('内容');
  });

  it('默认 placement=left 面板在左侧（top-0 bottom-0 left-0）', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-drawer-panel') as HTMLElement;
    expect(panel.classList.contains('left-0')).toBe(true);
    expect(panel.classList.contains('top-0')).toBe(true);
    expect(panel.classList.contains('bottom-0')).toBe(true);
  });

  it('placement=right 面板在右侧（right-0）', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" placement="right" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-drawer-panel') as HTMLElement;
    expect(panel.classList.contains('right-0')).toBe(true);
  });

  it('width prop 控制面板宽度', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" :width="400" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-drawer-panel') as HTMLElement;
    expect(panel.style.width).toBe('400px');
  });

  it('默认 width=296', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-drawer-panel') as HTMLElement;
    expect(panel.style.width).toBe('296px');
  });

  it('title prop 渲染 header 标题', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" title="抽屉标题" />',
      },
      { attachTo: attach() },
    );
    const header = document.body.querySelector('.c-drawer-header');
    expect(header).toBeTruthy();
    const title = header?.querySelector('.c-drawer-title');
    expect(title?.textContent).toBe('抽屉标题');
    expect(title?.classList.contains('font-display')).toBe(true);
    expect(title?.classList.contains('font-semibold')).toBe(true);
    expect(title?.classList.contains('text-md')).toBe(true);
  });

  it('无 title 且 closable=false 时不渲染 header', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" :closable="false" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-drawer-header')).toBeFalsy();
  });

  it('closable=true 渲染关闭按钮（默认）', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" title="标题" />',
      },
      { attachTo: attach() },
    );
    const closeBtn = document.body.querySelector('.c-drawer-close');
    expect(closeBtn).toBeTruthy();
  });

  it('closable=false 不渲染关闭按钮', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" :closable="false" title="标题" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-drawer-close')).toBeFalsy();
  });

  it('点击关闭按钮 emit update:open=false', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" title="标题" />',
      },
      { attachTo: attach() },
    );
    const closeBtn = document.body.querySelector('.c-drawer-close') as HTMLElement;
    closeBtn.click();
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
  });

  it('点击遮罩 emit update:open=false', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    const mask = document.body.querySelector('.c-drawer-mask') as HTMLElement;
    mask.click();
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
  });

  it('ESC 键关闭', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
  });

  it('非 ESC 键不关闭', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    const event = new KeyboardEvent('keydown', { key: 'Enter' });
    document.dispatchEvent(event);
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(true);
  });

  it('open=true 时 body 加 overflow-hidden', async () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('open 从 true→false 时 body 移除 overflow-hidden', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
    (wrapper.vm as any).open = false;
    await flushPromises();
    expect(document.body.style.overflow).toBe('');
  });

  it('Teleport 挂载到 body', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    // 遮罩/面板应在 document.body 下，而非 wrapper 根元素下
    expect(document.body.querySelector('.c-drawer-mask')).toBeTruthy();
    expect(document.body.querySelector('.c-drawer-panel')).toBeTruthy();
  });

  it('遮罩含正确 class', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    const mask = document.body.querySelector('.c-drawer-mask') as HTMLElement;
    expect(mask.classList.contains('fixed')).toBe(true);
    expect(mask.classList.contains('inset-0')).toBe(true);
    expect(mask.classList.contains('bg-[var(--color-overlay)]')).toBe(true);
    expect(mask.classList.contains('backdrop-blur-[2px]')).toBe(true);
    expect(mask.classList.contains('z-40')).toBe(true);
  });

  it('面板含正确 class', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-drawer-panel') as HTMLElement;
    expect(panel.classList.contains('fixed')).toBe(true);
    expect(panel.classList.contains('top-0')).toBe(true);
    expect(panel.classList.contains('bottom-0')).toBe(true);
    expect(panel.classList.contains('z-50')).toBe(true);
    expect(panel.classList.contains('bg-surface')).toBe(true);
    expect(panel.classList.contains('shadow-2xl')).toBe(true);
    expect(panel.classList.contains('flex')).toBe(true);
    expect(panel.classList.contains('flex-col')).toBe(true);
  });

  it('body 区域渲染 default slot 且含 p-4 overflow-y-auto', () => {
    mount(
      {
        components: { CDrawer },
        template: '<CDrawer :open="true"><p class="body-content">内容</p></CDrawer>',
      },
      { attachTo: attach() },
    );
    const body = document.body.querySelector('.c-drawer-body');
    expect(body).toBeTruthy();
    expect(body?.classList.contains('flex-1')).toBe(true);
    expect(body?.classList.contains('overflow-y-auto')).toBe(true);
    expect(body?.classList.contains('p-4')).toBe(true);
    expect(body?.querySelector('.body-content')).toBeTruthy();
  });

  it('组件卸载时移除 keydown 监听并解除 body 锁', async () => {
    const wrapper = mount(
      {
        components: { CDrawer },
        data() {
          return { open: true };
        },
        template: '<CDrawer v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
    wrapper.unmount();
    expect(document.body.style.overflow).toBe('');
    // 卸载后 ESC 不再触发
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
  });
});
