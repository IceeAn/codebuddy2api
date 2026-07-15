import { describe, expect, it, afterEach } from 'vitest';
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils';
import CModal from '../components/ui/CModal.vue';

const attach = (): HTMLElement => {
  const el = document.createElement('div');
  document.body.appendChild(el);
  return el;
};

enableAutoUnmount(afterEach);

describe('CModal', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('open=false 时不渲染遮罩和面板', () => {
    mount(CModal, {
      props: { open: false },
      slots: { default: '<p>内容</p>' },
    });
    expect(document.body.querySelector('.c-modal-mask')).toBeFalsy();
    expect(document.body.querySelector('.c-modal-panel')).toBeFalsy();
  });

  it('open=true 时渲染遮罩和面板', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true"><p>内容</p></CModal>',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-mask')).toBeTruthy();
    const panel = document.body.querySelector('.c-modal-panel');
    expect(panel).toBeTruthy();
    expect(panel?.textContent).toContain('内容');
  });

  it('默认 width=min(30rem, 90vw)（jsdom 不渲染 CSS 函数值，验证 prop 默认值）', () => {
    const wrapper = mount(CModal, { props: { open: true } });
    expect(wrapper.props('width')).toBe('min(30rem, 90vw)');
  });

  it('width prop 自定义（jsdom 渲染合法值）', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" width="40rem" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-modal-panel') as HTMLElement;
    expect(panel.style.width).toBe('40rem');
  });

  it('title prop 渲染 header 标题', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" title="模态标题" />',
      },
      { attachTo: attach() },
    );
    const header = document.body.querySelector('.c-modal-header');
    expect(header).toBeTruthy();
    const title = header?.querySelector('.c-modal-title');
    expect(title?.textContent).toBe('模态标题');
    expect(title?.classList.contains('font-display')).toBe(true);
    expect(title?.classList.contains('font-semibold')).toBe(true);
    expect(title?.classList.contains('text-md')).toBe(true);
  });

  it('无 title 且 closable=false 时不渲染 header', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" :closable="false" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-header')).toBeFalsy();
  });

  it('closable=true 默认渲染关闭按钮', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" title="标题" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-close')).toBeTruthy();
  });

  it('closable=false 不渲染关闭按钮', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" :closable="false" title="标题" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-close')).toBeFalsy();
  });

  it('点击关闭按钮 emit update:open=false', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" title="标题" />',
      },
      { attachTo: attach() },
    );
    const closeBtn = document.body.querySelector('.c-modal-close') as HTMLElement;
    closeBtn.click();
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
  });

  it('点击遮罩 emit update:open=false', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    const mask = document.body.querySelector('.c-modal-mask') as HTMLElement;
    mask.click();
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
  });

  it('点击面板不会触发遮罩关闭', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-modal-panel') as HTMLElement;
    panel.click();
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(true);
  });

  it('ESC 键关闭', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
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
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
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
        components: { CModal },
        template: '<CModal :open="true" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('open 从 true→false 时 body 移除 overflow-hidden', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
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
        components: { CModal },
        template: '<CModal :open="true" />',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-mask')).toBeTruthy();
    expect(document.body.querySelector('.c-modal-panel')).toBeTruthy();
  });

  it('面板含正确 class', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" />',
      },
      { attachTo: attach() },
    );
    const panel = document.body.querySelector('.c-modal-panel') as HTMLElement;
    expect(panel.classList.contains('bg-surface')).toBe(true);
    expect(panel.classList.contains('rounded-2xl')).toBe(true);
    expect(panel.classList.contains('flex')).toBe(true);
    expect(panel.classList.contains('flex-col')).toBe(true);
    expect(panel.classList.contains('overflow-hidden')).toBe(true);
    expect(panel.classList.contains('max-h-[85vh]')).toBe(true);
  });

  it('遮罩含正确 class', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" />',
      },
      { attachTo: attach() },
    );
    const mask = document.body.querySelector('.c-modal-mask') as HTMLElement;
    expect(mask.classList.contains('fixed')).toBe(true);
    expect(mask.classList.contains('inset-0')).toBe(true);
    expect(mask.classList.contains('bg-[var(--color-overlay)]')).toBe(true);
    expect(mask.classList.contains('backdrop-blur-[2px]')).toBe(true);
    expect(mask.classList.contains('z-40')).toBe(true);
  });

  it('body 区域渲染 default slot 且含 p-5 overflow-y-auto', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true"><p class="body-content">内容</p></CModal>',
      },
      { attachTo: attach() },
    );
    const body = document.body.querySelector('.c-modal-body');
    expect(body).toBeTruthy();
    expect(body?.classList.contains('p-5')).toBe(true);
    expect(body?.classList.contains('overflow-y-auto')).toBe(true);
    expect(body?.querySelector('.body-content')).toBeTruthy();
  });

  it('footer slot 渲染且含正确 class', () => {
    mount(
      {
        components: { CModal },
        template:
          '<CModal :open="true"><template #footer><button>确定</button></template></CModal>',
      },
      { attachTo: attach() },
    );
    const footer = document.body.querySelector('.c-modal-footer');
    expect(footer).toBeTruthy();
    expect(footer?.classList.contains('px-5')).toBe(true);
    expect(footer?.classList.contains('py-4')).toBe(true);
    expect(footer?.classList.contains('flex')).toBe(true);
    expect(footer?.classList.contains('justify-end')).toBe(true);
    expect(footer?.classList.contains('gap-2')).toBe(true);
    expect(footer?.classList.contains('border-t')).toBe(true);
    expect(footer?.classList.contains('border-border')).toBe(true);
    expect(footer?.querySelector('button')).toBeTruthy();
  });

  it('无 footer slot 时不渲染 footer', () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true"><p>内容</p></CModal>',
      },
      { attachTo: attach() },
    );
    expect(document.body.querySelector('.c-modal-footer')).toBeFalsy();
  });

  it('组件卸载时移除 keydown 监听并解除 body 锁', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data() {
          return { open: true };
        },
        template: '<CModal v-model:open="open" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
    wrapper.unmount();
    expect(document.body.style.overflow).toBe('');
    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);
    await flushPromises();
  });

  it('提供 dialog 语义并用标题建立可访问名称', async () => {
    mount(
      {
        components: { CModal },
        template: '<CModal :open="true" title="安全设置" />',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    const panel = document.body.querySelector('.c-modal-panel') as HTMLElement;
    const title = document.body.querySelector('.c-modal-title') as HTMLElement;
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.getAttribute('aria-modal')).toBe('true');
    expect(panel.getAttribute('aria-labelledby')).toBe(title.id);
  });

  it('无标题时可通过 aria-label 命名', async () => {
    mount(CModal, { props: { open: true, ariaLabel: '编辑凭证' }, attachTo: attach() });
    await flushPromises();
    expect(document.body.querySelector('.c-modal-panel')?.getAttribute('aria-label')).toBe(
      '编辑凭证',
    );
  });

  it('closable=false 时遮罩和 Escape 均不能关闭', async () => {
    const wrapper = mount(
      {
        components: { CModal },
        data: () => ({ open: true }),
        template: '<CModal v-model:open="open" :closable="false" aria-label="处理中" />',
      },
      { attachTo: attach() },
    );
    (document.body.querySelector('.c-modal-mask') as HTMLElement).click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(true);
  });

  it('打开时聚焦并捕获 Tab，关闭后恢复原焦点', async () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const wrapper = mount(
      {
        components: { CModal },
        data: () => ({ open: true }),
        template:
          '<CModal v-model:open="open" title="焦点测试"><button class="first">一</button><button class="last">二</button></CModal>',
      },
      { attachTo: attach() },
    );
    await flushPromises();
    const panel = document.body.querySelector('.c-modal-panel') as HTMLElement;
    expect(panel.contains(document.activeElement)).toBe(true);
    const focusables = Array.from(panel.querySelectorAll<HTMLElement>('button'));
    focusables.at(-1)!.focus();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(document.activeElement).toBe(focusables[0]);
    focusables[0].focus();
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }),
    );
    expect(document.activeElement).toBe(focusables.at(-1));

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await flushPromises();
    expect((wrapper.vm as any).open).toBe(false);
    expect(document.activeElement).toBe(opener);
  });

  it('使背景 inert，并在嵌套浮层关闭时恢复原 overflow 与下层状态', async () => {
    document.body.style.overflow = 'clip';
    const wrapper = mount(
      {
        components: { CModal },
        data: () => ({ outer: true, inner: true }),
        template: `
          <div class="background">背景</div>
          <CModal v-model:open="outer" title="外层" />
          <CModal v-model:open="inner" title="内层" />
        `,
      },
      { attachTo: attach() },
    );
    await flushPromises();
    const masks = Array.from(document.body.querySelectorAll<HTMLElement>('.c-modal-mask'));
    const layerFor = (mask: HTMLElement) =>
      mask.parentElement?.tagName === 'TRANSITION-STUB' ? mask.parentElement : mask;
    const inertMask = masks.find((mask) => layerFor(mask)!.inert)!;
    const activeMask = masks.find((mask) => !layerFor(mask)!.inert)!;
    expect(document.body.style.overflow).toBe('hidden');
    expect(inertMask).toBeTruthy();
    expect(activeMask).toBeTruthy();

    if (activeMask.textContent?.includes('内层')) (wrapper.vm as any).inner = false;
    else (wrapper.vm as any).outer = false;
    await flushPromises();
    expect(document.body.style.overflow).toBe('hidden');
    const remainingMask = document.body.querySelector<HTMLElement>('.c-modal-mask')!;
    expect(layerFor(remainingMask)!.inert).toBe(false);
    (wrapper.vm as any).inner = false;
    (wrapper.vm as any).outer = false;
    await flushPromises();
    expect(document.body.style.overflow).toBe('clip');
  });

  it('支持从关闭状态动态打开并注册浮层', async () => {
    const wrapper = mount(CModal, {
      props: { open: false, title: '动态对话框' },
      attachTo: attach(),
    });
    await wrapper.setProps({ open: true });
    await flushPromises();
    expect(document.body.querySelector('.c-modal-panel')).toBeTruthy();
    expect(document.body.style.overflow).toBe('hidden');
  });
});
