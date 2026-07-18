<script setup lang="ts">
import { computed } from 'vue';
import { Building2, CircleCheckBig, MousePointerClick, RotateCcw, Trash2 } from '@lucide/vue';
import type { CredentialRecord } from '../types';
import CButton from './ui/CButton.vue';
import CPopconfirm from './ui/CPopconfirm.vue';
import CTooltip from './ui/CTooltip.vue';

interface Props {
  credential: CredentialRecord;
  isCurrent: boolean;
  autoRotationEnabled?: boolean;
  isTesting: boolean;
  isSelecting?: boolean;
  isDeleting?: boolean;
  writeInProgress?: boolean;
  hasActiveTests?: boolean;
  canSwitchAccount?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  autoRotationEnabled: false,
  isSelecting: false,
  isDeleting: false,
  writeInProgress: false,
  hasActiveTests: false,
  canSwitchAccount: false,
});

const emit = defineEmits<{
  select: [credentialId: string];
  test: [credentialId: string];
  delete: [credentialId: string];
  switchAccount: [credentialId: string];
}>();

const deleteTitle = computed(
  () =>
    `确定删除凭证 ${props.credential.email || props.credential.user_id || props.credential.credential_id}？该操作不可恢复`,
);
const isFixedCurrent = computed(() => props.isCurrent && !props.autoRotationEnabled);
const selectDisabled = computed(
  () => isFixedCurrent.value || props.writeInProgress || props.hasActiveTests,
);
const testDisabled = computed(() => props.writeInProgress || props.isTesting);
const deleteDisabled = computed(() => props.writeInProgress || props.hasActiveTests);
const selectTooltip = computed(() => {
  if (!props.isCurrent) return '设为当前凭证';
  return props.autoRotationEnabled ? '固定当前凭证' : '已是当前凭证';
});
const selectAriaLabel = computed(() => {
  if (!props.isCurrent) return '切换为当前凭证';
  return props.autoRotationEnabled ? '固定当前凭证' : '已是当前凭证';
});

function selectCredential(): void {
  if (selectDisabled.value) return;
  emit('select', props.credential.credential_id);
}

function testCredential(): void {
  if (testDisabled.value) return;
  emit('test', props.credential.credential_id);
}

function deleteCredential(): void {
  if (deleteDisabled.value) return;
  emit('delete', props.credential.credential_id);
}

function switchAccount(): void {
  if (props.writeInProgress || props.hasActiveTests || !props.canSwitchAccount) return;
  emit('switchAccount', props.credential.credential_id);
}
</script>

<template>
  <div class="table-action-group flex items-center justify-start gap-1.5">
    <CTooltip :content="selectTooltip">
      <CButton
        size="sm"
        variant="secondary"
        shape="circle"
        :disabled="selectDisabled"
        :loading="isSelecting"
        :class="['table-action-button', { 'current-credential-action-button': isFixedCurrent }]"
        :aria-label="selectAriaLabel"
        @click="selectCredential"
      >
        <template #icon>
          <CircleCheckBig v-if="isFixedCurrent" :size="14" />
          <MousePointerClick v-else :size="14" />
        </template>
      </CButton>
    </CTooltip>

    <CTooltip content="测试凭证">
      <CButton
        size="sm"
        variant="secondary"
        shape="circle"
        class="table-action-button"
        :loading="isTesting"
        :disabled="testDisabled"
        aria-label="测试凭证"
        @click="testCredential"
      >
        <template #icon><RotateCcw :size="14" /></template>
      </CButton>
    </CTooltip>

    <CTooltip v-if="canSwitchAccount" content="切换 CodeBuddy 账号">
      <CButton
        size="sm"
        variant="secondary"
        shape="circle"
        class="table-action-button"
        :disabled="writeInProgress || hasActiveTests"
        aria-label="切换 CodeBuddy 账号"
        @click="switchAccount"
      >
        <template #icon><Building2 :size="14" /></template>
      </CButton>
    </CTooltip>

    <CTooltip content="删除凭证">
      <CPopconfirm :title="deleteTitle" confirm-variant="danger" @confirm="deleteCredential">
        <CButton
          size="sm"
          variant="secondary"
          shape="circle"
          class="table-action-button"
          :loading="isDeleting"
          :disabled="deleteDisabled"
          aria-label="删除凭证"
        >
          <template #icon><Trash2 :size="14" /></template>
        </CButton>
      </CPopconfirm>
    </CTooltip>
  </div>
</template>
