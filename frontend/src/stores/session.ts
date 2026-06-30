import { defineStore } from 'pinia';
import { authApi } from '../api/admin';

/** restore 超时阈值（毫秒），避免 session() hang 时 boot-screen 永久 spinner */
const RESTORE_TIMEOUT_MS = 10_000;

function timeout(ms: number): Promise<never> {
  return new Promise<never>((_, reject) => setTimeout(() => reject(new Error('timeout')), ms));
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    authenticated: false,
    username: '',
    source: '',
    ready: false,
  }),
  actions: {
    async restore() {
      try {
        const session = await Promise.race([authApi.session(), timeout(RESTORE_TIMEOUT_MS)]);
        this.authenticated = true;
        this.username = session.username;
        this.source = session.source || '';
      } catch {
        this.authenticated = false;
        this.username = '';
        this.source = '';
      } finally {
        this.ready = true;
      }
    },
    async login(username: string, password: string) {
      const session = await authApi.login(username, password);
      this.authenticated = true;
      this.username = session.username;
      this.source = session.source || 'session_cookie';
    },
    async logout() {
      try {
        await authApi.logout();
      } finally {
        this.authenticated = false;
        this.username = '';
        this.source = '';
      }
    },
  },
});
