<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { Check, ChevronDown, ListFilter } from '@lucide/vue';
import CSpin from './CSpin.vue';

interface Option {
  label: string;
  value: string | number;
}

interface Props {
  modelValue?: string | number;
  options?: Option[];
  size?: 'sm' | 'md';
  placeholder?: string;
  loading?: boolean;
  filterable?: boolean;
  disabled?: boolean;
  footerActionLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  options: () => [],
  size: 'md',
  placeholder: undefined,
  loading: false,
  filterable: false,
  disabled: false,
  footerActionLabel: undefined,
});

const emit = defineEmits<{
  'update:modelValue': [value: string | number];
  'footer-action': [];
}>();

const open = ref(false);
const query = ref('');
const rootRef = ref<HTMLDivElement | null>(null);
const triggerRef = ref<HTMLButtonElement | null>(null);
const panelRef = ref<HTMLDivElement | null>(null);
const filterInputRef = ref<HTMLInputElement | null>(null);
const panelPlacement = ref<'top' | 'bottom'>('bottom');

const PANEL_MAX_HEIGHT_PX = 288;
const PANEL_GAP_PX = 4;

const selectedLabel = computed(() => {
  const opt = props.options.find((o) => o.value === props.modelValue);
  return opt?.label ?? '';
});

const triggerSizeClass = computed(() =>
  props.size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-[38px] px-3 text-sm',
);

const panelPositionClass = computed(() =>
  panelPlacement.value === 'top' ? 'bottom-full mb-1 origin-bottom' : 'top-full mt-1 origin-top',
);

const filteredOptions = computed(() => {
  if (!props.filterable || !query.value) return props.options;
  const q = query.value.toLowerCase();
  return props.options.filter((o) => o.label.toLowerCase().includes(q));
});

function toggle(): void {
  open.value = !open.value;
  if (!open.value) {
    query.value = '';
  } else {
    nextTick(() => {
      const triggerRect = triggerRef.value!.getBoundingClientRect();
      const panelHeight = Math.min(panelRef.value!.scrollHeight, PANEL_MAX_HEIGHT_PX);
      const spaceBelow = window.innerHeight - triggerRect.bottom - PANEL_GAP_PX;
      const spaceAbove = triggerRect.top - PANEL_GAP_PX;
      panelPlacement.value = spaceBelow < panelHeight && spaceAbove > spaceBelow ? 'top' : 'bottom';
      if (props.filterable) filterInputRef.value?.focus();
    });
  }
}

function select(opt: Option): void {
  emit('update:modelValue', opt.value);
  open.value = false;
  query.value = '';
}

function activateFooterAction(): void {
  open.value = false;
  query.value = '';
  emit('footer-action');
}

function onDocumentClick(event: MouseEvent): void {
  if (!open.value) return;
  if (rootRef.value!.contains(event.target as Node | null)) return;
  open.value = false;
  query.value = '';
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    open.value = false;
    query.value = '';
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick, true);
  document.addEventListener('keydown', onKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick, true);
  document.removeEventListener('keydown', onKeydown);
});
</script>

<template>
  <div ref="rootRef" class="c-select relative inline-flex w-full">
    <button
      ref="triggerRef"
      type="button"
      :disabled="disabled"
      class="c-select-trigger c-control-focus inline-flex w-full min-w-0 items-center justify-between gap-2 rounded-md border border-border bg-surface text-text hover:border-border-strong disabled:bg-surface-2 disabled:text-muted/60"
      :class="triggerSizeClass"
      @click.stop="toggle"
    >
      <span :class="['min-w-0 truncate', selectedLabel ? 'text-text' : 'text-muted/60']">
        {{ selectedLabel || placeholder }}
      </span>
      <span class="inline-flex shrink-0 items-center gap-1.5">
        <CSpin v-if="loading" size="sm" />
        <ChevronDown
          :size="16"
          class="c-select-chevron text-muted transition-transform"
          :class="{ 'rotate-180': open }"
        />
      </span>
    </button>

    <Transition
      enter-active-class="transition-[opacity,translate] duration-[var(--duration-base)] ease-[var(--ease-out-quad)] will-change-[opacity,translate]"
      leave-active-class="transition-[opacity,translate] duration-[var(--duration-base)] ease-[var(--ease-in-quad)] will-change-[opacity,translate]"
      enter-from-class="opacity-0 -translate-y-2"
      leave-to-class="opacity-0 -translate-y-2"
    >
      <div
        ref="panelRef"
        v-if="open"
        class="c-select-panel absolute left-0 z-50 flex max-h-[18rem] w-full min-w-full flex-col overflow-hidden rounded-lg border border-border bg-surface p-1 shadow-[var(--shadow-popover)]"
        :class="panelPositionClass"
        @click.stop
      >
        <div v-if="filterable" class="c-select-filter-area mb-1 shrink-0 px-1 pt-1">
          <input
            ref="filterInputRef"
            v-model="query"
            type="text"
            class="c-select-filter c-control-focus h-9 w-full rounded-md border border-border bg-surface px-3 text-sm text-text"
            placeholder="搜索..."
          />
        </div>
        <div class="c-select-options min-h-0 overflow-y-auto">
          <div
            v-for="opt in filteredOptions"
            :key="opt.value"
            class="c-select-option flex h-9 cursor-pointer items-center justify-between gap-2 rounded-md px-3 text-sm text-text hover:bg-surface-2"
            :class="opt.value === modelValue ? 'bg-soft-brand text-tone-brand' : ''"
            @click="select(opt)"
          >
            <span class="min-w-0 truncate">{{ opt.label }}</span>
            <Check v-if="opt.value === modelValue" :size="14" />
          </div>
        </div>
        <div
          v-if="footerActionLabel"
          class="c-select-footer mt-1 shrink-0 border-t border-border/60 pt-1"
        >
          <button
            type="button"
            class="c-select-footer-action flex h-9 w-full items-center gap-2 rounded-md px-3 text-left text-sm font-medium text-tone-brand hover:bg-soft-brand"
            @click="activateFooterAction"
          >
            <ListFilter :size="15" class="shrink-0" />
            <span class="min-w-0 truncate">{{ footerActionLabel }}</span>
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>
