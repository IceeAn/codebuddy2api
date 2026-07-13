import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CAlert from '../components/ui/CAlert.vue';

describe('CAlert', () => {
  it('默认渲染（type=info, showIcon=true）', () => {
    const wrapper = mount(CAlert);
    expect(wrapper.element.tagName).toBe('DIV');
    expect(wrapper.attributes('role')).toBe('status');
    expect(wrapper.find('svg').exists()).toBe(true);
    expect(wrapper.classes()).toContain('flex');
    expect(wrapper.classes()).toContain('gap-3');
    expect(wrapper.classes()).toContain('p-3.5');
    expect(wrapper.classes()).toContain('rounded-lg');
    expect(wrapper.classes()).toContain('border-l-4');
  });

  it('type=info 含 info 配色', () => {
    const wrapper = mount(CAlert, { props: { type: 'info' } });
    expect(wrapper.classes()).toContain('border-l-brand-500');
    expect(wrapper.classes()).toContain('bg-brand-500/[0.07]');
  });

  it('type=success 含 success 配色', () => {
    const wrapper = mount(CAlert, { props: { type: 'success' } });
    expect(wrapper.attributes('role')).toBe('status');
    expect(wrapper.classes()).toContain('border-l-success-500');
    expect(wrapper.classes()).toContain('bg-success-500/[0.08]');
  });

  it('type=warning 含 warning 配色与 role=alert', () => {
    const wrapper = mount(CAlert, { props: { type: 'warning' } });
    expect(wrapper.attributes('role')).toBe('alert');
    expect(wrapper.classes()).toContain('border-l-warning-500');
    expect(wrapper.classes()).toContain('bg-warning-500/[0.10]');
  });

  it('type=error 含 error 配色与 role=alert', () => {
    const wrapper = mount(CAlert, { props: { type: 'error' } });
    expect(wrapper.attributes('role')).toBe('alert');
    expect(wrapper.classes()).toContain('border-l-error-500');
    expect(wrapper.classes()).toContain('bg-error-500/[0.08]');
  });

  it('showIcon=false 时不渲染图标', () => {
    const wrapper = mount(CAlert, { props: { showIcon: false } });
    expect(wrapper.find('svg').exists()).toBe(false);
  });

  it('title 渲染标题', () => {
    const wrapper = mount(CAlert, { props: { title: '提示标题' } });
    const title = wrapper.find('.c-alert-title');
    expect(title.exists()).toBe(true);
    expect(title.text()).toBe('提示标题');
    expect(title.classes()).toContain('font-display');
    expect(title.classes()).toContain('font-semibold');
    expect(title.classes()).toContain('text-sm');
    expect(title.classes()).toContain('text-text-strong');
  });

  it('无 title 时不渲染标题节点', () => {
    const wrapper = mount(CAlert, { slots: { default: '内容' } });
    expect(wrapper.find('.c-alert-title').exists()).toBe(false);
  });

  it('default slot 渲染内容', () => {
    const wrapper = mount(CAlert, { slots: { default: '提示内容' } });
    const body = wrapper.find('.c-alert-content');
    expect(body.exists()).toBe(true);
    expect(body.text()).toBe('提示内容');
    expect(body.classes()).toContain('text-sm');
    expect(body.classes()).toContain('text-text');
  });

  it('action slot 渲染在右侧', () => {
    const wrapper = mount(CAlert, {
      slots: { action: '<button class="retry">重试</button>' },
    });
    const action = wrapper.find('.c-alert-action');
    expect(action.exists()).toBe(true);
    expect(action.find('button.retry').exists()).toBe(true);
  });

  it('closable=false 时不渲染关闭按钮', () => {
    const wrapper = mount(CAlert, { props: { closable: false } });
    expect(wrapper.find('.c-alert-close').exists()).toBe(false);
  });

  it('closable=true 渲染关闭按钮，点击后 emit close 并隐藏', async () => {
    const wrapper = mount(CAlert, { props: { closable: true } });
    const closeBtn = wrapper.find('.c-alert-close');
    expect(closeBtn.exists()).toBe(true);
    await closeBtn.trigger('click');
    expect(wrapper.emitted('close')).toBeTruthy();
    expect(wrapper.find('.c-alert-close').exists()).toBe(false);
  });

  it('图标左上对齐（items-start）', () => {
    const wrapper = mount(CAlert);
    expect(wrapper.classes()).toContain('items-start');
  });

  it('info 图标使用品牌语义色', () => {
    const wrapper = mount(CAlert, { props: { type: 'info' } });
    const icon = wrapper.find('.c-alert-icon');
    expect(icon.exists()).toBe(true);
    expect(icon.classes()).toContain('text-tone-brand');
  });

  it('success 图标使用成功语义色', () => {
    const wrapper = mount(CAlert, { props: { type: 'success' } });
    const icon = wrapper.find('.c-alert-icon');
    expect(icon.classes()).toContain('text-tone-success');
  });

  it('warning 图标使用警告语义色', () => {
    const wrapper = mount(CAlert, { props: { type: 'warning' } });
    const icon = wrapper.find('.c-alert-icon');
    expect(icon.classes()).toContain('text-tone-warning');
  });

  it('error 图标使用错误语义色', () => {
    const wrapper = mount(CAlert, { props: { type: 'error' } });
    const icon = wrapper.find('.c-alert-icon');
    expect(icon.classes()).toContain('text-tone-error');
  });
});
