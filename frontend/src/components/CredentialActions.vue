<script setup lang="ts">
import { computed } from 'vue';
import { CircleCheckBig, MousePointerClick, RotateCcw, Trash2 } from '@lucide/vue';
import type { CredentialRecord } from '../types';
import CButton from './ui/CButton.vue';
import CPopconfirm from './ui/CPopconfirm.vue';
import CTooltip from './ui/CTooltip.vue';

interface Props {
  credential: CredentialRecord;
  isCurrent: boolean;
  autoRotationEnabled?: boolean;
  isTesting: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  autoRotationEnabled: false,
});

const emit = defineEmits<{
  select: [credentialId: string];
  test: [credentialId: string];
  delete: [credentialId: string];
}>();

const deleteTitle = computed(
  () =>
    `确定删除凭证 ${props.credential.email || props.credential.user_id || props.credential.credential_id}？该操作不可恢复`,
);
const selectDisabled = computed(() => props.isCurrent && !props.autoRotationEnabled);
const selectTooltip = computed(() => {
  if (!props.isCurrent) return '设为当前凭证';
  return props.autoRotationEnabled ? '固定当前凭证' : '已是当前凭证';
});
const selectAriaLabel = computed(() => {
  if (!props.isCurrent) return '切换为当前凭证';
  return props.autoRotationEnabled ? '固定当前凭证' : '已是当前凭证';
});

function selectCredential(): void {
  emit('select', props.credential.credential_id);
}

function testCredential(): void {
  emit('test', props.credential.credential_id);
}

function deleteCredential(): void {
  emit('delete', props.credential.credential_id);
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
        :class="[
          'table-action-button',
          { 'current-credential-action-button': selectDisabled },
        ]"
        :aria-label="selectAriaLabel"
        @click="selectCredential"
      >
        <template #icon>
          <CircleCheckBig v-if="selectDisabled" :size="14" />
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
        aria-label="测试凭证"
        @click="testCredential"
      >
        <template #icon><RotateCcw :size="14" /></template>
      </CButton>
    </CTooltip>

    <CTooltip content="删除凭证">
      <CPopconfirm :title="deleteTitle" confirm-variant="danger" @confirm="deleteCredential">
        <CButton
          size="sm"
          variant="secondary"
          shape="circle"
          class="table-action-button"
          aria-label="删除凭证"
        >
          <template #icon><Trash2 :size="14" /></template>
        </CButton>
      </CPopconfirm>
    </CTooltip>
  </div>
</template>
