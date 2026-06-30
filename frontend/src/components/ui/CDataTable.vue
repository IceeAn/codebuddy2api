<script setup lang="ts">
import { computed, h as hFn, onBeforeUnmount, ref, watch, type VNode } from 'vue';
import CSpin from './CSpin.vue';
import CEmptyState from './CEmptyState.vue';
import CTooltip from './CTooltip.vue';

export type CellRender = VNode | string | number;

export interface Column<T = Record<string, unknown>> {
  title?: string;
  key: string;
  width?: number | string;
  minWidth?: number | string;
  align?: 'left' | 'right' | 'center';
  ellipsis?: boolean | { tooltip?: boolean };
  render?: (row: T, index: number) => CellRender;
  className?: string;
  headerClassName?: string;
}

interface Props {
  columns: Column[];
  data: Record<string, unknown>[];
  loading?: boolean;
  error?: boolean;
  bordered?: boolean;
  size?: 'small' | 'default';
  rowKey?: string;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: false,
  bordered: false,
  size: 'default',
  rowKey: undefined,
});

const rowHeight = computed(() => (props.size === 'small' ? 'h-9' : 'h-11'));

const MINIMUM_LOADING_MS = 300;
const visibleLoading = ref(props.loading);
let loadingStartedAt = props.loading ? Date.now() : 0;
let hideLoadingTimer: number | undefined;

function clearHideLoadingTimer(): void {
  window.clearTimeout(hideLoadingTimer);
  hideLoadingTimer = undefined;
}

watch(
  () => props.loading,
  (loading) => {
    clearHideLoadingTimer();
    if (loading) {
      loadingStartedAt = Date.now();
      visibleLoading.value = true;
      return;
    }

    const remaining = MINIMUM_LOADING_MS - (Date.now() - loadingStartedAt);
    if (remaining <= 0) {
      visibleLoading.value = false;
      return;
    }

    hideLoadingTimer = window.setTimeout(() => {
      visibleLoading.value = false;
      hideLoadingTimer = undefined;
    }, remaining);
  },
);

onBeforeUnmount(clearHideLoadingTimer);

const showEmpty = computed(() => !visibleLoading.value && !props.error && props.data.length === 0);

function widthStyle(col: Column): Record<string, string> {
  const style: Record<string, string> = {};
  if (col.width !== undefined) {
    style.width = typeof col.width === 'number' ? `${col.width}px` : col.width;
  }
  if (col.minWidth !== undefined) {
    style.minWidth = typeof col.minWidth === 'number' ? `${col.minWidth}px` : col.minWidth;
  }
  return style;
}

function alignClass(col: Column): string {
  switch (col.align) {
    case 'right':
      return 'text-right';
    case 'center':
      return 'text-center';
    default:
      return 'text-left';
  }
}

function isEllipsis(col: Column): boolean {
  return col.ellipsis !== undefined && col.ellipsis !== false;
}

function hasTooltip(col: Column): boolean {
  if (!isEllipsis(col)) return false;
  if (typeof col.ellipsis === 'object') return col.ellipsis.tooltip !== false;
  return true;
}

function renderCell(col: Column, row: Record<string, unknown>, index: number): VNode {
  const content: CellRender = col.render
    ? col.render(row, index)
    : ((row[col.key] as string | number | undefined) ?? '');
  const ellipsis = isEllipsis(col);

  if (hasTooltip(col)) {
    // 仅对文本内容（string/number）包裹 tooltip；VNode 内容跳过 tooltip，只 truncate
    const text = typeof content === 'string' || typeof content === 'number' ? String(content) : '';
    if (text) {
      return hFn(
        CTooltip,
        { content: text, class: 'w-full min-w-0 max-w-full' },
        { default: () => [renderVNode(content, ellipsis)] },
      );
    }
  }
  return renderVNode(content, ellipsis);
}

function renderVNode(content: CellRender, ellipsis = false): VNode {
  if (typeof content === 'string' || typeof content === 'number') {
    return hFn(
      'span',
      { class: ellipsis ? 'block min-w-0 max-w-full truncate' : undefined },
      String(content),
    );
  }
  return content;
}
</script>

<template>
  <div class="c-data-table relative rounded-lg border border-border" :aria-busy="visibleLoading">
    <div class="c-data-table-scroll overflow-x-auto rounded-lg">
      <table class="w-full border-collapse">
        <thead>
          <tr class="border-b border-border">
            <th
              v-for="col in columns"
              :key="col.key"
              :style="widthStyle(col)"
              :class="[
                'h-10 bg-surface-2/50 px-4 align-middle text-xs font-semibold tracking-wide text-muted uppercase',
                alignClass(col),
                col.headerClassName ?? '',
              ]"
            >
              {{ col.title ?? '' }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in data"
            :key="rowKey ? (row[rowKey] as PropertyKey) : index"
            :class="[
              rowHeight,
              'border-b border-border/60 text-sm text-text transition-colors duration-[var(--duration-fast)] hover:bg-surface-2',
            ]"
          >
            <td
              v-for="col in columns"
              :key="col.key"
              :style="widthStyle(col)"
              :class="[
                'px-4 align-middle',
                alignClass(col),
                isEllipsis(col) ? 'max-w-0 truncate overflow-hidden' : '',
                col.className ?? '',
              ]"
            >
              <component :is="renderCell(col, row, index)" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div
      v-if="showEmpty"
      class="c-data-table-empty grid min-h-24 place-items-center px-4 py-8 text-center text-sm text-muted"
    >
      <slot name="empty">
        <CEmptyState description="暂无数据" />
      </slot>
    </div>
    <div v-if="visibleLoading && data.length === 0" class="c-data-table-loading-spacer min-h-24" />
    <Transition name="c-data-table-loading">
      <div
        v-if="visibleLoading"
        class="c-data-table-loading absolute inset-x-0 top-10 bottom-0 z-10 bg-surface/60 px-4 text-sm text-muted backdrop-blur-[1px]"
        role="status"
        aria-label="正在加载"
      >
        <div class="c-data-table-loading-indicator flex w-full items-center justify-center">
          <CSpin size="lg" aria-hidden="true" />
        </div>
      </div>
    </Transition>
  </div>
</template>
