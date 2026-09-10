<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { KeyRound } from '@lucide/vue';
import { useQueryClient } from '@tanstack/vue-query';
import { authApi } from '../api/admin';
import { ApiError } from '../api/client';
import type { AuthErrorCode } from '../types';
import { useSessionStore } from '../stores/session';
import { useToast } from '../composables/useToast';
import { chunkLoadRecovery } from '../utils/chunkLoadRecovery';
import CButton from './ui/CButton.vue';
import CInput from './ui/CInput.vue';

const props = withDefaults(defineProps<{ forced?: boolean }>(), {
  forced: false,
});

const session = useSessionStore();
const queryClient = useQueryClient();
const toast = useToast();
const model = reactive({
  currentPassword: '',
  newPassword: '',
  confirmation: '',
});
const errors = reactive({
  currentPassword: '',
  newPassword: '',
  confirmation: '',
});
const submitting = ref(false);
const isDirty = computed(
  () => model.currentPassword.length + model.newPassword.length + model.confirmation.length > 0,
);

function focusField(id: string): void {
  void nextTick(() => document.getElementById(id)?.focus());
}

function clearCurrentError(): void {
  errors.currentPassword = '';
}

function clearNewErrors(): void {
  errors.newPassword = '';
  errors.confirmation = '';
}

function updateCurrentPassword(value: string): void {
  model.currentPassword = value;
  clearCurrentError();
}

function updateNewPassword(value: string): void {
  model.newPassword = value;
  clearNewErrors();
}

function updateConfirmation(value: string): void {
  model.confirmation = value;
  clearNewErrors();
}

function clearSensitiveFields(): void {
  model.currentPassword = '';
  model.newPassword = '';
  model.confirmation = '';
  clearCurrentError();
  clearNewErrors();
}

function setNewPasswordError(message: string, focusConfirmation = false): void {
  errors.newPassword = message;
  errors.confirmation = message;
  focusField(focusConfirmation ? 'password-confirmation' : 'new-password');
}

function isValidNewPassword(password: string): boolean {
  const length = Array.from(password).length;
  return length >= 8 && length <= 128 && !/\p{Cc}/u.test(password);
}

function validateLocally(): boolean {
  clearCurrentError();
  clearNewErrors();
  if (!props.forced && !model.currentPassword) {
    errors.currentPassword = '请输入当前密码';
    focusField('current-password');
    return false;
  }
  if (!isValidNewPassword(model.newPassword)) {
    setNewPasswordError('新密码必须为 8 至 128 个字符，且不能包含控制字符');
    return false;
  }
  if (!props.forced && model.currentPassword === model.newPassword) {
    setNewPasswordError('新密码不能与当前密码相同');
    return false;
  }
  if (model.newPassword !== model.confirmation) {
    setNewPasswordError('两次输入的新密码不一致', true);
    return false;
  }
  return true;
}

function errorCode(error: ApiError): AuthErrorCode | undefined {
  const detail = error.detail;
  if (typeof detail !== 'object' || detail === null || !('error_code' in detail)) {
    return undefined;
  }
  return String((detail as { error_code: unknown }).error_code) as AuthErrorCode;
}

async function returnToLogin(username: string, message: string): Promise<void> {
  clearSensitiveFields();
  queryClient.clear();
  session.finishPasswordChange(username, message);
  await chunkLoadRecovery.replace('/dashboard');
}

async function handleApiError(error: ApiError, username: string): Promise<void> {
  const code = errorCode(error);
  if (code === 'current_password_incorrect') {
    model.currentPassword = '';
    errors.currentPassword = '当前密码错误';
    focusField('current-password');
    return;
  }
  if (code === 'new_password_invalid' || code === 'new_password_unchanged') {
    setNewPasswordError(error.message);
    return;
  }
  if (code === 'password_changed_elsewhere') {
    await returnToLogin(username, '密码已由其他会话修改，请使用新密码登录');
    return;
  }
  if (code === 'bootstrap_expired') {
    await returnToLogin(username, '初始账号已过期，请重启服务后重试');
    return;
  }
  toast.error(error.message);
}

async function submit(): Promise<void> {
  if (submitting.value || !validateLocally()) return;
  const username = session.username;
  submitting.value = true;
  try {
    await authApi.changePassword(
      props.forced ? undefined : model.currentPassword,
      model.newPassword,
    );
    await returnToLogin(username, '密码已修改，请使用新密码重新登录');
  } catch (error) {
    if (error instanceof ApiError && error.status < 500) {
      await handleApiError(error, username);
    } else {
      toast.warning(
        '网络请求未完成，无法确认密码是否已修改；请保留当前输入并重新登录验证，系统不会自动重试。',
      );
    }
  } finally {
    submitting.value = false;
  }
}

function confirmDiscard(): boolean {
  if (submitting.value) return false;
  if (!isDirty.value) return true;
  if (!window.confirm('密码表单包含未提交内容，确定放弃吗？')) return false;
  clearSensitiveFields();
  return true;
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return;
  event.preventDefault();
  event.returnValue = '';
}

onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload));
onBeforeUnmount(() => window.removeEventListener('beforeunload', handleBeforeUnload));

defineExpose({
  isDirty,
  submitting,
  confirmDiscard,
  clearSensitiveFields,
});
</script>

<template>
  <section data-password-change-form>
    <p class="mb-5 text-sm text-muted">
      修改成功后，当前账号的所有管理台会话都会退出；已有 API Key 不受影响。
    </p>

    <div class="grid gap-1">
      <label for="password-account" class="text-sm font-medium text-text">用户名</label>
      <CInput
        id="password-account"
        :model-value="session.username"
        readonly
        autocomplete="username"
      />
    </div>

    <div v-if="!forced" class="mt-3 grid gap-1">
      <label for="current-password" class="text-sm font-medium text-text">当前密码</label>
      <CInput
        id="current-password"
        type="password"
        :model-value="model.currentPassword"
        autocomplete="current-password"
        :error="Boolean(errors.currentPassword)"
        @update:model-value="updateCurrentPassword"
        @enter="submit"
      />
      <p v-if="errors.currentPassword" class="text-xs text-tone-error" role="alert">
        {{ errors.currentPassword }}
      </p>
    </div>

    <div class="mt-3 grid gap-1">
      <label for="new-password" class="text-sm font-medium text-text">新密码</label>
      <CInput
        id="new-password"
        type="password"
        :model-value="model.newPassword"
        autocomplete="new-password"
        :error="Boolean(errors.newPassword)"
        @update:model-value="updateNewPassword"
        @enter="submit"
      />
      <p
        v-if="errors.newPassword"
        data-new-password-error
        class="text-xs text-tone-error"
        role="alert"
      >
        {{ errors.newPassword }}
      </p>
    </div>

    <div class="mt-3 grid gap-1">
      <label for="password-confirmation" class="text-sm font-medium text-text">确认新密码</label>
      <CInput
        id="password-confirmation"
        type="password"
        :model-value="model.confirmation"
        autocomplete="new-password"
        :error="Boolean(errors.confirmation)"
        @update:model-value="updateConfirmation"
        @enter="submit"
      />
      <p
        v-if="errors.confirmation"
        data-new-password-error
        class="text-xs text-tone-error"
        role="alert"
      >
        {{ errors.confirmation }}
      </p>
    </div>

    <CButton
      class="mt-5"
      data-submit-password
      variant="primary"
      :loading="submitting"
      :disabled="submitting"
      @click="submit"
    >
      <template #icon><KeyRound :size="16" /></template>
      {{ forced ? '修改密码并重新登录' : '修改密码' }}
    </CButton>
  </section>
</template>
