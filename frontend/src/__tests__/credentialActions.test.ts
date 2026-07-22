import { defineComponent } from 'vue';
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import CredentialActions from '../components/CredentialActions.vue';
import CPopconfirm from '../components/ui/CPopconfirm.vue';
import CTooltip from '../components/ui/CTooltip.vue';
import type { CredentialRecord } from '../types';

const TooltipStub = defineComponent({
  name: 'CTooltip',
  props: { content: String },
  template: '<span class="tooltip-stub"><slot /><slot name="content" /></span>',
});

const PopconfirmStub = defineComponent({
  name: 'CPopconfirm',
  props: { title: String, confirmVariant: String },
  emits: ['confirm'],
  template:
    '<span class="popconfirm-stub"><slot /><button aria-label="确认删除" @click="$emit(\'confirm\')" /></span>',
});

const credential: CredentialRecord = {
  credential_id: 'cred-1',
  filename: 'credential.json',
  user_id: 'user-1',
  email: 'user@example.com',
  time_remaining_str: '1h',
  is_expired: false,
  token_type: 'Bearer',
  has_refresh_token: false,
  has_token: true,
  token_display: 'token...view',
};

function mountActions(
  overrides: Partial<{
    credential: CredentialRecord;
    isCurrent: boolean;
    autoRotationEnabled: boolean;
    isTesting: boolean;
    isSelecting: boolean;
    isDeleting: boolean;
    writeInProgress: boolean;
    hasActiveTests: boolean;
    canSwitchAccount: boolean;
    canCheckIn: boolean;
    isCheckingIn: boolean;
    checkinDisabledReason: string;
  }> = {},
) {
  return mount(CredentialActions, {
    props: {
      credential,
      isCurrent: false,
      isTesting: false,
      ...overrides,
    },
    global: {
      stubs: {
        CTooltip: TooltipStub,
        CPopconfirm: PopconfirmStub,
      },
    },
  });
}

describe('CredentialActions', () => {
  it('非当前凭证可执行切换、测试和删除', async () => {
    const wrapper = mountActions();

    expect(wrapper.findAllComponents(CTooltip).map((item) => item.props('content'))).toEqual([
      '设为当前凭证',
      '测试凭证',
      '删除凭证',
    ]);

    await wrapper.get('[aria-label="切换为当前凭证"]').trigger('click');
    await wrapper.get('[aria-label="测试凭证"]').trigger('click');
    await wrapper.get('[aria-label="确认删除"]').trigger('click');

    expect(wrapper.emitted('select')).toEqual([['cred-1']]);
    expect(wrapper.emitted('test')).toEqual([['cred-1']]);
    expect(wrapper.emitted('delete')).toEqual([['cred-1']]);

    await wrapper.setProps({ isTesting: true });
    expect(wrapper.get('[aria-label="测试凭证"]').attributes('disabled')).toBeDefined();
  });

  it('当前凭证显示禁用状态且不提供切换操作', async () => {
    const wrapper = mountActions({ isCurrent: true });
    const currentButton = wrapper.get('[aria-label="已是当前凭证"]');

    expect(currentButton.attributes('disabled')).toBeDefined();
    expect(currentButton.classes()).toContain('current-credential-action-button');
    expect(wrapper.find('[aria-label="切换为当前凭证"]').exists()).toBe(false);
    await currentButton.trigger('click');
    expect(wrapper.emitted('select')).toBeUndefined();
  });

  it('自动轮换开启时当前凭证仍可点击固定', async () => {
    const wrapper = mountActions({ isCurrent: true, autoRotationEnabled: true });
    const currentButton = wrapper.get('[aria-label="固定当前凭证"]');

    expect(currentButton.attributes('disabled')).toBeUndefined();
    expect(currentButton.classes()).not.toContain('current-credential-action-button');
    await currentButton.trigger('click');
    expect(wrapper.emitted('select')).toEqual([['cred-1']]);
  });

  it('删除确认文案依次使用邮箱、用户 ID 和凭证 ID', async () => {
    const wrapper = mountActions();
    const popconfirm = wrapper.findComponent(CPopconfirm);
    expect(popconfirm.props('title')).toContain('user@example.com');
    expect(popconfirm.props('confirmVariant')).toBe('danger');

    await wrapper.setProps({ credential: { ...credential, email: undefined } });
    expect(popconfirm.props('title')).toContain('user-1');

    await wrapper.setProps({ credential: { ...credential, email: undefined, user_id: '' } });
    expect(popconfirm.props('title')).toContain('cred-1');
  });

  it('并发测试只锁定写操作，任一写操作会锁定全部行操作', async () => {
    const wrapper = mountActions({ hasActiveTests: true });
    const selectButton = wrapper.get('[aria-label="切换为当前凭证"]');

    expect(selectButton.attributes('disabled')).toBeDefined();
    expect(selectButton.classes()).not.toContain('current-credential-action-button');
    expect(selectButton.find('.lucide-circle-check-big').exists()).toBe(false);
    expect(selectButton.find('.lucide-mouse-pointer-click').exists()).toBe(true);
    expect(wrapper.get('[aria-label="删除凭证"]').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[aria-label="测试凭证"]').attributes('disabled')).toBeUndefined();

    await wrapper.setProps({ isTesting: true });
    expect(wrapper.get('[aria-label="测试凭证"]').attributes('disabled')).toBeDefined();

    await wrapper.setProps({ hasActiveTests: false, isTesting: false, writeInProgress: true });
    expect(wrapper.get('[aria-label="切换为当前凭证"]').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[aria-label="测试凭证"]').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[aria-label="删除凭证"]').attributes('disabled')).toBeDefined();

    await wrapper.get('[aria-label="切换为当前凭证"]').trigger('click');
    await wrapper.get('[aria-label="测试凭证"]').trigger('click');
    await wrapper.get('[aria-label="确认删除"]').trigger('click');
    const state = (wrapper.vm.$ as any).setupState;
    state.selectCredential();
    state.testCredential();
    expect(wrapper.emitted('select')).toBeUndefined();
    expect(wrapper.emitted('test')).toBeUndefined();
    expect(wrapper.emitted('delete')).toBeUndefined();
  });

  it('选择和删除分别显示目标行 loading', async () => {
    const wrapper = mountActions({ isSelecting: true });
    expect(wrapper.get('[aria-label="切换为当前凭证"]').attributes('disabled')).toBeDefined();

    await wrapper.setProps({ isSelecting: false, isDeleting: true });
    expect(wrapper.get('[aria-label="删除凭证"]').attributes('disabled')).toBeDefined();
  });

  it('仅对可切换的 OAuth 凭证显示账号切换操作', async () => {
    const wrapper = mountActions({ canSwitchAccount: true });

    expect(wrapper.findAllComponents(CTooltip).map((item) => item.props('content'))).toEqual([
      '设为当前凭证',
      '测试凭证',
      '切换 CodeBuddy 账号',
      '删除凭证',
    ]);
    await wrapper.get('[aria-label="切换 CodeBuddy 账号"]').trigger('click');
    expect(wrapper.emitted('switchAccount')).toEqual([['cred-1']]);

    await wrapper.setProps({ writeInProgress: true });
    await wrapper.get('[aria-label="切换 CodeBuddy 账号"]').trigger('click');
    const state = (wrapper.vm.$ as any).setupState;
    state.switchAccount();
    expect(wrapper.emitted('switchAccount')).toEqual([['cred-1']]);

    await wrapper.setProps({ writeInProgress: false, hasActiveTests: true });
    state.switchAccount();
    expect(wrapper.emitted('switchAccount')).toEqual([['cred-1']]);

    await wrapper.setProps({ hasActiveTests: false, canSwitchAccount: false });
    state.switchAccount();
    expect(wrapper.emitted('switchAccount')).toEqual([['cred-1']]);
  });

  it('仅对有效个人版凭证显示签到，并展示今日详情和成功禁用状态', async () => {
    const wrapper = mountActions({ canCheckIn: true });
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.checkinTooltipLines).toEqual(['签到']);
    const button = wrapper.get('[aria-label="签到"]');
    expect(button.attributes('disabled')).toBeUndefined();
    await button.trigger('click');
    expect(wrapper.emitted('checkin')).toEqual([['cred-1']]);

    await wrapper.setProps({
      credential: {
        ...credential,
        daily_checkin: {
          code: 7,
          message: '稍后再试',
          success: false,
        },
      },
    });
    expect(state.checkinTooltipLines).toEqual(['Code：7', '消息：稍后再试']);
    expect(wrapper.get('[aria-label="签到"]').attributes('disabled')).toBeUndefined();

    await wrapper.setProps({
      credential: {
        ...credential,
        daily_checkin: {
          code: 0,
          message: 'OK',
          success: true,
          credit: 100,
          checked_in_at: 1_700_000_000,
          next_checkin_at: 1_700_086_400,
        },
      },
    });
    expect(state.checkinTooltipLines[0]).toContain('签到时间：');
    expect(state.checkinTooltipLines).toContain('获得积分：100');
    expect(state.checkinTooltipLines.at(-1)).toContain('下次可签到：');
    expect(wrapper.get('[aria-label="签到"]').attributes('disabled')).toBeDefined();
    state.checkin();
    expect(wrapper.emitted('checkin')).toEqual([['cred-1']]);

    await wrapper.setProps({ isCheckingIn: true });
    expect(wrapper.get('[aria-label="签到"]').attributes('disabled')).toBeDefined();

    await wrapper.setProps({
      isCheckingIn: false,
      credential: {
        ...credential,
        daily_checkin: {
          code: 0,
          message: 'OK',
          success: true,
          credit: null,
        },
      },
    });
    expect(state.checkinTooltipLines).toEqual(['签到时间：-', '获得积分：-']);

    await wrapper.setProps({
      credential: {
        ...credential,
        daily_checkin: { code: null, message: '网络异常', success: false },
      },
      writeInProgress: true,
    });
    expect(state.checkinTooltipLines).toEqual(['Code：未知', '消息：网络异常']);
    state.checkin();
    expect(wrapper.emitted('checkin')).toEqual([['cred-1']]);

    const hidden = mountActions();
    (hidden.vm.$ as any).setupState.checkin();
    expect(hidden.emitted('checkin')).toBeUndefined();
  });

  it('签到期间禁用同一凭证的冲突操作', async () => {
    const wrapper = mountActions({
      canCheckIn: true,
      canSwitchAccount: true,
      isCheckingIn: true,
    });

    for (const label of ['切换为当前凭证', '测试凭证', '切换 CodeBuddy 账号', '删除凭证']) {
      expect(wrapper.get(`[aria-label="${label}"]`).attributes('disabled')).toBeDefined();
    }
    const state = (wrapper.vm.$ as any).setupState;
    state.selectCredential();
    state.testCredential();
    state.switchAccount();
    state.deleteCredential();
    expect(wrapper.emitted('select')).toBeUndefined();
    expect(wrapper.emitted('test')).toBeUndefined();
    expect(wrapper.emitted('switchAccount')).toBeUndefined();
    expect(wrapper.emitted('delete')).toBeUndefined();
  });

  it('企业版凭证显示禁用的签到按钮和不支持提示', async () => {
    const wrapper = mountActions({
      credential: { ...credential, enterprise_id: 'enterprise' },
      checkinDisabledReason: '企业版凭证不支持签到',
    });

    expect(wrapper.text()).toContain('企业版凭证不支持签到');
    expect(wrapper.get('[aria-label="签到"]').attributes('disabled')).toBeDefined();
    await wrapper.get('[aria-label="签到"]').trigger('click');
    expect(wrapper.emitted('checkin')).toBeUndefined();
  });
});
