import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const {
  sessionMock,
  routeMock,
  routerMock,
  queryClientMock,
  setUnauthorizedHandlerMock,
  themeMock,
} = vi.hoisted(() => ({
  sessionMock: {
    ready: false,
    authenticated: false,
    username: '',
    restore: vi.fn<() => Promise<void>>(),
    logout: vi.fn<() => Promise<void>>(),
  },
  routeMock: { name: 'dashboard' as string | undefined },
  routerMock: { push: vi.fn<(to: string | Record<string, unknown>) => Promise<void>>() },
  queryClientMock: { clear: vi.fn<() => void>() },
  setUnauthorizedHandlerMock: vi.fn<(handler: (() => void) | null) => void>(),
  themeMock: {
    mode: 'light' as 'light' | 'dark',
    init: vi.fn<() => void>(),
    toggle: vi.fn<() => void>(),
    set: vi.fn<(mode: 'light' | 'dark') => void>(),
  },
}));

vi.mock('../stores/session', () => ({
  useSessionStore: () => sessionMock,
}));

vi.mock('../stores/theme', () => ({
  THEME_ICON_SWAP_DELAY_MS: 143,
  useThemeStore: () => themeMock,
}));

vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => routerMock,
  RouterView: { template: '<div class="router-view" />' },
}));

vi.mock('@tanstack/vue-query', () => ({
  useQueryClient: () => queryClientMock,
}));

vi.mock('../api/client', () => ({
  setUnauthorizedHandler: setUnauthorizedHandlerMock,
}));

import App from '../App.vue';
const CButtonStub = defineComponent({
  name: 'CButton',
  inheritAttrs: false,
  props: {
    loading: Boolean,
    disabled: Boolean,
    block: Boolean,
    variant: String,
    size: String,
    shape: String,
  },
  emits: ['click'],
  setup(props, { attrs, emit, slots }) {
    return () =>
      h(
        'button',
        {
          ...attrs,
          disabled: props.disabled || props.loading,
          onClick: () => emit('click'),
        },
        [slots.icon?.(), slots.default?.()],
      );
  },
});
const CSpinStub = defineComponent({
  name: 'CSpin',
  props: { size: String, inherit: Boolean },
  setup() {
    return () => h('span', { class: 'c-spin-stub', 'aria-label': '加载中' });
  },
});
const CDrawerStub = defineComponent({
  name: 'CDrawer',
  props: { open: Boolean, placement: String, width: Number, title: String, closable: Boolean },
  emits: ['update:open'],
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'c-drawer-stub', 'data-open': String(props.open) }, slots.default?.());
  },
});

function mountApp(matches = true) {
  const addEventListener = vi.fn<MediaQueryList['addEventListener']>();
  const removeEventListener = vi.fn<MediaQueryList['removeEventListener']>();
  vi.mocked(window.matchMedia).mockReturnValue({
    matches,
    media: '(min-width: 48rem)',
    onchange: null,
    addEventListener,
    removeEventListener,
    addListener: vi.fn<MediaQueryList['addListener']>(),
    removeListener: vi.fn<MediaQueryList['removeListener']>(),
    dispatchEvent: vi.fn<MediaQueryList['dispatchEvent']>(),
  });

  const RouterViewStub = defineComponent({
    name: 'RouterView',
    setup(_, { slots }) {
      const Page = defineComponent({ template: '<div class="route-page" />' });
      return () => slots.default?.({ Component: Page });
    },
  });

  const wrapper = mount(App, {
    global: {
      stubs: {
        CButton: CButtonStub,
        CSpin: CSpinStub,
        CDrawer: CDrawerStub,
        CToastHost: true,
        LoginView: { template: '<div class="login-view">登录页</div>' },
        RouterView: RouterViewStub,
        Activity: true,
        ChartNoAxesCombined: true,
        KeyRound: true,
        LogOut: true,
        MenuIcon: true,
        Moon: true,
        PlugZap: true,
        Settings: true,
        ShieldCheck: true,
        Sun: true,
        TerminalSquare: true,
        BookOpen: true,
      },
    },
  });
  return { wrapper, addEventListener, removeEventListener };
}

describe('App', () => {
  beforeEach(() => {
    sessionMock.ready = false;
    sessionMock.authenticated = false;
    sessionMock.username = '';
    sessionMock.restore.mockReset();
    sessionMock.restore.mockResolvedValue(undefined);
    sessionMock.logout.mockReset();
    sessionMock.logout.mockResolvedValue(undefined);
    routeMock.name = 'dashboard';
    routerMock.push.mockReset();
    routerMock.push.mockResolvedValue(undefined);
    queryClientMock.clear.mockReset();
    setUnauthorizedHandlerMock.mockReset();
    themeMock.mode = 'light';
    themeMock.init.mockReset();
    themeMock.toggle.mockReset();
    themeMock.set.mockReset();
    vi.mocked(window.matchMedia).mockClear();
    localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('启动时注册 handler、媒体查询、恢复 session 并初始化主题', async () => {
    const { addEventListener } = mountApp();
    await vi.waitFor(() => expect(sessionMock.restore).toHaveBeenCalledOnce());

    expect(setUnauthorizedHandlerMock).toHaveBeenCalledWith(expect.any(Function));
    expect(window.matchMedia).toHaveBeenCalledWith('(min-width: 48rem)');
    expect(addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    expect(themeMock.init).toHaveBeenCalledOnce();
  });

  it('未就绪时显示 boot-screen 与加载指示器', async () => {
    const { wrapper } = mountApp();
    await vi.waitFor(() => expect(sessionMock.restore).toHaveBeenCalledOnce());

    expect(wrapper.find('.boot-screen').exists()).toBe(true);
    expect(wrapper.findComponent(CSpinStub).exists()).toBe(true);
  });

  it('未认证时显示登录页', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = false;
    const { wrapper } = mountApp();

    expect(wrapper.find('.login-view').exists()).toBe(true);
  });

  it('认证后显示桌面导航、用户名和当前标题', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    sessionMock.username = 'admin';
    routeMock.name = 'settings';
    const { wrapper } = mountApp();

    expect(wrapper.text()).toContain('admin');
    expect(wrapper.text()).toContain('设置');
    expect(wrapper.find('.sidebar').exists()).toBe(true);
    expect(wrapper.find('.hamburger').exists()).toBe(false);
    expect(wrapper.find('nav[aria-label="主导航"]').exists()).toBe(true);
  });

  it('侧边栏使用项目图标', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    expect(wrapper.get('img.project-icon').attributes('src')).toBe('/assets/codebuddy2api.svg');
  });

  it('console 菜单与页面标题使用中文 API 测试', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    routeMock.name = 'console';
    const { wrapper } = mountApp();

    expect(wrapper.find('nav[aria-label="主导航"]').text()).toContain('API 测试');
    expect(wrapper.find('.topbar').text()).toContain('API 测试');
    expect(wrapper.text()).not.toContain('Console');
  });

  it('开发文档菜单与页面标题使用中文名称', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    routeMock.name = 'api-docs';
    const { wrapper } = mountApp();

    expect(wrapper.find('nav[aria-label="主导航"]').text()).toContain('开发文档');
    expect(wrapper.find('.topbar').text()).toContain('开发文档');
  });

  it('统计菜单与页面标题使用中文名称', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    routeMock.name = 'stats';
    const { wrapper } = mountApp();

    expect(wrapper.find('nav[aria-label="主导航"]').text()).toContain('统计');
    expect(wrapper.find('.topbar').text()).toContain('统计');
  });

  it('桌面侧边栏使用主题化导轨样式', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    const sidebar = wrapper.find('.sidebar');
    expect(sidebar.classes()).toContain('bg-rail');
    expect(sidebar.classes()).toContain('border-rail-border');

    const brandText = sidebar.find('.font-display');
    expect(brandText.classes()).toContain('text-rail-text-strong');

    const username = sidebar.find('.font-display + div');
    expect(username.classes()).toContain('text-rail-muted');

    const navButton = wrapper.find('nav[aria-label="主导航"] button');
    expect(navButton.classes()).toContain('text-rail-active-text');
  });

  it('认证后布局允许内容撑高父容器，使 sticky 侧栏不受首屏高度限制', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    const shell = wrapper.find('.sidebar').element.parentElement;
    expect(shell?.classList.contains('min-h-screen')).toBe(true);
    expect(shell?.classList.contains('h-screen')).toBe(false);
    expect(wrapper.find('.sidebar').classes()).toEqual(
      expect.arrayContaining(['sticky', 'h-screen']),
    );
  });

  it('顶栏使用玻璃毛玻璃效果', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    const topbar = wrapper.find('.topbar');
    expect(topbar.exists()).toBe(true);
    expect(topbar.classes().some((c) => c.includes('backdrop-blur'))).toBe(true);
    expect(topbar.classes()).toContain('shrink-0');
  });

  it('页面切换期间禁用主题按钮，完成后恢复', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();
    const state = (wrapper.vm.$ as any).setupState;
    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'))!;

    expect(wrapper.find('.page-transition-frame').exists()).toBe(true);
    expect(wrapper.find('transition-stub[name="page"]').attributes('mode')).toBe('out-in');

    state.startPageTransition();
    await wrapper.vm.$nextTick();
    expect(toggleButton.attributes('disabled')).toBeDefined();
    await toggleButton.trigger('click');
    state.toggleTheme();
    expect(themeMock.toggle).not.toHaveBeenCalled();

    state.finishPageTransition();
    await wrapper.vm.$nextTick();
    expect(toggleButton.attributes('disabled')).toBeUndefined();
    await toggleButton.trigger('click');
    expect(themeMock.toggle).toHaveBeenCalledOnce();
  });

  it('移动端显示菜单并执行移动导航', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp(false);
    const state = (wrapper.vm.$ as any).setupState;

    await vi.waitFor(() => expect(state.isMobile).toBe(true));
    expect(wrapper.find('.sidebar').exists()).toBe(false);
    expect(wrapper.find('.hamburger').exists()).toBe(true);

    await wrapper.get('.hamburger').trigger('click');
    expect(state.mobileNavOpen).toBe(true);

    const drawer = wrapper.findComponent(CDrawerStub);
    expect(drawer.props('open')).toBe(true);

    drawer.vm.$emit('update:open', false);
    await wrapper.vm.$nextTick();
    expect(state.mobileNavOpen).toBe(false);

    state.mobileNavOpen = true;
    const credentialButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('凭证'));
    await credentialButton?.trigger('click');

    expect(state.mobileNavOpen).toBe(false);
    expect(routerMock.push).toHaveBeenCalledWith({ name: 'credentials' });
  });

  it('桌面导航点击跳转且未知路由回退为总览标题', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    routeMock.name = 'unknown';
    const { wrapper } = mountApp();

    expect(wrapper.text()).toContain('总览');
    const credentialButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('凭证'));
    await credentialButton?.trigger('click');
    expect(routerMock.push).toHaveBeenCalledWith({ name: 'credentials' });
  });

  it('缺少路由名时使用 dashboard 作为活动路由', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    routeMock.name = undefined;
    const { wrapper } = mountApp();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.activeRoute).toBe('dashboard');
    expect(wrapper.text()).toContain('总览');
  });

  it('切换主题时调用 theme.toggle()', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'));
    await toggleButton?.trigger('click');

    expect(themeMock.toggle).toHaveBeenCalledOnce();
  });

  it('dark 模式下主题切换按钮 aria-label 为切换到亮色模式', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    themeMock.mode = 'dark';
    const { wrapper } = mountApp();

    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'));
    expect(toggleButton?.attributes('aria-label')).toBe('切换到亮色模式');
  });

  it('从亮色切到暗色时，图标在最低对比点延迟切换', async () => {
    vi.useFakeTimers();
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    themeMock.mode = 'light';
    themeMock.toggle.mockImplementation(() => {
      themeMock.mode = 'dark';
    });
    const { wrapper } = mountApp();

    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'));

    expect(toggleButton?.find('.lucide-moon').exists()).toBe(true);
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(false);

    await toggleButton?.trigger('click');
    wrapper.vm.$forceUpdate();
    await wrapper.vm.$nextTick();
    expect(themeMock.toggle).toHaveBeenCalledOnce();
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(true);
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(false);

    vi.advanceTimersByTime(142);
    await wrapper.vm.$nextTick();
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(true);

    vi.advanceTimersByTime(1);
    await wrapper.vm.$nextTick();
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(false);
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(true);
  });

  it('从暗色切回亮色时，图标在最低对比点延迟切换', async () => {
    vi.useFakeTimers();
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    themeMock.mode = 'dark';
    themeMock.toggle.mockImplementation(() => {
      themeMock.mode = 'light';
    });
    const { wrapper } = mountApp();

    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'));

    expect(toggleButton?.find('.lucide-sun').exists()).toBe(true);
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(false);

    await toggleButton?.trigger('click');
    wrapper.vm.$forceUpdate();
    await wrapper.vm.$nextTick();
    expect(themeMock.toggle).toHaveBeenCalledOnce();
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(true);
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(false);

    vi.advanceTimersByTime(142);
    await wrapper.vm.$nextTick();
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(true);

    vi.advanceTimersByTime(1);
    await wrapper.vm.$nextTick();
    expect(toggleButton?.find('.lucide-sun').exists()).toBe(false);
    expect(toggleButton?.find('.lucide-moon').exists()).toBe(true);
  });

  it('主题颜色过渡中允许再次切换，图标按最后一次点击重新计时', async () => {
    vi.useFakeTimers();
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    themeMock.mode = 'light';
    themeMock.toggle.mockImplementation(() => {
      themeMock.mode = themeMock.mode === 'light' ? 'dark' : 'light';
    });
    const { wrapper } = mountApp();
    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label')?.includes('切换'))!;

    await toggleButton.trigger('click');
    wrapper.vm.$forceUpdate();
    vi.advanceTimersByTime(260);
    await wrapper.vm.$nextTick();
    expect(toggleButton.attributes('disabled')).toBeUndefined();
    expect(toggleButton.find('.lucide-sun').exists()).toBe(true);

    await toggleButton.trigger('click');
    wrapper.vm.$forceUpdate();
    await wrapper.vm.$nextTick();
    expect(themeMock.toggle).toHaveBeenCalledTimes(2);
    expect(toggleButton.find('.lucide-sun').exists()).toBe(true);

    vi.advanceTimersByTime(142);
    await wrapper.vm.$nextTick();
    expect(toggleButton.find('.lucide-sun').exists()).toBe(true);

    vi.advanceTimersByTime(1);
    await wrapper.vm.$nextTick();
    expect(toggleButton.find('.lucide-moon').exists()).toBe(true);
  });

  it('右上角退出按钮使用默认按钮尺寸', () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();

    const logoutButton = wrapper
      .findAllComponents(CButtonStub)
      .find((button) => button.attributes('aria-label') === '退出登录');

    expect(logoutButton?.props('size')).toBeUndefined();
  });

  it('全局 401 handler 登出并清理查询缓存', async () => {
    mountApp();
    await vi.waitFor(() => expect(setUnauthorizedHandlerMock).toHaveBeenCalled());
    const handler = setUnauthorizedHandlerMock.mock.calls[0][0];

    expect(handler).not.toBeNull();
    handler!();
    expect(sessionMock.logout).toHaveBeenCalledOnce();
    expect(queryClientMock.clear).toHaveBeenCalledOnce();
  });

  it('主动退出后清缓存并返回根路径', async () => {
    sessionMock.ready = true;
    sessionMock.authenticated = true;
    const { wrapper } = mountApp();
    const state = (wrapper.vm.$ as any).setupState;

    await state.logout();
    expect(sessionMock.logout).toHaveBeenCalledOnce();
    expect(queryClientMock.clear).toHaveBeenCalledOnce();
    expect(routerMock.push).toHaveBeenCalledWith('/');
  });

  it('卸载时移除媒体监听并清除未授权 handler', () => {
    const { wrapper, removeEventListener } = mountApp();
    wrapper.unmount();

    expect(removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    expect(setUnauthorizedHandlerMock).toHaveBeenLastCalledWith(null);
  });
});
