<script setup lang="ts">
import { computed } from 'vue';
import {
  Building2,
  CalendarCheck,
  CircleCheckBig,
  MousePointerClick,
  RotateCcw,
  Trash2,
} from '@lucide/vue';
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
  canCheckIn?: boolean;
  isCheckingIn?: boolean;
  checkinDisabledReason?: string;
}

const props = withDefaults(defineProps<Props>(), {
  autoRotationEnabled: false,
  isSelecting: false,
  isDeleting: false,
  writeInProgress: false,
  hasActiveTests: false,
  canSwitchAccount: false,
  canCheckIn: false,
  isCheckingIn: false,
  checkinDisabledReason: '',
});

const emit = defineEmits<{
  select: [credentialId: string];
  test: [credentialId: string];
  delete: [credentialId: string];
  switchAccount: [credentialId: string];
  checkin: [credentialId: string];
}>();

const deleteTitle = computed(
  () =>
    `确定删除凭证 ${props.credential.email || props.credential.user_id || props.credential.credential_id}？该操作不可恢复`,
);
const isFixedCurrent = computed(() => props.isCurrent && !props.autoRotationEnabled);
const selectDisabled = computed(
  () => isFixedCurrent.value || props.writeInProgress || props.hasActiveTests || props.isCheckingIn,
);
const testDisabled = computed(() => props.writeInProgress || props.isTesting || props.isCheckingIn);
const deleteDisabled = computed(
  () => props.writeInProgress || props.hasActiveTests || props.isCheckingIn,
);
const checkinDisabled = computed(
  () =>
    props.writeInProgress ||
    props.isCheckingIn ||
    Boolean(props.checkinDisabledReason) ||
    props.credential.daily_checkin?.success === true,
);
const checkinTooltipLines = computed(() => {
  if (props.checkinDisabledReason) return [props.checkinDisabledReason];
  const detail = props.credential.daily_checkin;
  if (!detail) return ['签到'];
  const lines: string[] = [];
  if (detail.code === 0) {
    const checkedInAt =
      typeof detail.checked_in_at === 'number'
        ? new Date(detail.checked_in_at * 1000).toLocaleString()
        : '-';
    lines.push(`签到时间：${checkedInAt}`);
    lines.push(`获得积分：${detail.credit ?? '-'}`);
  } else {
    lines.push(`Code：${detail.code ?? '未知'}`);
    lines.push(`消息：${detail.message}`);
  }
  if (detail.success && typeof detail.next_checkin_at === 'number') {
    lines.push(`下次可签到：${new Date(detail.next_checkin_at * 1000).toLocaleString()}`);
  }
  return lines;
});
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
  if (
    props.writeInProgress ||
    props.hasActiveTests ||
    props.isCheckingIn ||
    !props.canSwitchAccount
  )
    return;
  emit('switchAccount', props.credential.credential_id);
}

function checkin(): void {
  if (!props.canCheckIn || checkinDisabled.value) return;
  emit('checkin', props.credential.credential_id);
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
        :disabled="writeInProgress || hasActiveTests || isCheckingIn"
        aria-label="切换 CodeBuddy 账号"
        @click="switchAccount"
      >
        <template #icon><Building2 :size="14" /></template>
      </CButton>
    </CTooltip>

    <CTooltip v-if="canCheckIn || checkinDisabledReason">
      <template #content>
        <span class="flex flex-col gap-1">
          <span v-for="line in checkinTooltipLines" :key="line">{{ line }}</span>
        </span>
      </template>
      <CButton
        size="sm"
        variant="secondary"
        shape="circle"
        class="table-action-button"
        :loading="isCheckingIn"
        :disabled="checkinDisabled"
        aria-label="签到"
        @click="checkin"
      >
        <template #icon><CalendarCheck :size="14" /></template>
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
