<script setup lang="ts">
import { reactive, ref } from 'vue';
import { LogIn } from '@lucide/vue';
import CForm, { type FormRules } from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CInput from '../components/ui/CInput.vue';
import CButton from '../components/ui/CButton.vue';
import { useToast } from '../composables/useToast';
import { useSessionStore } from '../stores/session';
import { createLoginSubmitter } from '../utils/loginSubmit';

const session = useSessionStore();
const toast = useToast();
const formRef = ref<InstanceType<typeof CForm> | null>(null);
const PROJECT_ICON_URL = '/assets/codebuddy2api.svg';
const model = reactive({
  username: '',
  password: '',
});
const loading = ref(false);

const rules: FormRules = {
  username: { required: true, message: '请输入用户名', trigger: 'blur' },
  password: { required: true, message: '请输入密码', trigger: 'blur' },
};

const submit = createLoginSubmitter(
  (username, password) => session.login(username, password),
  () => {
    model.password = '';
  },
  (msg) => toast.error(msg),
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
