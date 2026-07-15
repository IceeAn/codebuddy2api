import { createRouter, createWebHashHistory } from 'vue-router';
import DashboardView from './views/DashboardView.vue';
import StatsView from './views/StatsView.vue';
import CredentialsView from './views/CredentialsView.vue';
import ApiKeysView from './views/ApiKeysView.vue';
import ApiConsoleView from './views/ApiConsoleView.vue';
import ApiDocsView from './views/ApiDocsView.vue';
import SettingsView from './views/SettingsView.vue';
import NotFoundView from './views/NotFoundView.vue';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: DashboardView, meta: { title: '总览' } },
    { path: '/stats', name: 'stats', component: StatsView, meta: { title: '统计' } },
    {
      path: '/credentials',
      name: 'credentials',
      component: CredentialsView,
      meta: { title: '凭证' },
    },
    { path: '/api-keys', name: 'api-keys', component: ApiKeysView, meta: { title: 'API Key' } },
    { path: '/console', name: 'console', component: ApiConsoleView, meta: { title: 'API 测试' } },
    {
      path: '/api-docs',
      name: 'api-docs',
      component: ApiDocsView,
      meta: { title: '开发文档' },
    },
    { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '设置' } },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: NotFoundView,
      meta: { title: '页面不存在' },
    },
  ],
});

router.afterEach((to, _from, failure) => {
  if (failure) return;
  const title = typeof to.meta.title === 'string' ? to.meta.title : '管理台';
  document.title = `${title} · CodeBuddy2API`;
});

export default router;
