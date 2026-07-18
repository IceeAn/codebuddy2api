<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { adminApi } from '../api/admin';
import { useSessionStore } from '../stores/session';
import { useToast } from '../composables/useToast';
import type { CredentialAccount } from '../types';
import CAlert from './ui/CAlert.vue';
import CButton from './ui/CButton.vue';
import CModal from './ui/CModal.vue';
import CRadioButton from './ui/CRadioButton.vue';
import CRadioGroup from './ui/CRadioGroup.vue';

const props = defineProps<{
  open: boolean;
  credentialId: string;
}>();
const emit = defineEmits<{
  close: [];
  switching: [value: boolean];
}>();

const session = useSessionStore();
const queryClient = useQueryClient();
const toast = useToast();
const selectedAccountId = ref('');
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
  if (!selectedAccountId.value || switchMutation.isPending.value) return;
  switchMutation.mutate();
}

function accountLabel(account: CredentialAccount): string {
  const identity = account.nickname || (account.type === 'personal' ? '个人账号' : '企业账号');
  const organization = [account.enterprise_name, account.department_full_name]
    .filter(Boolean)
    .join(' / ');
  return organization ? `${identity} · ${organization}` : identity;
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
    <CRadioGroup v-else v-model="selectedAccountId" class="flex flex-col gap-2">
      <CRadioButton
        v-for="account in accountsQuery.data.value?.accounts || []"
        :key="account.account_id"
        :value="account.account_id"
      >
        {{ accountLabel(account) }}
      </CRadioButton>
    </CRadioGroup>
    <template #footer>
      <CButton :disabled="switchMutation.isPending.value" @click="close">取消</CButton>
      <CButton
        variant="primary"
        :loading="switchMutation.isPending.value"
        :disabled="!selectedAccountId"
        @click="confirm"
      >
        确认切换
      </CButton>
    </template>
  </CModal>
</template>
