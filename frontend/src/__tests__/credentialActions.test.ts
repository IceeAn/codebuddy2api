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
  template: '<span class="tooltip-stub"><slot /></span>',
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
});
