import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { clearMock, confirmDiscardMock, logoutMock } = vi.hoisted(() => ({
  clearMock: vi.fn<() => void>(),
  confirmDiscardMock: vi.fn<() => boolean>(() => true),
  logoutMock: vi.fn<() => Promise<void>>(),
}));

vi.mock('../stores/session', () => ({
  useSessionStore: () => ({ username: 'alice', logout: logoutMock }),
}));
vi.mock('@tanstack/vue-query', () => ({
  useQueryClient: () => ({ clear: clearMock }),
}));

import ForcedPasswordChangeView from '../views/ForcedPasswordChangeView.vue';

const PasswordFormStub = defineComponent({
  name: 'PasswordChangeForm',
  props: { forced: Boolean },
  setup(props, { expose }) {
    expose({ confirmDiscard: confirmDiscardMock });
    return () => h('div', { 'data-forced': String(props.forced) }, '密码表单');
  },
});

function mountView() {
  return mount(ForcedPasswordChangeView, {
    global: {
      stubs: {
        PasswordChangeForm: PasswordFormStub,
        LogOut: true,
      },
    },
  });
}

describe('ForcedPasswordChangeView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    confirmDiscardMock.mockReturnValue(true);
    logoutMock.mockResolvedValue(undefined);
  });

  it('使用独占页面、强制表单和固定标题', () => {
    const wrapper = mountView();
    expect(wrapper.find('[data-forced="true"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('首次登录后必须修改密码');
    expect(wrapper.text()).toContain('服务启动后 1 小时');
    expect(document.title).toBe('修改密码 · CodeBuddy2API');
  });

  it('确认后退出并清空管理缓存，取消时保持会话', async () => {
    const wrapper = mountView();
    await wrapper.get('button').trigger('click');
    expect(logoutMock).toHaveBeenCalledOnce();
    expect(clearMock).toHaveBeenCalledOnce();

    confirmDiscardMock.mockReturnValue(false);
    await wrapper.get('button').trigger('click');
    expect(logoutMock).toHaveBeenCalledOnce();
  });
});
