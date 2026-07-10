<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useQueryClient } from '@tanstack/vue-query';
import {
  Activity,
  BookOpen,
  ChartNoAxesCombined,
  KeyRound,
  LogOut,
  Menu as MenuIcon,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  TerminalSquare,
} from '@lucide/vue';
import LoginView from './views/LoginView.vue';
import CButton from './components/ui/CButton.vue';
import CSpin from './components/ui/CSpin.vue';
import CDrawer from './components/ui/CDrawer.vue';
import CToastHost from './components/CToastHost.vue';
import { useSessionStore } from './stores/session';
import { THEME_ICON_SWAP_DELAY_MS, useThemeStore } from './stores/theme';
import type { ThemeMode } from './stores/theme';
import { setUnauthorizedHandler } from './api/client';
const session = useSessionStore();
const theme = useThemeStore();
const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const PROJECT_ICON_URL = '/assets/codebuddy2api.svg';

const navItems = [
  { routeName: 'dashboard', label: '总览', icon: Activity },
  { routeName: 'stats', label: '统计', icon: ChartNoAxesCombined },
  { routeName: 'credentials', label: '凭证', icon: KeyRound },
  { routeName: 'api-keys', label: 'API Key', icon: ShieldCheck },
  { routeName: 'console', label: 'API 测试', icon: TerminalSquare },
  { routeName: 'api-docs', label: '开发文档', icon: BookOpen },
  { routeName: 'settings', label: '设置', icon: Settings },
];

const activeRoute = computed(() => String(route.name || 'dashboard'));

const pageTitle = computed(
  () => navItems.find((item) => item.routeName === activeRoute.value)?.label || '总览',
);
const routeKey = computed(() => route.fullPath || activeRoute.value);

/** 移动端导航：与 Tailwind md 断点保持一致，< 48rem 用 CDrawer 承载导航。 */
const mobileNavOpen = ref(false);
const isMobile = ref(false);
let mediaQuery: MediaQueryList | null = null;
const visibleThemeIconMode = ref<ThemeMode>(theme.mode);
let themeIconSwapTimer: number | undefined;

function updateMobile(event: MediaQueryListEvent | MediaQueryList): void {
  isMobile.value = !event.matches;
}

function navigateMobile(routeName: string): void {
  mobileNavOpen.value = false;
  router.push({ name: routeName });
}

/** 主题颜色渐变到前景/背景最低对比点后再切换图标，降低换形突兀感。 */
function scheduleThemeIconSwap(nextMode: ThemeMode): void {
  window.clearTimeout(themeIconSwapTimer);
  themeIconSwapTimer = window.setTimeout(() => {
    visibleThemeIconMode.value = nextMode;
    themeIconSwapTimer = undefined;
  }, THEME_ICON_SWAP_DELAY_MS);
}

onMounted(async () => {
  theme.init();
  visibleThemeIconMode.value = theme.mode;

  setUnauthorizedHandler(() => {
    void session.logout();
    queryClient.clear();
  });

  mediaQuery = window.matchMedia('(min-width: 48rem)');
  updateMobile(mediaQuery);
  mediaQuery.addEventListener('change', updateMobile);

  await session.restore();
});

onUnmounted(() => {
  mediaQuery!.removeEventListener('change', updateMobile);
  window.clearTimeout(themeIconSwapTimer);
  setUnauthorizedHandler(null);
});

async function logout() {
  await session.logout();
  queryClient.clear();
  await router.push('/');
}

function toggleTheme() {
  const nextMode = theme.mode === 'dark' ? 'light' : 'dark';
  theme.toggle();
  scheduleThemeIconSwap(nextMode);
}
</script>

<template>
  <div v-if="!session.ready" class="boot-screen grid min-h-screen place-items-center bg-bg">
    <CSpin size="lg" />
  </div>

  <LoginView v-else-if="!session.authenticated" />

  <div v-else class="flex min-h-screen bg-bg">
    <aside
      v-if="!isMobile"
      class="sidebar sticky top-0 flex h-screen w-[15.5rem] shrink-0 flex-col border-r border-rail-border bg-rail"
    >
      <div class="flex h-16 items-center gap-3 border-b border-rail-border px-5">
        <img class="project-icon h-9 w-9 shrink-0" :src="PROJECT_ICON_URL" alt="" />
        <div class="min-w-0">
          <div class="truncate font-display text-[15px] font-bold text-rail-text-strong">
            CodeBuddy2API
          </div>
          <div class="truncate text-xs text-rail-muted">{{ session.username }}</div>
        </div>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto p-3" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.routeName"
          :class="[
            'flex h-10 w-full items-center gap-3 rounded-md px-3 text-sm transition-colors',
            activeRoute === item.routeName
              ? 'bg-rail-active text-rail-active-text shadow-[inset_3px_0_0_0_var(--color-rail-active-indicator)]'
              : 'text-rail-text hover:bg-rail-hover hover:text-rail-text-strong',
          ]"
          :aria-current="activeRoute === item.routeName ? 'page' : undefined"
          type="button"
          @click="router.push({ name: item.routeName })"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </button>
      </nav>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="topbar sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface/75 px-5 backdrop-blur-md md:h-16 md:px-7"
      >
        <div class="flex items-center gap-2.5">
          <button
            v-if="isMobile"
            class="hamburger grid h-9 w-9 place-items-center rounded-md text-text hover:bg-surface-2"
            type="button"
            aria-label="打开导航菜单"
            @click="mobileNavOpen = true"
          >
            <MenuIcon :size="20" />
          </button>
          <div>
            <div class="hidden text-xs text-muted md:block">管理台</div>
            <h1 class="font-display text-base font-semibold text-text-strong md:text-lg">
              {{ pageTitle }}
            </h1>
          </div>
        </div>
        <div class="flex items-center gap-2.5">
          <CButton
            shape="circle"
            variant="ghost"
            :aria-label="theme.mode === 'light' ? '切换到暗色模式' : '切换到亮色模式'"
            @click="toggleTheme"
          >
            <template #icon>
              <Moon v-if="visibleThemeIconMode === 'light'" :size="18" />
              <Sun v-else :size="18" />
            </template>
          </CButton>
          <CButton variant="secondary" aria-label="退出登录" @click="logout">
            <template #icon>
              <LogOut :size="16" />
            </template>
            退出
          </CButton>
        </div>
      </header>

      <main class="mx-auto w-full max-w-[90rem] px-5 py-6 md:px-7">
        <div class="page-transition-frame">
          <RouterView v-slot="{ Component }">
            <Transition name="page" mode="out-in">
              <component :is="Component" :key="routeKey" />
            </Transition>
          </RouterView>
        </div>
      </main>
    </div>

    <CDrawer v-model:open="mobileNavOpen" placement="left" :width="244" title="CodeBuddy2API">
      <nav class="space-y-1" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.routeName"
          :class="[
            'flex h-10 w-full items-center gap-3 rounded-md px-3 text-sm transition-colors',
            activeRoute === item.routeName
              ? 'bg-brand-500/12 text-brand-700 shadow-[inset_3px_0_0_0_var(--color-brand-400)] dark:text-brand-300'
              : 'text-text hover:bg-surface-2 hover:text-text-strong',
          ]"
          :aria-current="activeRoute === item.routeName ? 'page' : undefined"
          type="button"
          @click="navigateMobile(item.routeName)"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </button>
      </nav>
    </CDrawer>
  </div>

  <CToastHost />
</template>
