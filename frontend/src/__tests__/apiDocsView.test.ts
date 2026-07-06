import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import ApiDocsView from '../views/ApiDocsView.vue';

const CardStub = defineComponent({
  name: 'CCard',
  props: { title: String },
  setup(props, { slots }) {
    return () =>
      h('section', [
        h('header', [h('span', props.title), slots['header-extra']?.()]),
        slots.default?.(),
      ]);
  },
});

describe('ApiDocsView', () => {
  it('展示 Swagger 文档说明并在正文下方提供跳转入口', () => {
    const wrapper = mount(ApiDocsView, {
      global: {
        stubs: {
          CCard: CardStub,
          BookOpen: true,
          ExternalLink: true,
        },
      },
    });

    expect(wrapper.get('header').text()).toBe('Swagger API 文档');
    expect(wrapper.find('header a').exists()).toBe(false);

    const link = wrapper.get('section > div > a');
    expect(link.text()).toContain('打开 Swagger 文档');
    expect(link.attributes('href')).toBe('/docs');
    expect(link.attributes('target')).toBe('_blank');
    expect(link.attributes('rel')).toBe('noopener noreferrer');
    expect(wrapper.text()).toContain('管理台登录会话');
  });
});
