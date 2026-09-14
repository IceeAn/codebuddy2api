import { describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { tokenSegments, tokenColor } from '../utils/tokenizerVisualization';
import TokenizerVisualization from '../components/TokenizerVisualization.vue';

describe('token 文字可视化', () => {
  it('保持三字节文字完整，以 2:1 的硬边界背景显示两个 token', () => {
    const segments = tokenSegments('你', [
      { id: 256, start: 0, end: 2 },
      { id: 160, start: 2, end: 3 },
    ]);
    expect(segments).toHaveLength(1);
    expect(segments[0].text).toBe('你');
    expect(segments[0].background).toBe(
      `linear-gradient(to right, ${tokenColor(0)} 0% 66.66666666666666%, ${tokenColor(1)} 66.66666666666666% 100%)`,
    );
    expect(segments[0].title).toBe(
      'Token 1 · ID 256（2 字节/3 字节）\nToken 2 · ID 160（1 字节/3 字节）',
    );
  });

  it('支持三段拆字、跨字 token、emoji 和组合字符，不生成替代符', () => {
    const text = '你🙂e\u0301👩‍💻';
    const size = new TextEncoder().encode(text).length;
    const segments = tokenSegments(
      text,
      Array.from({ length: size }, (_, i) => ({
        id: i,
        start: i,
        end: i + 1,
      })),
    );
    expect(segments.map((segment) => segment.text)).toEqual(['你', '🙂', 'e\u0301', '👩‍💻']);
    expect(segments[0].background).toContain('33.33333333333333%');
    expect(segments[1].background).toContain('25%');
    expect(segments[1].title).toContain('（1 字节/4 字节）');
    expect(segments.map((segment) => segment.text).join('')).toBe(text);
    const spanning = tokenSegments('你好', [
      { id: 1, start: 0, end: 4 },
      { id: 2, start: 4, end: 6 },
    ]);
    expect(spanning[0].background).toBe(tokenColor(0));
    expect(spanning[1].title).toContain('ID 1（1 字节/3 字节）');
  });

  it('合并同一 token 的文字，保留空白和未编码区间，跳过零长度 token', () => {
    const text = ' hello  world\n';
    const segments = tokenSegments(text, [
      { id: 0, start: 0, end: 0 },
      { id: 1, start: 1, end: 6 },
      { id: 2, start: 8, end: 13 },
    ]);
    expect(segments.map((segment) => segment.text)).toEqual([' ', 'hello', '  ', 'world', '\n']);
    expect(segments[1].title).toBe('Token 2 · ID 1');
    expect(segments[0].background).toBe('transparent');
    expect(tokenSegments('', [])).toEqual([]);
    expect(tokenSegments(' \n', [])[0].text).toBe(' \n');
    expect(tokenSegments('e\u0301', [{ id: 1, start: 1, end: 3 }])[0].background).toContain(
      'transparent',
    );
    expect(tokenColor(8)).toBe(tokenColor(0));
  });

  it('显示统计与完整文字，支持切换 token ID 和空结果', async () => {
    const wrapper = mount(TokenizerVisualization, {
      props: {
        result: {
          text: '你',
          input_tokens: 2,
          tokens: [
            { id: 256, start: 0, end: 2 },
            { id: 160, start: 2, end: 3 },
          ],
        },
      },
    });
    expect(wrapper.get('[data-testid="token-text"]').text()).toBe('你');
    expect(wrapper.text()).toContain('2 tokens');
    expect(wrapper.text()).toContain('1 字符');
    await wrapper.get('button[aria-label="显示 Token ID"]').trigger('click');
    expect(wrapper.get('[data-testid="token-ids"]').text()).toContain('256');
    await wrapper.get('button[aria-label="显示文字"]').trigger('click');
    await wrapper.setProps({ result: { text: '', input_tokens: 0, tokens: [] } });
    expect(wrapper.text()).toContain('没有可显示的文字');
    wrapper.unmount();
  });

  it('使用自定义悬浮提示，移出后关闭且不生成系统 title 提示', async () => {
    vi.useFakeTimers();
    const wrapper = mount(TokenizerVisualization, {
      attachTo: document.body,
      props: {
        result: {
          text: '🙂',
          input_tokens: 4,
          tokens: [175, 106, 105, 249].map((id, index) => ({ id, start: index, end: index + 1 })),
        },
      },
    });
    try {
      const trigger = wrapper.get('[data-testid="token-text"] > span');
      expect(wrapper.find('[title]').exists()).toBe(false);
      await trigger.trigger('mouseenter');
      await vi.advanceTimersByTimeAsync(300);
      await flushPromises();
      const tooltip = document.querySelector('[role="tooltip"]');
      expect(tooltip?.textContent).toBe(
        'Token 1 · ID 175（1 字节/4 字节）\nToken 2 · ID 106（1 字节/4 字节）\nToken 3 · ID 105（1 字节/4 字节）\nToken 4 · ID 249（1 字节/4 字节）',
      );
      expect(tooltip?.querySelector('.whitespace-pre-line')).not.toBeNull();
      expect(trigger.attributes('aria-describedby')).toBe(tooltip?.id);
      await trigger.trigger('mouseleave');
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
      await wrapper.setProps({
        result: {
          text: ' hello\n  world ',
          input_tokens: 2,
          tokens: [
            { id: 1, start: 1, end: 6 },
            { id: 2, start: 9, end: 14 },
          ],
        },
      });
      expect(wrapper.get('[data-testid="token-text"]').element.textContent).toBe(
        ' hello\n  world ',
      );
    } finally {
      wrapper.unmount();
      vi.useRealTimers();
    }
  });
});
