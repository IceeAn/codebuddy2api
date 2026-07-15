import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { h } from 'vue';
import CDataTable, { type Column } from '../components/ui/CDataTable.vue';

const rowKeys = new WeakMap<Record<string, unknown>, number>();
let nextRowKey = 0;

function stableTestRowKey(row: Record<string, unknown>): number {
  const existing = rowKeys.get(row);
  if (existing !== undefined) return existing;
  const created = nextRowKey++;
  rowKeys.set(row, created);
  return created;
}

describe('CDataTable', () => {
  it('渲染 table 元素', () => {
    const wrapper = mount(CDataTable, {
      props: { rowKey: stableTestRowKey, columns: [{ title: '名称', key: 'name' }], data: [] },
    });
    expect(wrapper.find('table').exists()).toBe(true);
  });

  it('定位容器与横向滚动容器分离', () => {
    const wrapper = mount(CDataTable, {
      props: { rowKey: stableTestRowKey, columns: [{ title: '名称', key: 'name' }], data: [] },
    });
    const container = wrapper.find('.c-data-table');
    expect(container.classes()).toContain('rounded-lg');
    expect(container.classes()).toContain('border');
    expect(container.classes()).toContain('relative');
    expect(container.classes()).not.toContain('overflow-x-auto');
    expect(wrapper.find('.c-data-table-scroll').classes()).toContain('overflow-x-auto');
  });

  it('渲染表头 thead 与 th 文本', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          { title: '名称', key: 'name' },
          { title: '年龄', key: 'age' },
        ],
        data: [],
      },
    });
    const ths = wrapper.findAll('thead th');
    expect(ths.length).toBe(2);
    expect(ths[0].text()).toBe('名称');
    expect(ths[1].text()).toBe('年龄');
  });

  it('表头含 h-10 与 uppercase class', () => {
    const wrapper = mount(CDataTable, {
      props: { rowKey: stableTestRowKey, columns: [{ title: '名称', key: 'name' }], data: [] },
    });
    const th = wrapper.find('thead th');
    expect(th.classes()).toContain('h-10');
    expect(th.classes()).toContain('uppercase');
    expect(th.classes()).toContain('font-semibold');
    expect(th.classes()).toContain('text-muted');
  });

  it('渲染数据行（row[key]）', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [
          { name: 'Alice', age: 30 },
          { name: 'Bob', age: 25 },
        ],
      },
    });
    const rows = wrapper.findAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].text()).toContain('Alice');
    expect(rows[1].text()).toContain('Bob');
  });

  it('render 函数渲染自定义内容', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          {
            title: '操作',
            key: 'actions',
            render: (row) => h('button', { class: 'btn' }, `编辑-${row.id}`),
          },
        ],
        data: [{ id: '1' }],
      },
    });
    const btn = wrapper.find('tbody button');
    expect(btn.exists()).toBe(true);
    expect(btn.text()).toBe('编辑-1');
  });

  it('render 返回字符串直接显示', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          {
            title: '时间',
            key: 'time',
            render: (row) => `T-${row.ms}`,
          },
        ],
        data: [{ ms: 100 }],
      },
    });
    expect(wrapper.find('tbody td').text()).toBe('T-100');
  });

  it('width 透传到 th style', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', width: 120 }],
        data: [],
      },
    });
    const th = wrapper.find('thead th');
    expect(th.attributes('style')).toContain('width');
    expect(th.attributes('style')).toContain('120px');
  });

  it('minWidth 透传到 th style（兼容现有用法）', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', minWidth: 160 }],
        data: [],
      },
    });
    const th = wrapper.find('thead th');
    expect(th.attributes('style')).toContain('min-width');
    expect(th.attributes('style')).toContain('160px');
  });

  it('align=right 时 th/td 含 text-right', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '数量', key: 'count', align: 'right' }],
        data: [{ count: 5 }],
      },
    });
    expect(wrapper.find('thead th').classes()).toContain('text-right');
    expect(wrapper.find('tbody td').classes()).toContain('text-right');
  });

  it('headerClassName 只应用到 th，不影响 td', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '操作', key: 'actions', headerClassName: 'table-action-header' }],
        data: [{ actions: '删除' }],
      },
    });
    expect(wrapper.find('thead th').classes()).toContain('table-action-header');
    expect(wrapper.find('tbody td').classes()).not.toContain('table-action-header');
  });

  it('align=left（默认）时 th/td 含 text-left', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('thead th').classes()).toContain('text-left');
    expect(wrapper.find('tbody td').classes()).toContain('text-left');
  });

  it('align=center 时 th/td 含 text-center', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', align: 'center' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('thead th').classes()).toContain('text-center');
    expect(wrapper.find('tbody td').classes()).toContain('text-center');
  });

  it('ellipsis=true 时 td 含 truncate 与 max-w-0', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', ellipsis: true }],
        data: [{ name: 'A' }],
      },
    });
    const td = wrapper.find('tbody td');
    expect(td.classes()).toContain('truncate');
    expect(td.classes()).toContain('max-w-0');
    expect(td.classes()).toContain('overflow-hidden');
  });

  it('ellipsis={tooltip:true} 时用 CTooltip 包裹', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', ellipsis: { tooltip: true } }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('tbody td span.relative').exists()).toBe(true);
  });

  it('ellipsis={tooltip:true} 时文本节点自身可收缩并显示省略号', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '文件', key: 'filename', ellipsis: { tooltip: true } }],
        data: [{ filename: 'codebuddy_token_extremely_long_filename_without_breaks.json' }],
      },
    });

    const tooltipTrigger = wrapper.find('tbody td span.relative');
    const text = tooltipTrigger.find('span.truncate');
    expect(tooltipTrigger.classes()).toContain('w-full');
    expect(tooltipTrigger.classes()).toContain('min-w-0');
    expect(tooltipTrigger.classes()).toContain('max-w-full');
    expect(text.exists()).toBe(true);
    expect(text.classes()).toContain('block');
    expect(text.classes()).toContain('min-w-0');
    expect(text.classes()).toContain('max-w-full');
  });

  it('ellipsis={tooltip:false} 时不包裹 CTooltip 但仍 truncate', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', ellipsis: { tooltip: false } }],
        data: [{ name: 'A' }],
      },
    });
    const td = wrapper.find('tbody td');
    expect(td.classes()).toContain('truncate');
    expect(td.find('span.relative').exists()).toBe(false);
  });

  it('行 hover 含 hover:bg-surface-2', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
      },
    });
    const tr = wrapper.find('tbody tr');
    expect(tr.classes()).toContain('hover:bg-surface-2');
    expect(tr.classes()).toContain('h-11');
  });

  it('空数据渲染默认空状态', () => {
    const wrapper = mount(CDataTable, {
      props: { rowKey: stableTestRowKey, columns: [{ title: '名称', key: 'name' }], data: [] },
    });
    const empty = wrapper.find('.c-data-table-empty');
    expect(empty.exists()).toBe(true);
    expect(empty.classes()).toContain('min-h-24');
    expect(empty.classes()).toContain('place-items-center');
    expect(empty.classes()).toContain('px-4');
    expect(empty.classes()).toContain('py-8');
  });

  it('empty slot 覆盖默认空状态', () => {
    const wrapper = mount(CDataTable, {
      props: { rowKey: stableTestRowKey, columns: [{ title: '名称', key: 'name' }], data: [] },
      slots: { empty: '自定义空状态' },
    });
    expect(wrapper.text()).toContain('自定义空状态');
  });

  it('loading=true 时遮罩正文、保留数据行与无障碍说明', async () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
        loading: true,
      },
    });
    const loading = wrapper.find('.c-data-table-loading');
    expect(loading.exists()).toBe(true);
    expect(loading.attributes('role')).toBe('status');
    expect(loading.attributes('aria-label')).toBe('正在加载');
    expect(loading.text()).toBe('');
    expect(loading.find('[aria-hidden="true"]').exists()).toBe(true);
    expect(wrapper.findAll('tbody tr')).toHaveLength(1);

    await wrapper.setProps({ loading: false });
    expect(wrapper.findAll('tbody tr')).toHaveLength(1);
  });

  it('loading=false 时不渲染覆盖层', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
        loading: false,
      },
    });
    expect(wrapper.find('.c-data-table-loading').exists()).toBe(false);
  });

  it('loading=true 时遮罩立即出现，并在请求结束后至少显示满 300ms', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    try {
      const wrapper = mount(CDataTable, {
        props: {
          rowKey: stableTestRowKey,
          columns: [{ title: '名称', key: 'name' }],
          data: [],
          loading: false,
        },
      });

      await wrapper.setProps({ loading: true });
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(true);

      vi.advanceTimersByTime(100);
      await wrapper.setProps({ loading: false });
      await vi.advanceTimersByTimeAsync(199);
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(true);

      await vi.advanceTimersByTimeAsync(1);
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(false);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('loading 已持续 300ms 时，请求结束后立即开始隐藏遮罩', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    try {
      const wrapper = mount(CDataTable, {
        props: {
          rowKey: stableTestRowKey,
          columns: [{ title: '名称', key: 'name' }],
          data: [],
          loading: true,
        },
      });

      vi.advanceTimersByTime(300);
      await wrapper.setProps({ loading: false });
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(false);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('最低显示时间内重新 loading 时取消旧隐藏计时并重新计时', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    try {
      const wrapper = mount(CDataTable, {
        props: {
          rowKey: stableTestRowKey,
          columns: [{ title: '名称', key: 'name' }],
          data: [],
          loading: true,
        },
      });

      vi.advanceTimersByTime(100);
      await wrapper.setProps({ loading: false });
      vi.advanceTimersByTime(100);
      await wrapper.setProps({ loading: true });
      await vi.advanceTimersByTimeAsync(100);
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(true);

      await wrapper.setProps({ loading: false });
      await vi.advanceTimersByTimeAsync(199);
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(true);
      await vi.advanceTimersByTimeAsync(1);
      expect(wrapper.find('.c-data-table-loading').exists()).toBe(false);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('loading 遮罩使用透明度过渡', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        loading: true,
      },
    });

    expect(wrapper.find('transition-stub').attributes('name')).toBe('c-data-table-loading');
  });

  it('loading 遮罩从表头下方开始并覆盖表格正文', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
        loading: true,
      },
    });
    const loading = wrapper.find('.c-data-table-loading');
    expect(loading.classes()).toContain('absolute');
    expect(loading.classes()).toContain('top-10');
    expect(loading.classes()).toContain('bottom-0');
    expect(loading.classes()).toContain('bg-surface/60');
    expect(loading.classes()).toContain('backdrop-blur-[1px]');
  });

  it('loading 指示器的长短列表定位交由容器响应式样式处理', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: Array.from({ length: 100 }, (_, index) => ({ name: `row-${index}` })),
        loading: true,
      },
    });

    const loading = wrapper.find('.c-data-table-loading');
    const indicator = wrapper.find('.c-data-table-loading-indicator');
    expect(loading.classes()).not.toContain('place-items-center');
    expect(indicator.classes()).not.toContain('sticky');
    expect(indicator.classes()).not.toContain('top-[calc(50vh-0.875rem)]');
  });

  it('loading 且无数据时预留最小正文高度', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        loading: true,
      },
    });

    expect(wrapper.find('.c-data-table-loading-spacer').classes()).toContain('min-h-24');
  });

  it('容器通过 aria-busy 暴露加载状态', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        loading: true,
      },
    });

    expect(wrapper.find('.c-data-table').attributes('aria-busy')).toBe('true');
    await wrapper.setProps({ loading: false });
    expect(wrapper.find('.c-data-table').attributes('aria-busy')).toBe('true');
    await vi.advanceTimersByTimeAsync(300);
    expect(wrapper.find('.c-data-table').attributes('aria-busy')).toBe('false');
    wrapper.unmount();
    vi.useRealTimers();
  });

  it('error=true 且无数据时不显示空状态', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        error: true,
      },
    });

    expect(wrapper.find('.c-data-table-empty').exists()).toBe(false);
    expect(wrapper.find('.c-data-table-loading').exists()).toBe(false);
  });

  it('size=small 时行高 h-9', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
        size: 'small',
      },
    });
    const tr = wrapper.find('tbody tr');
    expect(tr.classes()).toContain('h-9');
  });

  it('size=default（默认）时行高 h-11', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('tbody tr').classes()).toContain('h-11');
  });

  it('className 透传到 td', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', className: 'mono' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('tbody td').classes()).toContain('mono');
  });

  it('row 缺少 key 时显示空', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'missing' }],
        data: [{ other: 'A' }],
      },
    });
    expect(wrapper.find('tbody td').text()).toBe('');
  });

  it('Column 类型可导出使用', () => {
    const cols: Column[] = [{ title: 'A', key: 'a' }];
    expect(cols.length).toBe(1);
  });

  it('bordered prop 兼容（始终有 border）', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        bordered: false,
      },
    });
    expect(wrapper.find('.c-data-table').classes()).toContain('border');
  });

  it('多列渲染顺序正确', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          { title: 'A', key: 'a' },
          { title: 'B', key: 'b' },
          { title: 'C', key: 'c' },
        ],
        data: [{ a: '1', b: '2', c: '3' }],
      },
    });
    const tds = wrapper.findAll('tbody td');
    expect(tds.length).toBe(3);
    expect(tds[0].text()).toBe('1');
    expect(tds[1].text()).toBe('2');
    expect(tds[2].text()).toBe('3');
  });

  it('width 为字符串时直接作为 CSS 值', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', width: '10rem' }],
        data: [],
      },
    });
    const th = wrapper.find('thead th');
    expect(th.attributes('style')).toContain('width: 10rem');
  });

  it('minWidth 为字符串时直接作为 CSS 值', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', minWidth: '12rem' }],
        data: [],
      },
    });
    const th = wrapper.find('thead th');
    expect(th.attributes('style')).toContain('min-width: 12rem');
  });

  it('ellipsis tooltip 配 render 返回 VNode 时不包裹 CTooltip（只 truncate）', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          {
            title: '操作',
            key: 'actions',
            ellipsis: { tooltip: true },
            render: (row) => h('span', { class: 'custom' }, String(row.text)),
          },
        ],
        data: [{ text: 'hi' }],
      },
    });
    expect(wrapper.find('tbody td span.relative').exists()).toBe(false);
    expect(wrapper.find('tbody td .custom').exists()).toBe(true);
  });

  it('title 为 undefined 时 th 渲染空', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ key: 'name' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('thead th').text()).toBe('');
  });

  it('render 返回数字时显示数字字符串', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [
          {
            title: '数量',
            key: 'count',
            render: (row) => row.n as number,
          },
        ],
        data: [{ n: 42 }],
      },
    });
    expect(wrapper.find('tbody td').text()).toBe('42');
  });

  it('ellipsis 默认（true）含 tooltip', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name', ellipsis: true }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('tbody td span.relative').exists()).toBe(true);
  });

  it('无 ellipsis 时不包裹 CTooltip', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [{ name: 'A' }],
      },
    });
    expect(wrapper.find('tbody td span.relative').exists()).toBe(false);
  });

  it('loading 且 data 为空时不显示空状态', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        loading: true,
      },
    });
    expect(wrapper.find('.c-data-table-empty').exists()).toBe(false);
    expect(wrapper.find('.c-data-table-loading').exists()).toBe(true);
  });

  it('bordered=true 时也有 border（兼容）', () => {
    const wrapper = mount(CDataTable, {
      props: {
        rowKey: stableTestRowKey,
        columns: [{ title: '名称', key: 'name' }],
        data: [],
        bordered: true,
      },
    });
    expect(wrapper.find('.c-data-table').classes()).toContain('border');
  });

  it('rowKey prop 指定字段名时正常渲染', () => {
    const wrapper = mount(CDataTable, {
      props: {
        columns: [{ title: '名称', key: 'name' }],
        data: [
          { id: 'a', name: 'A' },
          { id: 'b', name: 'B' },
        ],
        rowKey: 'id',
      },
    });
    const rows = wrapper.findAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].text()).toContain('A');
    expect(rows[1].text()).toContain('B');
  });

  it('缺少 rowKey 或行中缺少对应键时快速失败', () => {
    expect(() =>
      mount(CDataTable, {
        props: {
          columns: [{ title: '名称', key: 'name' }],
          data: [{ name: 'A' }],
        } as never,
      }),
    ).toThrow('CDataTable.rowKey');

    expect(() =>
      mount(CDataTable, {
        props: {
          columns: [{ title: '名称', key: 'name' }],
          data: [{ name: 'A' }],
          rowKey: 'id',
        },
      }),
    ).toThrow('缺少稳定键 id');

    expect(() =>
      mount(CDataTable, {
        props: {
          columns: [{ title: '名称', key: 'name' }],
          data: [{ name: 'A' }],
          rowKey: () => null as never,
        },
      }),
    ).toThrow('rowKey 函数返回值');
  });
});
