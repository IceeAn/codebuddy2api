import { createRouter, createWebHashHistory } from 'vue-router';
import { useSessionStore } from './stores/session';

const DashboardView = () => import('./views/DashboardView.vue');
const StatsView = () => import('./views/StatsView.vue');
const CredentialsView = () => import('./views/CredentialsView.vue');
const ApiKeysView = () => import('./views/ApiKeysView.vue');
const ApiConsoleView = () => import('./views/ApiConsoleView.vue');
const ApiDocsView = () => import('./views/ApiDocsView.vue');
const SettingsView = () => import('./views/SettingsView.vue');
const NotFoundView = () => import('./views/NotFoundView.vue');

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

function updateDocumentTitle(to = router.currentRoute.value, session = useSessionStore()): void {
  if (session.ready && !session.authenticated) {
    document.title = '登录 · CodeBuddy2API';
    return;
  }
  if (session.ready && session.passwordChangeRequired) {
    document.title = '修改密码 · CodeBuddy2API';
    return;
  }
  const title = typeof to.meta.title === 'string' ? to.meta.title : '管理台';
  document.title = `${title} · CodeBuddy2API`;
}

let subscribedSession: ReturnType<typeof useSessionStore> | undefined;
let stopWatchingSession: (() => void) | undefined;

function subscribeToSessionTitle(session: ReturnType<typeof useSessionStore>): void {
  if (subscribedSession === session) return;
  stopWatchingSession?.();
  subscribedSession = session;
  stopWatchingSession = session.$subscribe(
    () => updateDocumentTitle(router.currentRoute.value, session),
    { detached: true },
  );
}

router.afterEach((to, _from, failure) => {
  const session = useSessionStore();
  subscribeToSessionTitle(session);
  if (failure) return;
  updateDocumentTitle(to, session);
});

export default router;
