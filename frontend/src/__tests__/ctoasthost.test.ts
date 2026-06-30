import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import CToastHost from '../components/CToastHost.vue';
import { useToastStore } from '../stores/toast';

function textOf(selector: string): string[] {
  return Array.from(document.body.querySelectorAll(selector)).map((item) => item.textContent || '');
}

describe('CToastHost', () => {
  let wrapper: ReturnType<typeof mount> | null = null;

  beforeEach(() => {
    setActivePinia(createPinia());
    document.body.innerHTML = '';
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    document.body.innerHTML = '';
  });

  it('没有 toast 时保留提示宿主但不渲染提示项', () => {
    wrapper = mount(CToastHost);

    expect(document.body.querySelector('.toast-host')).not.toBeNull();
    expect(document.body.querySelectorAll('.toast-item')).toHaveLength(0);
  });

  it('渲染所有类型 toast，并按严重程度设置可访问 role', async () => {
    const store = useToastStore();
    store.push('success', '保存成功', 0);
    store.push('info', '正在处理', 0);
    store.push('warning', '请检查配置', 0);
    store.push('error', '保存失败', 0);

    wrapper = mount(CToastHost);
    await nextTick();

    const host = document.body.querySelector('.toast-host');
    expect(host?.getAttribute('aria-live')).toBe('polite');
    expect(host?.className).toContain('pointer-events-none');

    const items = Array.from(document.body.querySelectorAll('.toast-item'));
    expect(items).toHaveLength(4);
    expect(textOf('.toast-title')).toEqual(['已完成', '提示', '注意', '操作失败']);
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('保存成功'),
      expect.stringContaining('正在处理'),
      expect.stringContaining('请检查配置'),
      expect.stringContaining('保存失败'),
    ]);
    expect(items.map((item) => item.getAttribute('role'))).toEqual([
      'status',
      'status',
      'alert',
      'alert',
    ]);
    expect(items[0].className).toContain('toast-success');
    expect(items[1].className).toContain('toast-info');
    expect(items[2].className).toContain('toast-warning');
    expect(items[3].className).toContain('toast-error');
    expect(document.body.querySelectorAll('.toast-accent')).toHaveLength(0);
    expect(items.every((item) => item.className.includes('toast-item'))).toBe(true);
    expect(document.body.querySelectorAll('.toast-icon-shell')).toHaveLength(4);
    expect(document.body.querySelectorAll('.toast-progress')).toHaveLength(0);
  });

  it('首条 toast 在宿主已挂载后插入，确保也触发列表进场动画', async () => {
    const store = useToastStore();
    wrapper = mount(CToastHost);
    await nextTick();

    expect(document.body.querySelector('.toast-host')).not.toBeNull();
    expect(document.body.querySelectorAll('.toast-item')).toHaveLength(0);

    store.push('warning', '请输入 API Key 名称', 0);
    await nextTick();

    expect(document.body.querySelectorAll('.toast-item')).toHaveLength(1);
  });

  it('自动消失的 toast 显示进度条，并把时长传给 CSS 动画', async () => {
    const store = useToastStore();
    store.push('success', '会自动关闭', 2500);
    wrapper = mount(CToastHost);
    await nextTick();

    const progress = document.body.querySelector<HTMLElement>('.toast-progress');
    const bar = document.body.querySelector<HTMLElement>('.toast-progress-bar');

    expect(progress).not.toBeNull();
    expect(bar).not.toBeNull();
    expect(bar?.style.getPropertyValue('--toast-duration')).toBe('2500ms');
    expect(bar?.className).toContain('origin-center');
    expect(bar?.className).not.toContain('origin-left');
  });

  it('鼠标悬浮在任意 toast 上会暂停所有进度条和自动关闭计时', async () => {
    const store = useToastStore();
    store.push('success', 'first', 2500);
    store.push('error', 'second', 2500);
    wrapper = mount(CToastHost);
    await nextTick();

    const host = document.body.querySelector<HTMLElement>('.toast-host');
    const list = document.body.querySelector<HTMLElement>('.toast-list');
    const items = document.body.querySelectorAll<HTMLElement>('.toast-item');
    expect(items).toHaveLength(2);
    expect(list).not.toBeNull();

    list!.dispatchEvent(new MouseEvent('mouseenter'));
    await nextTick();

    expect(store.isPaused).toBe(true);
    expect(host?.className).toContain('toast-paused');

    list!.dispatchEvent(new MouseEvent('mouseleave'));
    await nextTick();

    expect(store.isPaused).toBe(false);
    expect(host?.className).not.toContain('toast-paused');
  });

  it('点击关闭按钮移除对应 toast', async () => {
    const store = useToastStore();
    store.push('info', '可关闭提示', 0);
    wrapper = mount(CToastHost);
    await nextTick();

    const closeButton = document.body.querySelector<HTMLButtonElement>(
      'button[aria-label="关闭提示"]',
    );
    expect(closeButton).not.toBeNull();

    closeButton!.click();
    await nextTick();

    expect(store.toasts).toHaveLength(0);
    expect(document.body.querySelector('.toast-host')).not.toBeNull();
    expect(document.body.querySelectorAll('.toast-item')).toHaveLength(0);
  });
});
