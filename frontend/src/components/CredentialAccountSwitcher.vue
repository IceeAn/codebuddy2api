<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { adminApi } from '../api/admin';
import { useSessionStore } from '../stores/session';
import { useToast } from '../composables/useToast';
import type { CredentialAccount } from '../types';
import CAlert from './ui/CAlert.vue';
import CButton from './ui/CButton.vue';
import CModal from './ui/CModal.vue';

const props = withDefaults(
  defineProps<{
    open: boolean;
    credentialId: string;
    disabled?: boolean;
  }>(),
  { disabled: false },
);
const emit = defineEmits<{
  close: [];
  switching: [value: boolean];
}>();

const session = useSessionStore();
const queryClient = useQueryClient();
const toast = useToast();
const selectedAccountId = ref('');
const accountRadioName = `credential-account-${useId()}`;
let switchSucceeded = false;
const accountQueryKey = computed(() => [
  'admin',
  session.username,
  'credentials',
  props.credentialId,
  'accounts',
]);
const accountsQuery = useQuery({
  queryKey: accountQueryKey,
  queryFn: () => adminApi.credentialAccounts(props.credentialId),
  enabled: computed(() => props.open && Boolean(props.credentialId)),
  networkMode: 'always',
  refetchOnReconnect: false,
});

watch(
  () => accountsQuery.data.value,
  (data) => {
    selectedAccountId.value = data?.current_account_id || data?.accounts[0]?.account_id || '';
  },
  { immediate: true },
);

const switchMutation = useMutation({
  mutationFn: () => adminApi.selectCredentialAccount(props.credentialId, selectedAccountId.value),
  networkMode: 'always',
  onMutate: () => {
    switchSucceeded = false;
    emit('switching', true);
  },
  onSuccess: async () => {
    switchSucceeded = true;
    toast.success('CodeBuddy 账号已切换');
    await queryClient.invalidateQueries({ queryKey: accountQueryKey.value });
    await queryClient.invalidateQueries({
      queryKey: ['admin', session.username, 'credentials'],
    });
    await queryClient.invalidateQueries({ queryKey: ['admin', session.username, 'status'] });
  },
  onSettled: () => {
    emit('switching', false);
    if (switchSucceeded) emit('close');
    switchSucceeded = false;
  },
});

function close(): void {
  if (!switchMutation.isPending.value) emit('close');
}

function confirm(): void {
  if (props.disabled || !selectedAccountId.value || switchMutation.isPending.value) return;
  switchMutation.mutate();
}

function accountLabel(account: CredentialAccount): string {
  const identity = account.nickname || (account.type === 'personal' ? '个人账号' : '企业账号');
  return account.enterprise_name ? `${identity} · ${account.enterprise_name}` : identity;
}
</script>

<template>
  <CModal
    :open="open"
    title="切换 CodeBuddy 账号"
    :closable="!switchMutation.isPending.value"
    @update:open="close"
  >
    <CAlert v-if="accountsQuery.isError.value" type="error"
      >账号列表加载失败，请关闭后重试。</CAlert
    >
    <div v-else-if="accountsQuery.isLoading.value" class="text-sm text-muted">
      正在加载账号列表…
    </div>
    <fieldset v-else class="flex flex-col gap-2">
      <legend class="sr-only">选择 CodeBuddy 账号</legend>
      <label
        v-for="account in accountsQuery.data.value?.accounts || []"
        :key="account.account_id"
        :class="[
          'flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2.5 text-sm transition-[background-color,border-color,box-shadow]',
          selectedAccountId === account.account_id
            ? 'border-brand-500 bg-brand-500/[0.07] text-text-strong'
            : 'border-border bg-surface text-text hover:border-border-strong hover:bg-surface-2',
        ]"
      >
        <input
          v-model="selectedAccountId"
          class="credential-account-radio peer sr-only"
          type="radio"
          :name="accountRadioName"
          :value="account.account_id"
        />
        <span
          :class="[
            'credential-account-radio-mark flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-[1.5px] transition-[background-color,border-color,box-shadow]',
            selectedAccountId === account.account_id
              ? 'border-brand-600'
              : 'border-border-strong bg-surface',
          ]"
          aria-hidden="true"
        >
          <span
            v-if="selectedAccountId === account.account_id"
            class="h-2 w-2 rounded-full bg-brand-600"
          />
        </span>
        <span>{{ accountLabel(account) }}</span>
      </label>
    </fieldset>
    <template #footer>
      <CButton :disabled="switchMutation.isPending.value" @click="close">取消</CButton>
      <CButton
        variant="primary"
        :loading="switchMutation.isPending.value"
        :disabled="disabled || !selectedAccountId"
        @click="confirm"
      >
        确认切换
      </CButton>
    </template>
  </CModal>
</template>

<style scoped>
.credential-account-radio:focus-visible + .credential-account-radio-mark {
  border-color: var(--color-brand-500);
  box-shadow: 0 0 0 3px var(--focus-ring);
}
</style>
