<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { LogOut } from '@lucide/vue';
import { useQueryClient } from '@tanstack/vue-query';
import PasswordChangeForm from '../components/PasswordChangeForm.vue';
import CButton from '../components/ui/CButton.vue';
import { useSessionStore } from '../stores/session';

interface PasswordFormExpose {
  confirmDiscard: () => boolean;
}

const session = useSessionStore();
const queryClient = useQueryClient();
const passwordForm = ref<PasswordFormExpose | null>(null);

onMounted(() => {
  document.title = '修改密码 · CodeBuddy2API';
});

async function logout(): Promise<void> {
  if (passwordForm.value && !passwordForm.value.confirmDiscard()) return;
  try {
    await session.logout();
  } finally {
    queryClient.clear();
  }
}
</script>

<template>
  <main class="grid min-h-screen place-items-center bg-bg px-4 py-8">
    <section
      class="w-full max-w-lg rounded-2xl border border-border bg-surface p-7 shadow-[var(--shadow-card-lg)]"
    >
      <div class="mb-6">
        <h1 class="font-display text-2xl font-bold text-text-strong">首次登录后必须修改密码</h1>
        <p class="mt-2 text-sm text-muted">
          初始账号仅在本次服务启动后 1 小时内有效，请立即设置仅由你掌握的新密码。
        </p>
      </div>

      <PasswordChangeForm ref="passwordForm" forced />

      <div class="mt-6 border-t border-border pt-4">
        <CButton variant="secondary" @click="logout">
          <template #icon><LogOut :size="16" /></template>
          退出登录
        </CButton>
      </div>
    </section>
  </main>
</template>
