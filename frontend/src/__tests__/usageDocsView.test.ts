import { nextTick, reactive } from 'vue';
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import UsageDocsView from '../views/UsageDocsView.vue';

const state = vi.hoisted(() => ({ push: vi.fn<(path: string) => Promise<unknown>>() }));
const route = reactive<{ query: Record<string, unknown>; hash: string }>({ query: {}, hash: '' });
vi.mock('vue-router', () => ({ useRoute: () => route }));
vi.mock('../utils/chunkLoadRecovery', () => ({ chunkLoadRecovery: { push: state.push } }));
vi.mock('virtual:usage-docs', () => ({
  default: [
    {
      id: 'README.md',
      title: '项目',
      html: '<h1 id="项目">项目</h1><h2 id="开始">开始</h2><p>欢迎</p><a href="/#/usage-docs?doc=doc%2F指南.md#章节"><em>指南</em></a><a href="https://example.com">外链</a><a download="示例.json" href="/assets/example.json">附件</a>',
      headings: [{ id: '开始', text: '开始', level: 2 }],
      anchors: ['项目', '开始'],
    },
    {
      id: 'doc/指南.md',
      title: '指南',
      html: '<h1 id="指南">指南</h1><h2 id="章节">章节</h2><h3 id="细节">细节</h3>',
      headings: [
        { id: '章节', text: '章节', level: 2 },
        { id: '细节', text: '细节', level: 3 },
      ],
      anchors: ['指南', '章节', '细节'],
    },
    { id: 'doc/简述.md', title: '简述', html: '<h1>简述</h1>', headings: [], anchors: [] },
  ],
}));

beforeEach(() => {
  route.query = {};
  route.hash = '';
  state.push.mockReset();
  Element.prototype.scrollIntoView = vi.fn<Element['scrollIntoView']>();
});

describe('使用文档页', () => {
  it('默认展示 README、文档导航和章节目录，页面只有一个根节点', async () => {
    const wrapper = mount(UsageDocsView);
    await flushPromises();
    expect(wrapper.get('article').text()).toContain('欢迎');
    expect(wrapper.findAll('nav[aria-label="文档导航"] a')).toHaveLength(3);
    expect(wrapper.get('nav[aria-label="章节目录"]').text()).toContain('开始');
    expect(wrapper.element.children.length).toBeGreaterThan(0);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    await wrapper.get('button[aria-controls="usage-navigation"]').trigger('click');
    expect(
      wrapper.get('button[aria-controls="usage-navigation"]').attributes('aria-expanded'),
    ).toBe('true');
    await wrapper.get('nav[aria-label="文档导航"] a[href*="doc%2F"]').trigger('click');
    expect(state.push).toHaveBeenCalledWith('/usage-docs?doc=doc%2F%E6%8C%87%E5%8D%97.md');
    wrapper.unmount();
  });

  it('响应文档和章节变化，保留组件并定位正文，支持目录导航', async () => {
    const wrapper = mount(UsageDocsView);
    route.query = { doc: 'doc/指南.md' };
    route.hash = '#细节';
    await flushPromises();
    expect(wrapper.get('article').text()).toContain('章节');
    expect(wrapper.get('nav[aria-label="章节目录"] a[aria-current="location"]').text()).toBe(
      '细节',
    );
    await wrapper.get('nav[aria-label="章节目录"] a').trigger('click');
    expect(state.push).toHaveBeenCalledWith(
      '/usage-docs?doc=doc%2F%E6%8C%87%E5%8D%97.md#%E7%AB%A0%E8%8A%82',
    );
    route.query = { doc: 'doc/简述.md' };
    route.hash = '';
    await nextTick();
    expect(wrapper.find('nav[aria-label="章节目录"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('重复点击当前章节仍会定位正文', async () => {
    route.hash = '#开始';
    const wrapper = mount(UsageDocsView);
    await flushPromises();
    vi.mocked(Element.prototype.scrollIntoView).mockClear();
    await wrapper.get('nav[aria-label="章节目录"] a').trigger('click');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(state.push).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('正文站内链接通过恢复器导航，外链、附件及修饰键点击保持浏览器行为', async () => {
    const wrapper = mount(UsageDocsView);
    // 测试中阻止 jsdom 执行真实导航，不改变页面处理器接收到的事件。
    wrapper.element.addEventListener('click', (event: Event) => event.preventDefault());
    await wrapper.get('article em').trigger('click');
    expect(state.push).toHaveBeenCalledWith('/usage-docs?doc=doc%2F指南.md#章节');
    state.push.mockClear();
    for (const modifiers of [
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
      { button: 1 },
    ]) {
      await wrapper.get('article em').trigger('click', modifiers);
    }
    await wrapper.get('article p').trigger('click');
    wrapper
      .get('article p')
      .element.firstChild!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const cancelled = new MouseEvent('click', { bubbles: true, cancelable: true });
    cancelled.preventDefault();
    wrapper.get('article em').element.dispatchEvent(cancelled);
    await wrapper.get('article a[href^="https"]').trigger('click');
    await wrapper.get('article a[download]').trigger('click');
    expect(state.push).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it.each(['missing.md', ['README.md', 'doc/指南.md'], null])(
    '未知或无效文档参数明确报错：%s',
    async (doc) => {
      route.query = { doc };
      const wrapper = mount(UsageDocsView);
      await flushPromises();
      expect(wrapper.text()).toContain('文档不存在');
      expect(wrapper.find('article').exists()).toBe(false);
      await wrapper.get('button').trigger('click');
      expect(state.push).toHaveBeenCalledWith('/usage-docs');
      wrapper.unmount();
    },
  );

  it('未知章节显示提示并保留文档内容', async () => {
    route.hash = '#不存在';
    const wrapper = mount(UsageDocsView);
    await flushPromises();
    expect(wrapper.text()).toContain('未找到指定章节');
    expect(wrapper.get('article').text()).toContain('欢迎');
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
