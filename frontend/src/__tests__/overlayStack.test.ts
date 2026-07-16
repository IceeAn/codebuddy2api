import { afterEach, describe, expect, it, vi } from 'vitest';
import { registerOverlay } from '../components/ui/overlayStack';

describe('overlayStack', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = '';
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
  });

  it('隔离并恢复页面、无焦点控件时聚焦容器，且注销幂等', async () => {
    document.body.style.overflow = 'scroll';
    const opener = document.createElement('button');
    const background = document.createElement('main');
    background.setAttribute('aria-hidden', 'legacy');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const root = document.createElement('section');
    root.tabIndex = -1;
    document.body.append(opener, background, svg, root);
    opener.focus();
    const onEscape = vi.fn<() => void>();
    const unregister = registerOverlay({
      elements: [root],
      focusRoot: root,
      modal: true,
      onEscape,
    });

    expect(document.body.style.overflow).toBe('hidden');
    expect(background.inert).toBe(true);
    expect(background.getAttribute('aria-hidden')).toBe('true');
    expect(svg.getAttribute('aria-hidden')).toBeNull();
    expect(document.activeElement).toBe(root);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(document.activeElement).toBe(root);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onEscape).toHaveBeenCalledOnce();

    unregister();
    unregister();
    await Promise.resolve();
    expect(document.body.style.overflow).toBe('scroll');
    expect(background.inert).toBe(false);
    expect(background.getAttribute('aria-hidden')).toBe('legacy');
    expect(document.activeElement).toBe(opener);
  });

  it('只捕获顶层键盘，并覆盖中间焦点移动、非顶层注销和失效恢复点', async () => {
    const lower = document.createElement('section');
    const lowerButton = document.createElement('button');
    lower.append(lowerButton);
    const upper = document.createElement('section');
    const buttons = Array.from({ length: 3 }, () => document.createElement('button'));
    upper.append(...buttons);
    document.body.append(lower, upper);
    lowerButton.focus();
    const unregisterLower = registerOverlay({
      elements: [lower],
      focusRoot: lower,
      modal: false,
    });
    const unregisterUpper = registerOverlay({
      elements: [upper],
      focusRoot: upper,
      modal: false,
    });

    buttons[0].focus();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(document.activeElement).toBe(buttons[1]);
    buttons[2].focus();
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }),
    );
    expect(document.activeElement).toBe(buttons[1]);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    unregisterLower();
    lower.remove();
    unregisterUpper();
    await Promise.resolve();
    expect(document.body.style.overflow).toBe('');
  });

  it('活动元素不是 HTMLElement 时不保存恢复点', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const root = document.createElement('section');
    root.tabIndex = -1;
    document.body.append(svg, root);
    const activeSpy = vi.spyOn(document, 'activeElement', 'get').mockReturnValue(svg);
    const unregister = registerOverlay({ elements: [root], focusRoot: root, modal: false });
    activeSpy.mockRestore();
    expect(() => unregister()).not.toThrow();
  });

  it('锁定滚动时补偿滚动条宽度，嵌套浮层只补偿一次并完整恢复', () => {
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(
      window.innerWidth - 16,
    );
    document.body.style.paddingRight = '12px';
    const lower = document.createElement('section');
    const upper = document.createElement('section');
    lower.tabIndex = -1;
    upper.tabIndex = -1;
    document.body.append(lower, upper);

    const unregisterLower = registerOverlay({ elements: [lower], focusRoot: lower, modal: true });
    expect(document.body.style.paddingRight).toBe('28px');

    const unregisterUpper = registerOverlay({ elements: [upper], focusRoot: upper, modal: true });
    expect(document.body.style.paddingRight).toBe('28px');

    unregisterUpper();
    expect(document.body.style.paddingRight).toBe('28px');
    unregisterLower();
    expect(document.body.style.paddingRight).toBe('12px');
  });

  it('没有传统滚动条时不添加额外补偿', () => {
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(window.innerWidth);
    const root = document.createElement('section');
    root.tabIndex = -1;
    document.body.append(root);

    const unregister = registerOverlay({ elements: [root], focusRoot: root, modal: true });
    expect(document.body.style.paddingRight).toBe('');
    unregister();
  });
});
