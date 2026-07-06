import { createRouter, createWebHashHistory } from 'vue-router';
import DashboardView from './views/DashboardView.vue';
import CredentialsView from './views/CredentialsView.vue';
import ApiKeysView from './views/ApiKeysView.vue';
import ApiConsoleView from './views/ApiConsoleView.vue';
import ApiDocsView from './views/ApiDocsView.vue';
import SettingsView from './views/SettingsView.vue';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: DashboardView },
    { path: '/credentials', name: 'credentials', component: CredentialsView },
    { path: '/api-keys', name: 'api-keys', component: ApiKeysView },
    { path: '/console', name: 'console', component: ApiConsoleView },
    { path: '/api-docs', name: 'api-docs', component: ApiDocsView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
});

export default router;
