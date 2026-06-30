import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import CInputGroup from '../components/ui/CInputGroup.vue';

const stylesCss = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf-8');
const inputGroupUtility = stylesCss.match(
  /@utility c-input-group\s*\{([\s\S]*?)\n\}\n\n\/\* 动画 \*\//,
)?.[1];

const Child = defineComponent({
  name: 'Child',
  setup(_, { slots }) {
    return () => h('div', { class: 'child' }, slots.default?.());
  },
});

describe('CInputGroup', () => {
  it('渲染容器 div 且含基础 class', () => {
    const wrapper = mount(CInputGroup);
    expect(wrapper.element.tagName).toBe('DIV');
    expect(wrapper.classes()).toContain('c-input-group');
  });

  it('不再把拼接样式硬编码到组件 class 中', () => {
    const wrapper = mount(CInputGroup);
    const cls = wrapper.attributes('class') ?? '';
    expect(cls).not.toContain('[&>*');
    expect(cls).not.toContain('sm:[&>*');
  });

  it('default slot 透传多个子元素', () => {
    const wrapper = mount(CInputGroup, {
      slots: {
        default: () => [h(Child, () => 'left'), h(Child, () => 'right')],
      },
    });
    expect(wrapper.findAll('.child')).toHaveLength(2);
    expect(wrapper.text()).toContain('left');
    expect(wrapper.text()).toContain('right');
  });

  it('聚焦子项临时提升层级，且不限制弹出层层叠', () => {
    expect(inputGroupUtility).toMatch(
      /& > \*:focus-within\s*\{[^}]*position:\s*relative;[^}]*z-index:\s*1;/s,
    );
    expect(inputGroupUtility).not.toContain('isolation: isolate');
  });
});
