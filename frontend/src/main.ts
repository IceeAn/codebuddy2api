import '@fontsource/space-grotesk/latin-600.css';
import '@fontsource/space-grotesk/latin-700.css';
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { QueryClient, VueQueryPlugin, MutationCache, QueryCache } from '@tanstack/vue-query';
import App from './App.vue';
import router from './router';
import { isUnauthorizedError } from './api/client';
import { useToast } from './composables/useToast';
import './styles.css';

const app = createApp(App);

// Pinia 必须先于 QueryClient 注册：useToast 内部调用 useToastStore()，而
// QueryCache/MutationCache 的 onError 回调在运行时调用 useToast()，此时
// Pinia 必须已初始化，否则 store 无法激活。
app.use(createPinia());

function getErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const toast = useToast();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      networkMode: 'always',
      refetchOnReconnect: false,
      refetchOnWindowFocus: true,
    },
    mutations: {
      networkMode: 'always',
    },
  },
  queryCache: new QueryCache({
    onError: (err) => {
      // 401 由全局未授权 handler 处理，跳过错误提示
      if (isUnauthorizedError(err)) return;
      toast.error(getErrorMessage(err, '查询失败'));
    },
  }),
  mutationCache: new MutationCache({
    onError: (err) => {
      if (isUnauthorizedError(err)) return;
      toast.error(getErrorMessage(err, '操作失败'));
    },
  }),
});

app.use(router).use(VueQueryPlugin, { queryClient }).mount('#app');
