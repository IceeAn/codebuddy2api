import { ref } from 'vue';
import { useToast } from './useToast';

/**
 * 优先使用安全上下文可用的 `navigator.clipboard.writeText`，不可用时降级为
 * 临时 textarea + `document.execCommand('copy')`。失败会提示用户并返回 false。
 */
export function useClipboard() {
  const toast = useToast();
  const copied = ref(false);
  let copiedTimer: ReturnType<typeof setTimeout> | null = null;

  async function copy(text: string, successMsg = '已复制'): Promise<boolean> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        const previousFocus = document.activeElement as HTMLElement | null;
        let appended = false;
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        try {
          document.body.appendChild(textarea);
          appended = true;
          textarea.select();
          if (!document.execCommand('copy')) throw new Error('复制失败');
        } finally {
          if (appended) document.body.removeChild(textarea);
          previousFocus?.focus();
        }
      }
      copied.value = true;
      if (copiedTimer !== null) clearTimeout(copiedTimer);
      copiedTimer = setTimeout(() => {
        copied.value = false;
        copiedTimer = null;
      }, 2000);
      toast.success(successMsg);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '复制失败');
      return false;
    }
  }

  return { copied, copy };
}
