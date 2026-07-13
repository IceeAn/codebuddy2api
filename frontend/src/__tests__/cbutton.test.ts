import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CButton from '../components/ui/CButton.vue';

describe('CButton', () => {
  it('默认渲染 button，variant=secondary size=md', () => {
    const wrapper = mount(CButton);
    expect(wrapper.element.tagName).toBe('BUTTON');
    expect(wrapper.classes()).toContain('bg-surface');
    expect(wrapper.classes()).toContain('text-text');
    expect(wrapper.classes()).toContain('border');
    expect(wrapper.classes()).toContain('border-border');
    expect(wrapper.classes()).toContain('h-[38px]');
    expect(wrapper.classes()).toContain('px-4');
    expect(wrapper.classes()).toContain('text-sm');
  });

  it('variant=primary 使用随主题进度插值的固定语义色', () => {
    const wrapper = mount(CButton, { props: { variant: 'primary' } });
    expect(wrapper.classes()).toContain('bg-primary-action');
    expect(wrapper.classes()).toContain('!text-white');
    expect(wrapper.classes()).toContain('hover:bg-primary-action-hover');
    expect(wrapper.classes().some((className) => className.startsWith('dark:'))).toBe(false);
  });

  it('variant=secondary 含 secondary class', () => {
    const wrapper = mount(CButton, { props: { variant: 'secondary' } });
    expect(wrapper.classes()).toContain('bg-surface');
    expect(wrapper.classes()).toContain('hover:bg-surface-2');
    expect(wrapper.classes()).toContain('hover:border-border-strong');
  });

  it('variant=ghost 含 ghost class', () => {
    const wrapper = mount(CButton, { props: { variant: 'ghost' } });
    expect(wrapper.classes()).toContain('bg-transparent');
    expect(wrapper.classes()).toContain('text-muted');
    expect(wrapper.classes()).toContain('hover:bg-surface-2');
    expect(wrapper.classes()).toContain('hover:text-text');
  });

  it('variant=danger 含 danger class', () => {
    const wrapper = mount(CButton, { props: { variant: 'danger' } });
    expect(wrapper.classes()).toContain('bg-error-600');
    expect(wrapper.classes()).toContain('!text-white');
    expect(wrapper.classes()).toContain('hover:bg-error-500');
  });

  it('size=sm 含 sm 尺寸 class', () => {
    const wrapper = mount(CButton, { props: { size: 'sm' } });
    expect(wrapper.classes()).toContain('h-8');
    expect(wrapper.classes()).toContain('px-3');
    expect(wrapper.classes()).toContain('text-[13px]');
    expect(wrapper.classes()).toContain('gap-1.5');
  });

  it('size=lg 含 lg 尺寸 class', () => {
    const wrapper = mount(CButton, { props: { size: 'lg' } });
    expect(wrapper.classes()).toContain('h-11');
    expect(wrapper.classes()).toContain('px-5');
    expect(wrapper.classes()).toContain('text-[15px]');
  });

  it('shape=circle 时 rounded-full 且 p-0 且宽等于高', () => {
    const wrapper = mount(CButton, {
      props: { shape: 'circle', size: 'md' },
    });
    expect(wrapper.classes()).toContain('rounded-full');
    expect(wrapper.classes()).toContain('p-0');
    expect(wrapper.classes()).toContain('w-10');
    expect(wrapper.classes()).toContain('h-10');
  });

  it('shape=circle size=sm 时使用更大的触控面积', () => {
    const wrapper = mount(CButton, {
      props: { shape: 'circle', size: 'sm' },
    });
    expect(wrapper.classes()).toContain('w-9');
    expect(wrapper.classes()).toContain('h-9');
  });

  it('block=true 时 w-full', () => {
    const wrapper = mount(CButton, { props: { block: true } });
    expect(wrapper.classes()).toContain('w-full');
  });

  it('loading=true 时渲染 CSpin 且 button disabled', () => {
    const wrapper = mount(CButton, { props: { loading: true } });
    expect(wrapper.attributes('disabled')).toBeDefined();
    const spin = wrapper.find('span.animate-spin');
    expect(spin.exists()).toBe(true);
    expect(wrapper.classes()).not.toContain('pointer-events-none');
    expect(wrapper.classes()).toContain('opacity-80');
    // 不应给 CSpin 注入冗余 border-current：CSpin 内部已是 border-current/25 + border-t-current，
    // 外部 border-current（100% 不透明）会覆盖 25% 透明边，把旋转环变成实心圆环
    expect(spin.classes()).not.toContain('border-current');
  });

  it('disabled=true 时 button disabled 属性为 true', () => {
    const wrapper = mount(CButton, { props: { disabled: true } });
    expect(wrapper.attributes('disabled')).toBeDefined();
  });

  it('click 事件触发（非 disabled/loading 时）', async () => {
    const wrapper = mount(CButton);
    await wrapper.trigger('click');
    expect(wrapper.emitted('click')).toBeTruthy();
  });

  it('disabled 时不触发 click', async () => {
    const wrapper = mount(CButton, { props: { disabled: true } });
    await wrapper.trigger('click');
    expect(wrapper.emitted('click')).toBeFalsy();
  });

  it('loading 时不触发 click', async () => {
    const wrapper = mount(CButton, { props: { loading: true } });
    await wrapper.trigger('click');
    expect(wrapper.emitted('click')).toBeFalsy();
  });

  it('icon slot 渲染在 default slot 前', () => {
    const wrapper = mount(CButton, {
      slots: {
        icon: '<i class="test-icon" />',
        default: '点击',
      },
    });
    const html = wrapper.html();
    expect(html).toContain('test-icon');
    expect(html).toContain('点击');
    expect(html.indexOf('test-icon')).toBeLessThan(html.indexOf('点击'));
  });

  it('default slot 渲染内容', () => {
    const wrapper = mount(CButton, { slots: { default: '提交' } });
    expect(wrapper.text()).toContain('提交');
  });

  it('含通用 class（inline-flex items-center justify-center rounded-md transition）', () => {
    const wrapper = mount(CButton);
    expect(wrapper.classes()).toContain('inline-flex');
    expect(wrapper.classes()).toContain('items-center');
    expect(wrapper.classes()).toContain('justify-center');
    expect(wrapper.classes()).toContain('whitespace-nowrap');
    expect(wrapper.classes()).toContain('shrink-0');
    expect(wrapper.classes()).toContain('font-medium');
    expect(wrapper.classes()).toContain('rounded-md');
    expect(wrapper.classes()).toContain('transition-[background-color,box-shadow,transform]');
  });

  it('焦点外发光由全局规则统一提供', () => {
    const wrapper = mount(CButton);
    expect(wrapper.classes()).not.toContain('focus-visible:outline-2');
    expect(wrapper.classes()).not.toContain('focus-visible:outline-brand-500');
    expect(wrapper.classes()).not.toContain('focus-visible:outline-offset-2');
  });

  it('通用 class 不含 disabled:opacity-50（用 ghost 变体隔离验证，ghost 自身是 opacity-40）', () => {
    // 用 ghost 变体 mount：ghost 变体含 disabled:opacity-40 而非 disabled:opacity-50，
    // 若通用 class 仍残留 disabled:opacity-50，此处会捕获。
    const wrapper = mount(CButton, { props: { variant: 'ghost' } });
    expect(wrapper.classes()).not.toContain('disabled:cursor-not-allowed');
    expect(wrapper.classes()).toContain('disabled:active:scale-100');
    expect(wrapper.classes()).not.toContain('disabled:opacity-50');
  });

  it('variant=primary disabled 态含 disabled:bg-brand-600/50 与 disabled:text-white/60', () => {
    const wrapper = mount(CButton, { props: { variant: 'primary' } });
    expect(wrapper.classes()).toContain('disabled:bg-brand-600/50');
    expect(wrapper.classes()).toContain('disabled:!text-white/60');
    expect(wrapper.classes()).toContain('disabled:hover:bg-brand-600/50');
    expect(wrapper.classes()).toContain('disabled:hover:shadow-none');
  });

  it('variant=secondary disabled 态含 disabled:opacity-50', () => {
    const wrapper = mount(CButton, { props: { variant: 'secondary' } });
    expect(wrapper.classes()).toContain('disabled:opacity-50');
  });

  it('variant=ghost disabled 态含 disabled:opacity-40', () => {
    const wrapper = mount(CButton, { props: { variant: 'ghost' } });
    expect(wrapper.classes()).toContain('disabled:opacity-40');
  });

  it('variant=danger disabled 态含 disabled:opacity-50', () => {
    const wrapper = mount(CButton, { props: { variant: 'danger' } });
    expect(wrapper.classes()).toContain('disabled:opacity-50');
  });
});
