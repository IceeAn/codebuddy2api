import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const {
  credentialsQuery,
  queryOptions,
  mutationOptions,
  mutationStates,
  invalidateQueries,
  setQueryData,
  toastMock,
  copyMock,
  oauth,
  validateMock,
  restoreValidationMock,
} = vi.hoisted(() => {
  const refValue = <T>(value: T) => ({ __v_isRef: true as const, value });
  return {
    credentialsQuery: {
      data: refValue<unknown>(undefined),
      error: refValue<unknown>(undefined),
      isError: refValue(false),
      isLoading: refValue(false),
      isFetching: refValue(false),
      refetch: vi.fn<() => Promise<unknown>>(),
    },
    queryOptions: [] as Array<Record<string, any>>,
    mutationOptions: [] as Array<Record<string, (...args: any[]) => any>>,
    mutationStates: [] as Array<{
      isPending: { __v_isRef: true; value: boolean };
      mutate: Mock<(variables?: unknown) => void>;
    }>,
    invalidateQueries: vi.fn<(filters?: unknown) => Promise<void>>(),
    setQueryData: vi.fn<(queryKey: readonly unknown[], updater: unknown) => unknown>(),
    toastMock: {
      success: vi.fn<(message: string, duration?: number) => void>(),
      error: vi.fn<(message: string, duration?: number) => void>(),
      warning: vi.fn<(message: string, duration?: number) => void>(),
      info: vi.fn<(message: string, duration?: number) => void>(),
    },
    copyMock: vi.fn<(text: string, successMessage?: string) => Promise<boolean>>(),
    oauth: {
      authUrl: refValue(''),
      starting: refValue(false),
      polling: refValue(false),
      elapsedSeconds: refValue(0),
      manualOpenRequired: refValue(false),
      start: vi.fn<() => Promise<void>>(),
      stop: vi.fn<() => void>(),
      cancel: vi.fn<() => Promise<void>>(),
      openAuthUrl: vi.fn<() => void>(),
    },
    validateMock: vi.fn<() => Promise<void>>(),
    restoreValidationMock: vi.fn<() => void>(),
  };
});

vi.mock('@tanstack/vue-query', () => ({
  useQuery: (options: { queryKey: string[] }) => {
    queryOptions.push(options);
    return credentialsQuery;
  },
  useQueryClient: () => ({ invalidateQueries, setQueryData }),
  useMutation: (options: Record<string, (...args: any[]) => any>) => {
    mutationOptions.push(options);
    const state = {
      isPending: { __v_isRef: true as const, value: false },
      mutate: vi.fn<(variables?: unknown) => void>(),
    };
    mutationStates.push(state);
    return state;
  },
}));

vi.mock('../composables/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../composables/useClipboard', () => ({
  useClipboard: () => ({ copy: copyMock }),
}));

vi.mock('../composables/useOAuthPolling', () => ({
  useOAuthPolling: (options: { onSuccess: () => void }) => {
    (oauth as any).onSuccess = options.onSuccess;
    return oauth;
  },
}));

import CredentialsView from '../views/CredentialsView.vue';
import { adminApi } from '../api/admin';
import CredentialActions from '../components/CredentialActions.vue';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import { RefreshButtonStub } from './refreshButtonStub';

/**
 * CForm stub：expose validate / restoreValidation 供视图通过 formRef 调用。
 * validate 默认 resolve（校验通过），由测试用例按需 mockRejectedValue 模拟失败。
 */
const FormStub = defineComponent({
  name: 'CForm',
  setup(_, { expose, slots }) {
    expose({ validate: validateMock, restoreValidation: restoreValidationMock });
    return () => h('form', null, slots.default?.());
  },
});

function mountView() {
  queryOptions.length = 0;
  mutationOptions.length = 0;
  mutationStates.length = 0;
  return mount(CredentialsView, {
    global: {
      stubs: {
        RefreshButton: RefreshButtonStub,
        CAlert: {
          inheritAttrs: false,
          template: '<div class="c-alert"><slot /></div>',
        },
        CCard: {
          props: ['title', 'size'],
          template:
            '<section class="c-card"><div v-if="title" class="c-card-title font-display font-semibold text-md text-text-strong">{{ title }}</div><slot name="header" /><slot name="header-extra" /><slot /></section>',
        },
        CDataTable: {
          props: ['columns', 'data', 'loading', 'error', 'bordered', 'size', 'rowKey'],
          template:
            '<div class="c-data-table" :data-error="String(error)" :data-loading="String(loading)" :data-row-key="rowKey"><slot name="empty" /></div>',
        },
        CForm: FormStub,
        CFormItem: {
          props: ['label', 'path', 'required'],
          template:
            '<div class="c-form-item"><label v-if="label">{{ label }}</label><slot /></div>',
        },
        CTag: {
          props: ['type', 'dot'],
          template: '<span class="c-tag"><slot /></span>',
        },
        CredentialAccountSwitcher: {
          name: 'CredentialAccountSwitcher',
          props: ['open', 'credentialId'],
          emits: ['close', 'switching'],
          template: '<div class="credential-account-switcher-stub" />',
        },
        MousePointerClick: true,
        Copy: true,
        ExternalLink: true,
        Pause: true,
        Play: true,
        Plus: true,
        RotateCcw: true,
        Trash2: true,
      },
    },
  });
}

describe('CredentialsView', () => {
  beforeEach(() => {
    credentialsQuery.data.value = undefined;
    credentialsQuery.error.value = undefined;
    credentialsQuery.isError.value = false;
    credentialsQuery.isLoading.value = false;
    credentialsQuery.isFetching.value = false;
    invalidateQueries.mockReset();
    invalidateQueries.mockResolvedValue(undefined);
    setQueryData.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    copyMock.mockReset();
    oauth.authUrl.value = '';
    oauth.starting.value = false;
    oauth.polling.value = false;
    oauth.elapsedSeconds.value = 0;
    oauth.manualOpenRequired.value = false;
    oauth.start.mockReset();
    oauth.stop.mockReset();
    oauth.cancel.mockReset();
    oauth.openAuthUrl.mockReset();
    validateMock.mockReset();
    restoreValidationMock.mockReset();
    validateMock.mockResolvedValue(undefined);
    vi.spyOn(window, 'open').mockImplementation(() => null);
  });

  it('计算排序、当前状态和错误消息', () => {
    credentialsQuery.data.value = {
      credentials: [
        { credential_id: 'expired', is_expired: true },
        { credential_id: 'valid', is_expired: false },
      ],
      current: { credential_id: 'valid', status: 'auto_rotation' },
    };
    credentialsQuery.error.value = new Error('load failed');

    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.rows.map((item: any) => item.credential_id)).toEqual(['valid', 'expired']);
    expect(state.currentId).toBe('valid');
    expect(state.loadError).toEqual(new Error('load failed'));
    expect(state.errorMessage).toBe('load failed');
    expect(state.formatElapsed(65)).toBe('01:05');
    expect(queryOptions.map((option) => option.queryKey)).toEqual([
      ['admin', 'test-user', 'credentials'],
    ]);
  });

  it('筛选 tab 切换过滤 rows，计数从原始数据计算', async () => {
    credentialsQuery.data.value = {
      credentials: [
        { credential_id: 'expired1', is_expired: true },
        { credential_id: 'valid1', is_expired: false },
        { credential_id: 'expired2', is_expired: true },
        { credential_id: 'valid2', is_expired: false },
      ],
      current: { credential_id: 'valid1' },
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.filterTab).toBe('all');
    expect(state.rows.map((i: any) => i.credential_id)).toEqual([
      'valid1',
      'valid2',
      'expired1',
      'expired2',
    ]);
    expect(state.credentialCounts).toEqual({ all: 4, valid: 2, expired: 2 });

    state.filterTab = 'valid';
    await wrapper.vm.$nextTick();
    expect(state.rows.map((i: any) => i.credential_id)).toEqual(['valid1', 'valid2']);
    expect(state.credentialFilterTransition).toBe('credential-slide-left');
    expect(state.credentialCounts).toEqual({ all: 4, valid: 2, expired: 2 });

    state.filterTab = 'expired';
    await wrapper.vm.$nextTick();
    expect(state.rows.map((i: any) => i.credential_id)).toEqual(['expired1', 'expired2']);
    expect(state.credentialFilterTransition).toBe('credential-slide-left');
    expect(wrapper.find('.credential-table-viewport').exists()).toBe(true);
    expect(wrapper.find('transition-stub[name="credential-slide-left"]').attributes('mode')).toBe(
      'out-in',
    );

    state.filterTab = 'valid';
    await wrapper.vm.$nextTick();
    expect(state.credentialFilterTransition).toBe('credential-slide-right');
    expect(state.credentialCounts).toEqual({ all: 4, valid: 2, expired: 2 });
  });

  it('按筛选 tab 生成空状态文案，仅总数为 0 时提示添加入口', async () => {
    credentialsQuery.data.value = {
      credentials: [{ credential_id: 'valid1', is_expired: false }],
      current: { credential_id: 'valid1' },
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    state.filterTab = 'expired';
    await wrapper.vm.$nextTick();
    expect(state.rows).toEqual([]);
    expect(state.emptyDescription).toBe('暂无过期凭证');
    expect(wrapper.text()).toContain('暂无过期凭证');
    expect(wrapper.text()).not.toContain('暂无过期凭证，点击上方"开始认证"或手动添加');

    credentialsQuery.data.value = {
      credentials: [{ credential_id: 'expired1', is_expired: true }],
      current: { credential_id: 'expired1' },
    };
    const validWrapper = mountView();
    const validState = (validWrapper.vm.$ as any).setupState;
    validState.filterTab = 'valid';
    await validWrapper.vm.$nextTick();
    expect(validState.rows).toEqual([]);
    expect(validState.emptyDescription).toBe('暂无可用凭证');

    credentialsQuery.data.value = {
      credentials: [],
      current: {},
    };
    const allWrapper = mountView();
    const allState = (allWrapper.vm.$ as any).setupState;
    expect(allState.emptyDescription).toBe('暂无凭证，点击上方"开始认证"或手动添加');
  });

  it('缺少数据时使用默认值和未知错误', () => {
    credentialsQuery.error.value = 'bad';
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.rows).toEqual([]);
    expect(state.currentId).toBe('');
    expect(state.errorMessage).toBe('未知错误');
  });

  it('存在凭证时不注册模型查询', () => {
    credentialsQuery.data.value = {
      credentials: [{ credential_id: 'valid', is_expired: false }],
      current: {},
    };
    mountView();
    expect(queryOptions.map((option) => option.queryKey)).toEqual([
      ['admin', 'test-user', 'credentials'],
    ]);
  });

  it('凭证池标题使用卡片标题样式', () => {
    const wrapper = mountView();
    const title = wrapper.findAll('.c-card-title').find((node) => node.text() === '凭证池');

    expect(title?.classes()).toEqual(
      expect.arrayContaining(['font-display', 'font-semibold', 'text-md', 'text-text-strong']),
    );
  });

  it('表格接收查询错误状态，避免错误时显示空状态', () => {
    credentialsQuery.isError.value = true;
    const wrapper = mountView();

    expect(wrapper.find('.c-data-table').attributes('data-error')).toBe('true');
    expect(wrapper.find('.c-data-table').attributes('data-row-key')).toBe('credential_id');
  });

  it('后台刷新时表格进入加载状态', () => {
    credentialsQuery.isFetching.value = true;
    const wrapper = mountView();

    expect(wrapper.find('.c-data-table').attributes('data-loading')).toBe('true');
  });

  it('手动添加表单字段容器不额外增加纵向 gap', () => {
    const wrapper = mountView();
    const fields = wrapper.find('.credential-manual-form-fields');

    expect(fields.exists()).toBe(true);
    expect(fields.classes()).toContain('gap-0');
    expect(fields.classes()).not.toContain('gap-4');
  });

  it('认证卡片初始态仅显示说明与开始按钮，且手动添加不再要求用户 ID', () => {
    credentialsQuery.data.value = {
      credentials: [],
      current: { status: 'auto_rotation', auto_rotation_enabled: true },
    };
    const wrapper = mountView();
    const text = wrapper.text();

    expect(text).toContain('CodeBuddy 登录认证');
    expect(text).toContain('开始认证');
    expect(wrapper.find('.credential-auth-card').exists()).toBe(true);
    expect(wrapper.find('.credential-auth-card-content').classes()).toEqual(
      expect.arrayContaining(['flex', 'flex-col', 'gap-3']),
    );
    expect(wrapper.find('.credential-auth-card-content').classes()).not.toContain('lg:flex-1');
    expect(wrapper.find('.credential-auth-idle').classes()).toEqual(
      expect.arrayContaining(['flex', 'flex-col', 'gap-3']),
    );
    expect(wrapper.find('.credential-auth-idle').classes()).not.toContain('lg:flex-1');
    const startButton = wrapper.findAll('button').find((node) => node.text().includes('开始认证'));
    expect(startButton?.classes()).toContain('credential-auth-start-button');
    expect(startButton?.classes()).not.toContain('mt-auto');
    expect(startButton?.classes()).not.toContain('lg:mt-auto');
    expect(startButton?.classes()).not.toContain('self-start');
    expect(text).not.toContain('用户 ID（可选）');
    expect(text).not.toContain('不填写时将从 Token 自动识别。');
    expect(text).not.toContain('auto_rotation');
    expect(text).not.toContain('等待授权');
    expect(text).not.toContain('打开登录页');
    expect(text).not.toContain('复制链接');
    expect(text).not.toContain('取消认证');
  });

  it('手动添加校验 token 并触发创建', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.credentialRules.bearerToken.whitespace).toBe(true);
    validateMock.mockRejectedValueOnce(new Error('invalid'));
    await state.submitCredential();
    expect(validateMock).toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalledWith('请输入 Bearer Token');
    expect(mutationStates[0].mutate).not.toHaveBeenCalled();

    state.credentialForm.bearerToken = ' token ';
    await state.submitCredential();
    expect(mutationStates[0].mutate).toHaveBeenCalledOnce();
  });

  it('认证卡片等待态不展示完整 URL，并提供打开、复制和取消操作', async () => {
    oauth.polling.value = true;
    oauth.elapsedSeconds.value = 65;
    oauth.authUrl.value = 'https://auth.example/private-state';
    const wrapper = mountView();
    const text = wrapper.text();

    expect(text).toContain('等待登录认证');
    expect(text).toContain('已等待 01:05');
    expect(text).toContain('打开登录页');
    expect(text).toContain('复制链接');
    expect(text).toContain('取消认证');
    expect(text).not.toContain('https://auth.example/private-state');

    const buttons = wrapper.findAll('button');
    await buttons.find((button) => button.text().includes('打开登录页'))?.trigger('click');
    await buttons.find((button) => button.text().includes('复制链接'))?.trigger('click');
    await buttons.find((button) => button.text().includes('取消认证'))?.trigger('click');

    expect(oauth.openAuthUrl).toHaveBeenCalledOnce();
    expect(copyMock).toHaveBeenCalledWith('https://auth.example/private-state', '认证链接已复制');
    expect(oauth.cancel).toHaveBeenCalledOnce();
  });

  it('弹窗被拦截时显示手动打开提示，取消后等待元素全部消失', async () => {
    oauth.polling.value = true;
    oauth.authUrl.value = 'https://auth';
    oauth.manualOpenRequired.value = true;
    const wrapper = mountView();

    expect(wrapper.text()).toContain('登录页未能自动打开');

    wrapper.unmount();
    oauth.polling.value = false;
    oauth.authUrl.value = '';
    oauth.manualOpenRequired.value = false;
    const resetWrapper = mountView();
    expect(resetWrapper.text()).not.toContain('等待登录认证');
    expect(resetWrapper.text()).not.toContain('打开登录页');
    expect(resetWrapper.text()).not.toContain('复制链接');
    expect(resetWrapper.text()).not.toContain('取消认证');
  });

  it('各 mutation 成功路径更新状态并刷新查询', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.credentialForm.bearerToken = 'token';

    await mutationOptions[0].onSuccess();
    expect(state.credentialForm.bearerToken).toBe('');
    expect(restoreValidationMock).toHaveBeenCalled();
    expect(toastMock.success).toHaveBeenCalledWith('凭证已添加');

    await mutationOptions[1].onSuccess({ auto_rotation_disabled_by_select: true, current: {} });
    expect(toastMock.success).toHaveBeenCalledWith('已切换凭证，自动轮换已关闭');

    await mutationOptions[1].onSuccess({ auto_rotation_disabled_by_select: false, current: {} });
    expect(toastMock.success).toHaveBeenCalledWith('已切换凭证');

    credentialsQuery.data.value = {
      credentials: [
        { credential_id: 'gone', is_expired: false },
        { credential_id: 'keep', is_expired: false },
      ],
      current: { credential_id: 'gone', status: 'auto_rotation_disabled' },
    };
    const deletedCurrent = { credential_id: 'keep', status: 'auto_rotation' };
    await mutationOptions[2].onSuccess({ deleted: true, current: deletedCurrent }, 'gone');
    expect(toastMock.success).toHaveBeenCalledWith('凭证已删除');
    expect(setQueryData).toHaveBeenCalledWith(
      ['admin', 'test-user', 'credentials'],
      expect.any(Function),
    );
    const updater = setQueryData.mock.calls.at(-1)![1] as (old: unknown) => unknown;
    const next = updater(credentialsQuery.data.value);
    expect((next as any).credentials).toEqual([{ credential_id: 'keep', is_expired: false }]);
    expect((next as any).current).toBe(deletedCurrent);

    // updater 在 old 为空时直接返回 old（缓存尚未加载的场景）
    expect(updater(undefined)).toBeUndefined();

    await mutationOptions[4].onSuccess({ auto_rotation_enabled: true, current: {} });
    expect(toastMock.success).toHaveBeenCalledWith('自动轮换已启用');

    await mutationOptions[4].onSuccess({ auto_rotation_enabled: false, current: {} });
    expect(toastMock.success).toHaveBeenCalledWith('自动轮换已暂停');

    await (oauth as any).onSuccess();

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['admin', 'test-user', 'credentials'],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['admin', 'test-user', 'status'],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['admin', 'test-user', 'settings'],
    });
  });

  it('mutationFn 读取当前输入并由后端决定测试模型', async () => {
    const createSpy = vi.spyOn(adminApi, 'createCredential').mockResolvedValue({} as never);
    const testSpy = vi.spyOn(adminApi, 'testCredential').mockResolvedValue({} as never);
    const deleteSpy = vi.spyOn(adminApi, 'deleteCredential').mockResolvedValue({} as never);
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    state.credentialForm.bearerToken = 'token';

    await mutationOptions[0].mutationFn();
    await mutationOptions[3].mutationFn('cred');
    await mutationOptions[2].mutationFn('gone');
    expect(createSpy).toHaveBeenCalledWith('token');
    expect(testSpy).toHaveBeenCalledWith('cred');
    expect(deleteSpy).toHaveBeenCalledWith('gone');
  });

  it('测试 mutation 用 ID 集合维护并发行 loading 并提示结果', () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    mutationOptions[3].onMutate('cred-a');
    mutationOptions[3].onMutate('cred-b');
    expect([...state.testingIds]).toEqual(['cred-a', 'cred-b']);
    mutationOptions[3].onSuccess({ ok: true, status_code: 200, model_source: 'actual' });
    expect(toastMock.success).toHaveBeenCalledWith('凭证可用');
    mutationOptions[3].onSuccess({
      ok: true,
      status_code: 200,
      model_source: 'configured_fallback',
    });
    expect(toastMock.warning).toHaveBeenCalledWith('凭证可用（使用本地配置模型回退）');
    mutationOptions[3].onSuccess({ ok: false, status_code: 500, detail: '上游拒绝请求' });
    expect(toastMock.error).toHaveBeenCalledWith('测试失败：上游拒绝请求');
    mutationOptions[3].onSuccess({ ok: false, status_code: 502 });
    expect(toastMock.error).toHaveBeenCalledWith('测试失败：HTTP 502');
    mutationOptions[3].onSettled(undefined, undefined, 'cred-a');
    expect([...state.testingIds]).toEqual(['cred-b']);
    mutationOptions[3].onSettled(undefined, undefined, 'cred-b');
    expect([...state.testingIds]).toEqual([]);
  });

  it('列渲染覆盖当前、过期、可用和操作按钮', async () => {
    credentialsQuery.data.value = {
      credentials: [],
      current: { credential_id: 'active' },
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const active = { credential_id: 'active', is_expired: false, email: 'a@example.com' };
    const activeExpired = { credential_id: 'active', is_expired: true };
    const expired = { credential_id: 'expired', is_expired: true, user_id: 'user' };
    const valid = { credential_id: 'valid', is_expired: false };

    const activeTag = state.columns[0].render(active);
    const activeExpiredTag = state.columns[0].render(activeExpired);
    const expiredTag = state.columns[0].render(expired);
    const validTag = state.columns[0].render(valid);
    expect((activeTag.children as any).default()).toBe('当前');
    expect((activeExpiredTag.children as any).default()).toBe('当前 · 已过期');
    expect(activeExpiredTag.props?.type).toBe('error');
    expect((expiredTag.children as any).default()).toBe('过期');
    expect((validTag.children as any).default()).toBe('可用');
    expect(
      state.columns[1].render({
        ...active,
        nickname: '昵称',
        preferred_username: '偏好用户名',
      }),
    ).toBe('昵称');
    expect(
      state.columns[1].render({
        ...active,
        preferred_username: '偏好用户名',
      }),
    ).toBe('偏好用户名');
    expect(state.columns[1].render(active)).toBe('a@example.com');
    expect(state.columns[1].render(expired)).toBe('user');
    expect(state.columns[1].render(valid)).toBe('-');
    expect(
      state.columns[1].render({
        ...active,
        nickname: '昵称',
        enterprise_name: '测试企业',
      }),
    ).toBe('昵称 · 测试企业');
    expect(state.columns[5].title).toBe('操作');
    expect(state.columns[5].align).toBe('left');
    expect(state.columns[5].headerClassName).toBe('table-action-header');

    const actions = state.columns[5].render(valid);
    expect(actions.type).toBe(CredentialActions);
    expect(actions.props?.credential).toBe(valid);
    expect(actions.props?.isCurrent).toBe(false);
    expect(actions.props?.isTesting).toBe(false);
    expect(actions.props?.isSelecting).toBe(false);
    expect(actions.props?.isDeleting).toBe(false);
    expect(actions.props?.writeInProgress).toBe(false);
    expect(actions.props?.hasActiveTests).toBe(false);
    expect(actions.props?.canSwitchAccount).toBe(false);
    actions.props?.onSelect('valid');
    actions.props?.onTest('valid');
    actions.props?.onDelete('valid');
    expect(mutationStates[1].mutate).toHaveBeenCalledWith('valid');
    expect(mutationStates[3].mutate).toHaveBeenCalledWith('valid');
    expect(mutationStates[2].mutate).toHaveBeenCalledWith('valid');

    const activeActions = state.columns[5].render(active);
    expect(activeActions.type).toBe(CredentialActions);
    expect(activeActions.props?.isCurrent).toBe(true);
    expect(activeActions.props?.autoRotationEnabled).toBe(false);

    credentialsQuery.data.value = {
      credentials: [],
      current: { credential_id: 'active', auto_rotation_enabled: true },
    };
    const enabledWrapper = mountView();
    const enabledState = (enabledWrapper.vm.$ as any).setupState;
    expect(enabledState.columns[5].render(active).props?.autoRotationEnabled).toBe(true);

    const switchableActions = enabledState.columns[5].render({
      ...valid,
      has_refresh_token: true,
      account_count: 2,
    });
    expect(switchableActions.props?.canSwitchAccount).toBe(true);
    switchableActions.props?.onSwitchAccount('valid');
    expect(enabledState.accountSwitcherCredentialId).toBe('valid');
    enabledState.accountSwitching = true;
    enabledState.closeAccountSwitcher();
    expect(enabledState.accountSwitcherCredentialId).toBe('valid');
    enabledState.accountSwitching = false;
    enabledState.closeAccountSwitcher();
    expect(enabledState.accountSwitcherCredentialId).toBe('');
    wrapper.findComponent({ name: 'CredentialAccountSwitcher' }).vm.$emit('switching', true);
    await wrapper.vm.$nextTick();
    expect(state.accountSwitching).toBe(true);
    state.openAccountSwitcher('blocked');
    expect(state.accountSwitcherCredentialId).toBe('');
    state.accountSwitching = false;
    state.testingIds.add('testing');
    state.openAccountSwitcher('blocked-by-test');
    expect(state.accountSwitcherCredentialId).toBe('');
    state.testingIds.delete('testing');
    expect(
      enabledState.columns[5].render({
        ...valid,
        has_refresh_token: true,
      }).props?.canSwitchAccount,
    ).toBe(false);

    mutationOptions[3].onMutate('valid');
    expect(enabledState.columns[5].render(valid).props?.isTesting).toBe(true);
  });

  it('测试、选择和删除并发状态互相遵守冲突规则', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    const first = { credential_id: 'first', is_expired: false };
    const second = { credential_id: 'second', is_expired: false };

    mutationOptions[3].onMutate('first');
    let firstActions = state.columns[5].render(first);
    let secondActions = state.columns[5].render(second);
    expect(firstActions.props?.isTesting).toBe(true);
    expect(secondActions.props?.isTesting).toBe(false);
    expect(secondActions.props?.hasActiveTests).toBe(true);

    firstActions.props?.onTest('first');
    secondActions.props?.onTest('second');
    secondActions.props?.onSelect('second');
    secondActions.props?.onDelete('second');
    expect(mutationStates[3].mutate).toHaveBeenCalledTimes(1);
    expect(mutationStates[3].mutate).toHaveBeenCalledWith('second');
    expect(mutationStates[1].mutate).not.toHaveBeenCalled();
    expect(mutationStates[2].mutate).not.toHaveBeenCalled();

    mutationOptions[3].onSettled(undefined, undefined, 'first');
    mutationOptions[1].onMutate('second');
    secondActions = state.columns[5].render(second);
    expect(secondActions.props?.isSelecting).toBe(true);
    expect(secondActions.props?.writeInProgress).toBe(true);
    secondActions.props?.onTest('second');
    expect(mutationStates[3].mutate).toHaveBeenCalledTimes(1);
    mutationOptions[1].onSettled(undefined, undefined, 'other');
    expect(state.selectingId).toBe('second');
    mutationOptions[1].onSettled(undefined, undefined, 'second');

    mutationOptions[2].onMutate('second');
    secondActions = state.columns[5].render(second);
    expect(secondActions.props?.isDeleting).toBe(true);
    mutationOptions[2].onSettled(undefined, undefined, 'other');
    expect(state.deletingId).toBe('second');
    mutationOptions[2].onSettled(undefined, undefined, 'second');
  });

  it('存在测试时禁止新增、轮换与开始认证', async () => {
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;
    mutationOptions[3].onMutate('first');
    await wrapper.vm.$nextTick();

    const buttons = wrapper.findAll('button');
    expect(
      buttons.find((button) => button.text().includes('开始认证'))?.attributes('disabled'),
    ).toBeDefined();
    expect(
      buttons.find((button) => button.text().trim() === '添加')?.attributes('disabled'),
    ).toBeDefined();
    expect(
      buttons.find((button) => button.text().includes('自动轮换'))?.attributes('disabled'),
    ).toBeDefined();
    await state.submitCredential();
    state.start();
    state.toggleRotation();
    expect(mutationStates[0].mutate).not.toHaveBeenCalled();
    expect(oauth.start).not.toHaveBeenCalled();
    expect(mutationStates[4].mutate).not.toHaveBeenCalled();
  });

  it('轮询与错误模板分支可操作', async () => {
    credentialsQuery.data.value = {
      credentials: [],
      current: { status: 'no_credentials', auto_rotation_enabled: false },
    };
    credentialsQuery.error.value = new Error('load failed');
    oauth.polling.value = true;
    oauth.elapsedSeconds.value = 5;
    oauth.authUrl.value = 'https://auth';
    const wrapper = mountView();

    expect(wrapper.text()).toContain('等待登录认证');
    expect(wrapper.text()).toContain('00:05');
    expect(wrapper.text()).toContain('凭证加载失败');

    const state = (wrapper.vm.$ as any).setupState;
    state.start();
    const buttons = wrapper.findAll('button');
    await buttons.find((button) => button.text().includes('复制链接'))?.trigger('click');
    await buttons.find((button) => button.text().includes('打开登录页'))?.trigger('click');
    await buttons.find((button) => button.text().includes('取消认证'))?.trigger('click');
    await buttons.find((button) => button.text().includes('开启自动轮换'))?.trigger('click');
    await buttons.find((button) => button.text().includes('重试'))?.trigger('click');
    const inputs = wrapper.findAll('input');
    await inputs[0].setValue('token');
    await buttons.find((button) => button.text().includes('添加'))?.trigger('click');
    // submitCredential 内部 await formRef.validate() 后才 mutate，需等待 microtask
    await vi.waitFor(() => expect(mutationStates[0].mutate).toHaveBeenCalled());
    await wrapper.vm.$nextTick();

    expect(oauth.start).toHaveBeenCalledOnce();
    expect(oauth.cancel).toHaveBeenCalledOnce();
    expect(copyMock).toHaveBeenCalledWith('https://auth', '认证链接已复制');
    expect(credentialsQuery.refetch).toHaveBeenCalledOnce();
    expect(oauth.openAuthUrl).toHaveBeenCalled();
    expect(mutationStates[0].mutate).toHaveBeenCalled();
    expect(mutationStates[4].mutate).toHaveBeenCalled();
  });

  it('自动轮换状态未知时禁用切换按钮', async () => {
    credentialsQuery.data.value = {
      credentials: [],
      current: { status: 'no_credentials' },
    };
    const wrapper = mountView();
    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.text().trim() === '自动轮换');

    expect(toggleButton?.attributes('disabled')).toBeDefined();
    await toggleButton?.trigger('click');
    (wrapper.vm.$ as any).setupState.toggleRotation();
    expect(mutationStates[4].mutate).not.toHaveBeenCalled();
  });

  it('自动轮换启用时显示关闭按钮分支', async () => {
    credentialsQuery.data.value = {
      credentials: [],
      current: { auto_rotation_enabled: true },
    };
    const wrapper = mountView();
    const toggleButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('关闭自动轮换'));
    await toggleButton?.trigger('click');
    await wrapper.vm.$nextTick();
    expect(mutationStates[4].mutate).toHaveBeenCalled();
  });

  it('通过模板 v-model 切换 filterTab 触发 rows 过滤', async () => {
    credentialsQuery.data.value = {
      credentials: [
        { credential_id: 'valid', is_expired: false },
        { credential_id: 'expired', is_expired: true },
      ],
      current: { credential_id: 'valid' },
    };
    const wrapper = mountView();
    const state = (wrapper.vm.$ as any).setupState;

    expect(state.filterTab).toBe('all');
    expect(state.rows.map((i: any) => i.credential_id)).toEqual(['valid', 'expired']);

    wrapper.findComponent(CRadioGroup).vm.$emit('update:modelValue', 'expired');
    await wrapper.vm.$nextTick();

    expect(state.filterTab).toBe('expired');
    expect(state.rows.map((i: any) => i.credential_id)).toEqual(['expired']);
  });
});
