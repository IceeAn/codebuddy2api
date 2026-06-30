import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import CCard from '../components/ui/CCard.vue';

describe('CCard', () => {
  it('默认渲染（size=default）', () => {
    const wrapper = mount(CCard);
    expect(wrapper.element.tagName).toBe('DIV');
    expect(wrapper.classes()).toContain('p-5');
    expect(wrapper.classes()).toContain('rounded-xl');
    expect(wrapper.classes()).toContain('border');
    expect(wrapper.classes()).toContain('border-border');
    expect(wrapper.classes()).toContain('min-w-0');
    expect(wrapper.classes()).toContain('h-full');
    expect(wrapper.classes()).toContain('flex');
    expect(wrapper.classes()).toContain('flex-col');
  });

  it('size=small 含 small 样式', () => {
    const wrapper = mount(CCard, { props: { size: 'small' } });
    expect(wrapper.classes()).toContain('p-4');
    expect(wrapper.classes()).toContain('rounded-lg');
  });

  it('shadow-card class 存在', () => {
    const wrapper = mount(CCard);
    expect(wrapper.classes()).toContain('shadow-[var(--shadow-card)]');
  });

  it('interactive=true 含 hover 上浮效果', () => {
    const wrapper = mount(CCard, { props: { interactive: true } });
    expect(wrapper.classes()).toContain('hover:-translate-y-0.5');
    expect(wrapper.classes()).toContain('hover:shadow-[var(--shadow-card-lg)]');
    expect(wrapper.classes()).toContain('transition-[transform,box-shadow]');
  });

  it('interactive=false 默认无 hover 效果', () => {
    const wrapper = mount(CCard);
    expect(wrapper.classes()).not.toContain('hover:-translate-y-0.5');
  });

  it('title prop 渲染 header 与标题', () => {
    const wrapper = mount(CCard, { props: { title: '卡片标题' } });
    const header = wrapper.find('.c-card-header');
    expect(header.exists()).toBe(true);
    const title = header.find('.c-card-title');
    expect(title.exists()).toBe(true);
    expect(title.text()).toBe('卡片标题');
    expect(title.classes()).toContain('font-display');
    expect(title.classes()).toContain('font-semibold');
    expect(title.classes()).toContain('text-md');
    expect(title.classes()).toContain('text-text-strong');
  });

  it('无 title 且无 header/header-extra slot 时不渲染 header', () => {
    const wrapper = mount(CCard, { slots: { default: '内容' } });
    expect(wrapper.find('.c-card-header').exists()).toBe(false);
  });

  it('header slot 覆盖 title prop', () => {
    const wrapper = mount(CCard, {
      props: { title: 'prop标题' },
      slots: { header: '<div class="custom-header">自定义</div>' },
    });
    const header = wrapper.find('.c-card-header');
    expect(header.exists()).toBe(true);
    expect(header.find('.custom-header').exists()).toBe(true);
    expect(header.find('.c-card-title').exists()).toBe(false);
  });

  it('header-extra slot 右对齐渲染', () => {
    const wrapper = mount(CCard, {
      slots: { 'header-extra': '<button class="extra">操作</button>' },
    });
    const extra = wrapper.find('.c-card-header-extra');
    expect(extra.exists()).toBe(true);
    expect(extra.classes()).toContain('ml-auto');
    expect(extra.find('button.extra').exists()).toBe(true);
  });

  it('header-extra 单独存在时也渲染 header', () => {
    const wrapper = mount(CCard, {
      slots: { 'header-extra': '<span>extra</span>' },
    });
    expect(wrapper.find('.c-card-header').exists()).toBe(true);
  });

  it('default slot 渲染 body', () => {
    const wrapper = mount(CCard, { slots: { default: '<p class="body">内容</p>' } });
    const body = wrapper.find('.c-card-body');
    expect(body.exists()).toBe(true);
    expect(body.find('p.body').exists()).toBe(true);
    expect(body.classes()).toContain('flex-1');
  });

  it('footer slot 渲染且带 border-t', () => {
    const wrapper = mount(CCard, {
      slots: { footer: '<div class="foot">底部</div>' },
    });
    const footer = wrapper.find('.c-card-footer');
    expect(footer.exists()).toBe(true);
    expect(footer.classes()).toContain('pt-4');
    expect(footer.classes()).toContain('border-t');
    expect(footer.classes()).toContain('border-border');
    expect(footer.find('div.foot').exists()).toBe(true);
  });

  it('无 footer slot 时不渲染 footer', () => {
    const wrapper = mount(CCard, { slots: { default: '内容' } });
    expect(wrapper.find('.c-card-footer').exists()).toBe(false);
  });

  it('header 不再叠加额外 padding', () => {
    const wrapper = mount(CCard, {
      props: { size: 'small', title: '标题' },
    });
    const header = wrapper.find('.c-card-header');
    expect(header.classes()).not.toContain('px-4');
    expect(header.classes()).not.toContain('pt-4');
  });

  it('有 header 时 body 带顶部间距', () => {
    const wrapper = mount(CCard, {
      props: { size: 'default', title: '标题' },
    });
    const body = wrapper.find('.c-card-body');
    expect(body.classes()).toContain('pt-4');
  });

  it('header 含 border-b border-border', () => {
    const wrapper = mount(CCard, { props: { title: '标题' } });
    const header = wrapper.find('.c-card-header');
    expect(header.classes()).toContain('border-b');
    expect(header.classes()).toContain('border-border');
  });
});
