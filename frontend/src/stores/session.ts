import { defineStore } from 'pinia';
import { authApi } from '../api/admin';
import { ApiError } from '../api/client';

/** restore 超时阈值（毫秒），避免 session() hang 时 boot-screen 永久 spinner */
const RESTORE_TIMEOUT_MS = 10_000;

export const useSessionStore = defineStore('session', {
  state: () => ({
    authenticated: false,
    username: '',
    source: '',
    ready: false,
    restoreError: '',
    restoring: false,
  }),
  actions: {
    endLocalSession() {
      this.authenticated = false;
      this.username = '';
      this.source = '';
      this.restoreError = '';
    },
    async restore() {
      if (this.restoring) return;
      this.ready = false;
      this.restoring = true;
      this.restoreError = '';
      const controller = new AbortController();
      let timedOut = false;
      const timeoutId = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, RESTORE_TIMEOUT_MS);
      try {
        const session = await authApi.session(controller.signal);
        this.authenticated = true;
        this.username = session.username;
        this.source = session.source || '';
      } catch (error) {
        this.authenticated = false;
        this.username = '';
        this.source = '';
        if (error instanceof ApiError && error.status === 401 && error.isUnauthorized) {
          this.restoreError = '';
        } else if (timedOut) {
          this.restoreError = '登录状态确认超时，请重试';
        } else if (error instanceof ApiError) {
          this.restoreError = '无法确认登录状态，请稍后重试';
        } else {
          this.restoreError = '无法确认登录状态，请检查网络后重试';
        }
      } finally {
        window.clearTimeout(timeoutId);
        this.ready = true;
        this.restoring = false;
      }
    },
    async login(username: string, password: string) {
      const session = await authApi.login(username, password);
      this.authenticated = true;
      this.username = session.username;
      this.source = session.source || 'session_cookie';
      this.restoreError = '';
      this.ready = true;
    },
    async logout() {
      try {
        await authApi.logout();
      } finally {
        this.endLocalSession();
      }
    },
  },
});
