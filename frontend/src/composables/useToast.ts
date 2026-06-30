import { useToastStore } from '../stores/toast';

export function useToast() {
  const store = useToastStore();

  function success(message: string, duration?: number): void {
    store.push('success', message, duration);
  }

  function error(message: string, duration?: number): void {
    store.push('error', message, duration);
  }

  function warning(message: string, duration?: number): void {
    store.push('warning', message, duration);
  }

  function info(message: string, duration?: number): void {
    store.push('info', message, duration);
  }

  return { success, error, warning, info };
}
