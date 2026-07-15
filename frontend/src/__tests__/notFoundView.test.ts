import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import NotFoundView from '../views/NotFoundView.vue';

describe('NotFoundView', () => {
  it('说明路由不存在并提供返回总览入口', () => {
    const wrapper = mount(NotFoundView);
    expect(wrapper.text()).toContain('页面不存在');
    expect(wrapper.get('a').attributes('href')).toBe('#/dashboard');
  });
});
