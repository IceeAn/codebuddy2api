import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import { markRaw } from 'vue';
import { Inbox, PackageOpen } from '@lucide/vue';
import CEmptyState from '../components/ui/CEmptyState.vue';

describe('CEmptyState', () => {
  it('默认渲染（无 props）', () => {
    const wrapper = mount(CEmptyState);
    expect(wrapper.element.tagName).toBe('DIV');
    expect(wrapper.classes()).toContain('py-12');
    expect(wrapper.classes()).toContain('flex');
    expect(wrapper.classes()).toContain('flex-col');
    expect(wrapper.classes()).toContain('items-center');
    expect(wrapper.classes()).toContain('gap-2');
    expect(wrapper.classes()).toContain('text-center');
  });

  it('默认图标为 Inbox', () => {
    const wrapper = mount(CEmptyState);
    const icon = wrapper.find('.c-empty-icon');
    expect(icon.exists()).toBe(true);
    expect(icon.find('svg').exists()).toBe(true);
  });

  it('icon prop 自定义图标', () => {
    const wrapper = mount(CEmptyState, {
      props: { icon: markRaw(PackageOpen) },
    });
    const icon = wrapper.find('.c-empty-icon');
    expect(icon.exists()).toBe(true);
  });

  it('icon slot 覆盖 icon prop', () => {
    const wrapper = mount(CEmptyState, {
      props: { icon: markRaw(Inbox) },
      slots: { icon: '<i class="custom-icon" />' },
    });
    expect(wrapper.find('.custom-icon').exists()).toBe(true);
  });

  it('icon slot 存在时不渲染默认 icon 容器', () => {
    const wrapper = mount(CEmptyState, {
      slots: { icon: '<i class="custom-icon" />' },
    });
    expect(wrapper.find('.c-empty-icon').exists()).toBe(false);
  });

  it('图标含 text-muted/50 class', () => {
    const wrapper = mount(CEmptyState);
    const icon = wrapper.find('.c-empty-icon');
    expect(icon.classes()).toContain('text-muted/50');
  });

  it('图标尺寸 40px', () => {
    const wrapper = mount(CEmptyState);
    const icon = wrapper.find('.c-empty-icon');
    expect(icon.classes()).toContain('w-10');
    expect(icon.classes()).toContain('h-10');
  });

  it('title 渲染标题', () => {
    const wrapper = mount(CEmptyState, {
      props: { title: '暂无数据' },
    });
    const title = wrapper.find('.c-empty-title');
    expect(title.exists()).toBe(true);
    expect(title.text()).toBe('暂无数据');
    expect(title.classes()).toContain('mt-1');
    expect(title.classes()).toContain('text-[15px]');
    expect(title.classes()).toContain('font-semibold');
    expect(title.classes()).toContain('text-text');
  });

  it('无 title 时不渲染标题', () => {
    const wrapper = mount(CEmptyState);
    expect(wrapper.find('.c-empty-title').exists()).toBe(false);
  });

  it('description 渲染描述', () => {
    const wrapper = mount(CEmptyState, {
      props: { description: '请稍后再试' },
    });
    const desc = wrapper.find('.c-empty-desc');
    expect(desc.exists()).toBe(true);
    expect(desc.text()).toBe('请稍后再试');
    expect(desc.classes()).toContain('text-[13px]');
    expect(desc.classes()).toContain('text-muted');
  });

  it('无 description 时不渲染描述', () => {
    const wrapper = mount(CEmptyState);
    expect(wrapper.find('.c-empty-desc').exists()).toBe(false);
  });

  it('default slot 渲染操作区', () => {
    const wrapper = mount(CEmptyState, {
      slots: { default: '<button class="action">刷新</button>' },
    });
    const action = wrapper.find('.c-empty-action');
    expect(action.exists()).toBe(true);
    expect(action.classes()).toContain('mt-3');
    expect(action.find('button.action').exists()).toBe(true);
  });

  it('无 default slot 时不渲染操作区', () => {
    const wrapper = mount(CEmptyState);
    expect(wrapper.find('.c-empty-action').exists()).toBe(false);
  });
});
