import { defineComponent } from 'vue';
import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const { query, queryOptions, mutationOptions, mutation, invalidateQueries, toastMock } = vi.hoisted(
  () => {
    const refValue = <T>(value: T) => ({ __v_isRef: true as const, value });
    return {
      query: {
        data: refValue<any>(undefined),
        isError: refValue(false),
        isLoading: refValue(false),
      },
      queryOptions: [] as Array<Record<string, any>>,
      mutationOptions: {} as Record<string, (...args: any[]) => any>,
      mutation: {
        isPending: refValue(false),
        mutate: vi.fn<() => void>() as Mock,
      },
      invalidateQueries: vi.fn<(filters?: unknown) => Promise<void>>(),
      toastMock: { success: vi.fn<(message: string) => void>() },
    };
  },
);

vi.mock('@tanstack/vue-query', () => ({
  useQuery: (options: Record<string, any>) => {
    queryOptions.push(options);
    return query;
  },
  useMutation: (options: Record<string, (...args: any[]) => any>) => {
    Object.assign(mutationOptions, options);
    return mutation;
  },
  useQueryClient: () => ({ invalidateQueries }),
}));

vi.mock('../composables/useToast', () => ({ useToast: () => toastMock }));

import CredentialAccountSwitcher from '../components/CredentialAccountSwitcher.vue';
import { adminApi } from '../api/admin';

const ModalStub = defineComponent({
  name: 'CModal',
  props: ['open', 'title', 'closable'],
  emits: ['update:open'],
  template:
    '<section v-if="open" class="modal"><slot /><footer><slot name="footer" /></footer><button class="modal-close" @click="$emit(\'update:open\', false)" /></section>',
});

function mountSwitcher(open = true, eventOrder?: string[]) {
  return mount(CredentialAccountSwitcher, {
    props: {
      open,
      credentialId: 'cred/id',
      onSwitching: (value: boolean) => eventOrder?.push(`switching:${String(value)}`),
      onClose: () => eventOrder?.push('close'),
    },
    global: {
      stubs: {
        CModal: ModalStub,
        CAlert: { template: '<div class="alert"><slot /></div>' },
        CRadioGroup: {
          name: 'CRadioGroup',
          props: ['modelValue'],
          emits: ['update:modelValue'],
          template: '<div class="radio-group"><slot /></div>',
        },
        CRadioButton: {
          props: ['value'],
          template: '<button class="radio"><slot /></button>',
        },
      },
    },
  });
}

describe('CredentialAccountSwitcher', () => {
  beforeEach(() => {
    query.data.value = undefined;
    query.isError.value = false;
    query.isLoading.value = false;
    queryOptions.length = 0;
    Object.keys(mutationOptions).forEach((key) => delete mutationOptions[key]);
    mutation.isPending.value = false;
    mutation.mutate.mockReset();
    invalidateQueries.mockReset();
    invalidateQueries.mockResolvedValue(undefined);
    toastMock.success.mockReset();
  });

  it('使用用户隔离查询键并渲染安全账号信息', async () => {
    const accountsSpy = vi.spyOn(adminApi, 'credentialAccounts').mockResolvedValue({
      current_account_id: 'enterprise',
      accounts: [],
      can_switch: false,
    });
    query.data.value = {
      current_account_id: 'enterprise',
      accounts: [
        {
          account_id: 'personal',
          type: 'personal',
          nickname: '',
          enterprise_name: '',
        },
        {
          account_id: 'enterprise',
          type: 'enterprise',
          nickname: '张三',
          enterprise_name: '测试企业',
        },
      ],
    };
    const wrapper = mountSwitcher();
    await wrapper.vm.$nextTick();
    const state = (wrapper.vm.$ as any).setupState;

    expect(queryOptions[0].queryKey.value).toEqual([
      'admin',
      'test-user',
      'credentials',
      'cred/id',
      'accounts',
    ]);
    expect(queryOptions[0].enabled.value).toBe(true);
    expect(queryOptions[0].networkMode).toBe('always');
    expect(queryOptions[0].refetchOnReconnect).toBe(false);
    await queryOptions[0].queryFn();
    expect(accountsSpy).toHaveBeenCalledWith('cred/id');
    expect(state.selectedAccountId).toBe('enterprise');
    expect(state.accountLabel(query.data.value.accounts[0])).toBe('个人账号');
    expect(state.accountLabel(query.data.value.accounts[1])).toBe('张三 · 测试企业');
    expect(wrapper.text()).toContain('张三 · 测试企业');
    wrapper.findComponent({ name: 'CRadioGroup' }).vm.$emit('update:modelValue', 'personal');
    await wrapper.vm.$nextTick();
    expect(state.selectedAccountId).toBe('personal');
  });

  it('切换成功后驱逐账号、凭证与状态缓存', async () => {
    query.data.value = {
      current_account_id: '',
      accounts: [{ account_id: 'only', type: 'enterprise', nickname: '' }],
    };
    const selectSpy = vi.spyOn(adminApi, 'selectCredentialAccount').mockResolvedValue({} as never);
    const eventOrder: string[] = [];
    const wrapper = mountSwitcher(true, eventOrder);
    await wrapper.vm.$nextTick();
    const state = (wrapper.vm.$ as any).setupState;
    expect(state.selectedAccountId).toBe('only');
    expect(state.accountLabel(query.data.value.accounts[0])).toBe('企业账号');

    mutationOptions.onMutate();
    expect(wrapper.emitted('switching')).toEqual([[true]]);
    expect(await mutationOptions.mutationFn()).toEqual({});
    expect(selectSpy).toHaveBeenCalledWith('cred/id', 'only');
    await mutationOptions.onSuccess();
    expect(toastMock.success).toHaveBeenCalledWith('CodeBuddy 账号已切换');
    expect(invalidateQueries.mock.calls).toEqual([
      [{ queryKey: ['admin', 'test-user', 'credentials', 'cred/id', 'accounts'] }],
      [{ queryKey: ['admin', 'test-user', 'credentials'] }],
      [{ queryKey: ['admin', 'test-user', 'status'] }],
    ]);
    expect(wrapper.emitted('close')).toBeUndefined();
    mutationOptions.onSettled({ selected: true }, null);
    expect(wrapper.emitted('switching')).toEqual([[true], [false]]);
    expect(wrapper.emitted('close')).toEqual([[]]);
    expect(eventOrder).toEqual(['switching:true', 'switching:false', 'close']);

    state.confirm();
    expect(mutation.mutate).toHaveBeenCalledTimes(1);
    mutation.isPending.value = true;
    state.confirm();
    state.close();
    expect(mutation.mutate).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted('close')).toEqual([[]]);
  });

  it('切换失败仅解除 switching 状态且不关闭弹窗', () => {
    const eventOrder: string[] = [];
    const wrapper = mountSwitcher(true, eventOrder);

    mutationOptions.onMutate();
    mutationOptions.onSettled(undefined, new Error('switch failed'));

    expect(wrapper.emitted('switching')).toEqual([[true], [false]]);
    expect(wrapper.emitted('close')).toBeUndefined();
    expect(eventOrder).toEqual(['switching:true', 'switching:false']);
  });

  it('覆盖关闭、空选择及加载失败状态', async () => {
    query.isError.value = true;
    const wrapper = mountSwitcher();
    const state = (wrapper.vm.$ as any).setupState;
    expect(wrapper.text()).toContain('账号列表加载失败');
    state.confirm();
    expect(mutation.mutate).not.toHaveBeenCalled();
    state.close();
    expect(wrapper.emitted('close')).toEqual([[]]);

    wrapper.unmount();
    query.isError.value = false;
    query.isLoading.value = true;
    const loadingWrapper = mountSwitcher();
    expect(loadingWrapper.text()).toContain('正在加载账号列表');
    await loadingWrapper.get('.modal-close').trigger('click');
    expect(loadingWrapper.emitted('close')).toEqual([[]]);

    loadingWrapper.unmount();
    query.isLoading.value = false;
    const emptyWrapper = mountSwitcher();
    expect(emptyWrapper.findAll('.radio')).toHaveLength(0);
  });
});
