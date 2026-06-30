import { defineComponent, markRaw, ref } from 'vue';
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import RefreshButton from '../components/RefreshButton.vue';
import StatTile from '../components/StatTile.vue';
import CButton from '../components/ui/CButton.vue';
import { useToastStore } from '../stores/toast';

describe('RefreshButton', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('显示默认文案、loading 时不触发 refetch', async () => {
    const refetch = vi.fn<() => Promise<unknown>>();
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(true), refetch },
      },
      global: {
        stubs: {
          RefreshCw: true,
        },
      },
    });

    expect(wrapper.text()).toContain('刷新');
    const button = wrapper.get('button');
    expect(button.attributes('disabled')).toBeDefined();
    await button.trigger('click');
    await (wrapper.vm.$ as any).setupState.handleRefresh();
    expect(refetch).not.toHaveBeenCalled();
  });

  it('手动刷新立即处理业务结果，但 loading 至少保持 300ms 并阻止重复刷新', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn<() => Promise<unknown>>().mockResolvedValue({ isError: false });
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(false), refetch },
      },
      global: {
        stubs: {
          RefreshCw: true,
        },
      },
    });

    await wrapper.get('button').trigger('click');
    expect(refetch).toHaveBeenCalledOnce();
    expect(wrapper.get('button').attributes('disabled')).toBeDefined();
    expect(wrapper.find('.animate-spin').exists()).toBe(true);

    await (wrapper.vm.$ as any).setupState.handleRefresh();
    expect(refetch).toHaveBeenCalledOnce();

    await flushPromises();
    expect(useToastStore().toasts[0].message).toBe('已刷新');
    expect(wrapper.emitted('success')).toEqual([[{ isError: false }]]);
    expect(wrapper.get('button').attributes('disabled')).toBeDefined();

    await vi.advanceTimersByTimeAsync(300);
    await wrapper.vm.$nextTick();
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined();
  });

  it('refetch 返回错误状态时不显示成功提示', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn<() => Promise<unknown>>().mockResolvedValue({ isError: true });
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(false), refetch },
      },
      global: {
        stubs: {
          RefreshCw: true,
        },
      },
    });

    await wrapper.get('button').trigger('click');
    await vi.advanceTimersByTimeAsync(300);

    expect(refetch).toHaveBeenCalledOnce();
    expect(useToastStore().toasts).toHaveLength(0);
    expect(wrapper.emitted('success')).toBeUndefined();
  });

  it('离线时立即提示且不排队，恢复联网不会自动发送刷新请求', async () => {
    const online = vi.spyOn(window.navigator, 'onLine', 'get').mockReturnValue(false);
    const refetch = vi.fn<() => Promise<unknown>>().mockResolvedValue({ isError: false });
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(false), refetch },
      },
      global: { stubs: { RefreshCw: true } },
    });

    await wrapper.get('button').trigger('click');
    await wrapper.get('button').trigger('click');

    expect(refetch).not.toHaveBeenCalled();
    expect(useToastStore().toasts.map((toast) => toast.message)).toEqual([
      '当前处于离线状态，请联网后重试',
      '当前处于离线状态，请联网后重试',
    ]);

    online.mockReturnValue(true);
    window.dispatchEvent(new Event('online'));
    await wrapper.vm.$nextTick();
    expect(refetch).not.toHaveBeenCalled();
    expect(wrapper.emitted('success')).toBeUndefined();
  });

  it('支持自定义文案', () => {
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(false), refetch: vi.fn<() => Promise<unknown>>() },
        label: '重新加载',
      },
      global: { stubs: { RefreshCw: true } },
    });

    expect(wrapper.text()).toContain('重新加载');
  });

  it('向底层按钮传递尺寸和样式', () => {
    const wrapper = mount(RefreshButton, {
      props: {
        query: { isFetching: ref(false), refetch: vi.fn<() => Promise<unknown>>() },
        size: 'sm',
        variant: 'danger',
      },
      global: { stubs: { RefreshCw: true } },
    });

    expect(wrapper.findComponent(CButton).props()).toMatchObject({
      size: 'sm',
      variant: 'danger',
    });
  });

  it('支持自定义刷新成功提示', async () => {
    vi.useFakeTimers();
    const wrapper = mount(RefreshButton, {
      props: {
        query: {
          isFetching: ref(false),
          refetch: vi.fn<() => Promise<unknown>>().mockResolvedValue(undefined),
        },
        successMessage: '列表已更新',
      },
      global: { stubs: { RefreshCw: true } },
    });

    await wrapper.get('button').trigger('click');
    await vi.advanceTimersByTimeAsync(300);

    expect(useToastStore().toasts[0].message).toBe('列表已更新');
  });
});

describe('StatTile', () => {
  const Icon = markRaw(defineComponent({ template: '<i class="test-icon" />' }));

  it('渲染值、标签、图标与 meta', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '服务状态',
        value: 12,
        tone: 'success',
        icon: Icon,
        meta: 'healthy',
      },
    });

    expect(wrapper.text()).toContain('12');
    expect(wrapper.text()).toContain('服务状态');
    expect(wrapper.text()).toContain('healthy');
    expect(wrapper.find('.test-icon').exists()).toBe(true);
  });

  it('hover 过渡使用较慢速度', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '服务状态',
        value: 12,
        tone: 'success',
        icon: Icon,
      },
    });

    expect(wrapper.find('section').classes()).toContain('duration-200');
    expect(wrapper.find('section').classes()).toContain('transition-[translate,box-shadow]');
  });

  it('tone=brand 时图标盒含 brand 相关 class', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '有效凭证',
        value: '2/3',
        tone: 'brand',
        icon: Icon,
      },
    });

    const iconBox = wrapper.find('.bg-brand-500\\/15');
    expect(iconBox.exists()).toBe(true);
  });

  it('meta 为空时不渲染附加信息', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '调用',
        value: '0',
        tone: 'warning',
        icon: Icon,
      },
    });

    expect(wrapper.text()).not.toContain('按当前进程内统计');
  });

  it('支持为较长数值传入专用布局 class', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '服务时间',
        value: '1234天 03:04:05',
        tone: 'success',
        icon: Icon,
        valueClass: 'break-words',
      },
    });

    expect(wrapper.find('.font-display').classes()).toContain('break-words');
  });

  it('corner slot 渲染在右上角，并为主体内容预留空间', () => {
    const wrapper = mount(StatTile, {
      props: {
        label: '有效凭证',
        value: '2/3',
        tone: 'brand',
        icon: Icon,
      },
      slots: {
        corner: '<span class="corner">66%</span>',
      },
    });

    expect(wrapper.find('section').classes()).toContain('relative');
    expect(wrapper.find('.corner').exists()).toBe(true);
    expect(wrapper.find('.absolute.right-4.top-4').exists()).toBe(true);
    expect(wrapper.find('.min-w-0.pr-14').exists()).toBe(true);
  });
});
