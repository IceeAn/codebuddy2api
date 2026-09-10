<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { LogIn, X } from '@lucide/vue';
import { authApi } from '../api/admin';
import { ApiError } from '../api/client';
import CForm, { type FormRules } from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CInput from '../components/ui/CInput.vue';
import CButton from '../components/ui/CButton.vue';
import { useToast } from '../composables/useToast';
import { PROJECT_ICON_URL } from '../projectAssets';
import { useSessionStore } from '../stores/session';
import { createLoginSubmitter } from '../utils/loginSubmit';

const session = useSessionStore();
const toast = useToast();
const formRef = ref<InstanceType<typeof CForm> | null>(null);
const model = reactive({
  username: session.loginPrefillUsername,
  password: '',
});
const loading = ref(false);
const bootstrapRequired = ref(false);
const bootstrapExpired = ref(false);
const bootstrapNoticeDismissed = ref(false);
let bootstrapStatusVersion = 0;

onMounted(async () => {
  document.title = '登录 · CodeBuddy2API';
  const requestedAtVersion = bootstrapStatusVersion;
  try {
    const status = await authApi.bootstrapStatus();
    if (requestedAtVersion !== bootstrapStatusVersion) return;
    bootstrapRequired.value = status.bootstrap_required;
    bootstrapExpired.value = status.bootstrap_expired;
  } catch {
    // 状态提示是辅助信息；失败不能阻止用户尝试正式账号登录。
  }
});

const rules: FormRules = {
  username: { required: true, message: '请输入用户名', trigger: 'blur' },
  password: { required: true, message: '请输入密码', trigger: 'blur' },
};

const submit = createLoginSubmitter(
  (username, password) => session.login(username, password),
  () => {
    model.password = '';
  },
  (msg, error) => {
    if (
      error instanceof ApiError &&
      Reflect.get(Object(error.detail), 'error_code') === 'bootstrap_expired'
    ) {
      bootstrapStatusVersion += 1;
      bootstrapRequired.value = true;
      bootstrapExpired.value = true;
    }
    toast.error(msg);
  },
);

async function handleSubmit() {
  if (loading.value) return;
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  loading.value = true;
  try {
    if (session.passwordChangedFlash) session.dismissPasswordChangedFlash();
    await submit({
      username: model.username,
      password: model.password,
      isLoading: false,
    });
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main
    class="grid min-h-screen place-items-center bg-bg bg-[radial-gradient(ellipse_at_center,var(--color-brand-500)/0.08,transparent_70%)]"
  >
    <section
      class="w-[min(26rem,calc(100vw-2rem))] rounded-2xl border border-border bg-surface p-7 shadow-[var(--shadow-card-lg)]"
    >
      <div class="mb-6 flex items-center gap-3">
        <img class="project-icon h-12 w-12 shrink-0" :src="PROJECT_ICON_URL" alt="" />
        <div>
          <h1 class="font-display text-2xl font-bold text-text-strong">CodeBuddy2API</h1>
          <span class="text-sm text-muted">管理台</span>
        </div>
      </div>

      <div
        v-if="session.passwordChangedFlash"
        class="mb-4 flex items-start justify-between gap-3 rounded-md border border-success-500/30 bg-success-500/10 p-3 text-sm text-text"
        role="status"
      >
        <span>{{ session.passwordChangedFlash }}</span>
        <button
          type="button"
          class="shrink-0 text-muted hover:text-text"
          aria-label="关闭密码修改提示"
          @click="session.dismissPasswordChangedFlash()"
        >
          <X :size="16" />
        </button>
      </div>

      <div
        v-if="bootstrapRequired && bootstrapExpired"
        class="mb-4 rounded-md border border-error-500/30 bg-error-500/10 p-3 text-sm text-tone-error"
        role="alert"
      >
        初始账号已过期，请重启服务后重试。
      </div>
      <div
        v-else-if="bootstrapRequired && !bootstrapNoticeDismissed"
        class="mb-4 flex items-start justify-between gap-3 rounded-md border border-warning-500/30 bg-warning-500/10 p-3 text-sm text-text"
        role="status"
      >
        <span> 系统尚未初始化，请使用初始账号密码登录。初始账号仅在服务启动后 1 小时内有效。 </span>
        <button
          type="button"
          class="shrink-0 text-muted hover:text-text"
          aria-label="关闭初始账号提示"
          @click="bootstrapNoticeDismissed = true"
        >
          <X :size="16" />
        </button>
      </div>

      <CForm ref="formRef" :model="model" :rules="rules" label-placement="top">
        <CFormItem label="用户名" path="username">
          <CInput v-model="model.username" autocomplete="username" placeholder="用户名" autofocus />
        </CFormItem>
        <CFormItem label="密码" path="password">
          <CInput
            v-model="model.password"
            type="password"
            autocomplete="current-password"
            placeholder="密码"
            @enter="handleSubmit"
          />
        </CFormItem>
        <CButton
          class="mt-2"
          variant="primary"
          size="lg"
          block
          :loading="loading"
          :disabled="loading"
          @click="handleSubmit"
        >
          <template #icon>
            <LogIn :size="16" />
          </template>
          登录
        </CButton>
      </CForm>
    </section>
  </main>
</template>
