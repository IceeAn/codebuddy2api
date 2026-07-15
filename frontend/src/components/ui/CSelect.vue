<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { Check, ChevronDown, ListFilter } from '@lucide/vue';
import CSpin from './CSpin.vue';
import { formItemControlKey } from './formContext';

interface Option {
  label: string;
  value: string | number;
}

interface Props {
  id?: string;
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
  id: undefined,
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
const formItem = inject(formItemControlKey, null);
const instanceId = useId().replace(/[^A-Za-z0-9_-]/g, '');
const controlId = computed(() => props.id ?? formItem?.controlId ?? `c-select-${instanceId}`);
const listboxId = `c-select-listbox-${instanceId}`;
const activeIndex = ref(-1);

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

function optionId(index: number): string {
  return `c-select-option-${instanceId}-${index}`;
}

const activeDescendant = computed(() => {
  if (!open.value || activeIndex.value < 0 || activeIndex.value >= filteredOptions.value.length) {
    return undefined;
  }
  return optionId(activeIndex.value);
});

function initialActiveIndex(preferLast = false): number {
  if (filteredOptions.value.length === 0) return -1;
  const selectedIndex = filteredOptions.value.findIndex(
    (option) => option.value === props.modelValue,
  );
  if (selectedIndex >= 0) return selectedIndex;
  return preferLast ? filteredOptions.value.length - 1 : 0;
}

function scrollActiveOptionIntoView(): void {
  if (activeIndex.value < 0) return;
  nextTick(() => {
    rootRef.value
      ?.querySelector<HTMLElement>(`[id="${optionId(activeIndex.value)}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  });
}

function openDropdown(preferLast = false): void {
  open.value = true;
  activeIndex.value = initialActiveIndex(preferLast);
  scrollActiveOptionIntoView();
  nextTick(() => {
    const triggerRect = triggerRef.value!.getBoundingClientRect();
    const panelHeight = Math.min(panelRef.value!.scrollHeight, PANEL_MAX_HEIGHT_PX);
    const spaceBelow = window.innerHeight - triggerRect.bottom - PANEL_GAP_PX;
    const spaceAbove = triggerRect.top - PANEL_GAP_PX;
    panelPlacement.value = spaceBelow < panelHeight && spaceAbove > spaceBelow ? 'top' : 'bottom';
    if (props.filterable) filterInputRef.value?.focus();
  });
}

function closeDropdown(options: { restoreFocus?: boolean; validateBlur?: boolean } = {}): void {
  if (!open.value) return;
  open.value = false;
  query.value = '';
  activeIndex.value = -1;
  if (options.validateBlur) formItem?.onBlur();
  if (options.restoreFocus) nextTick(() => triggerRef.value?.focus());
}

function toggle(): void {
  if (open.value) {
    closeDropdown();
  } else {
    openDropdown();
  }
}

function select(opt: Option): void {
  emit('update:modelValue', opt.value);
  formItem?.onInput();
  closeDropdown({ restoreFocus: true });
}

function moveActive(delta: number): void {
  const count = filteredOptions.value.length;
  if (count === 0) {
    activeIndex.value = -1;
    return;
  }
  activeIndex.value = (activeIndex.value + delta + count) % count;
  scrollActiveOptionIntoView();
}

function selectActive(): void {
  const option = filteredOptions.value[activeIndex.value];
  if (option) select(option);
}

function onComboboxKeydown(event: KeyboardEvent): void {
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      if (open.value) moveActive(1);
      else openDropdown();
      break;
    case 'ArrowUp':
      event.preventDefault();
      if (open.value) moveActive(-1);
      else openDropdown(true);
      break;
    case 'Home':
      if (!open.value) return;
      event.preventDefault();
      activeIndex.value = filteredOptions.value.length ? 0 : -1;
      scrollActiveOptionIntoView();
      break;
    case 'End':
      if (!open.value) return;
      event.preventDefault();
      activeIndex.value = filteredOptions.value.length - 1;
      scrollActiveOptionIntoView();
      break;
    case 'Enter':
    case ' ':
      event.preventDefault();
      if (open.value) selectActive();
      else openDropdown();
      break;
    case 'Escape':
      if (!open.value) return;
      event.preventDefault();
      closeDropdown({ restoreFocus: true });
      break;
    case 'Tab':
      closeDropdown({ validateBlur: true });
      break;
  }
}

function onFilterKeydown(event: KeyboardEvent): void {
  if (event.key === ' ') return;
  onComboboxKeydown(event);
}

function onFocusout(event: FocusEvent): void {
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && (event.currentTarget as HTMLElement).contains(nextTarget))
    return;
  formItem?.onBlur();
}

function activateFooterAction(): void {
  closeDropdown();
  emit('footer-action');
}

function onDocumentClick(event: MouseEvent): void {
  if (!open.value) return;
  if (rootRef.value!.contains(event.target as Node | null)) return;
  closeDropdown({ validateBlur: true });
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    closeDropdown({ restoreFocus: true });
  }
}

watch(query, () => {
  if (!open.value) return;
  activeIndex.value = initialActiveIndex();
  scrollActiveOptionIntoView();
});

watch(
  () => props.options,
  () => {
    if (!open.value) return;
    activeIndex.value = initialActiveIndex();
    scrollActiveOptionIntoView();
  },
);

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
  <div ref="rootRef" class="c-select relative inline-flex w-full" @focusout="onFocusout">
    <button
      :id="controlId"
      ref="triggerRef"
      type="button"
      :disabled="disabled"
      role="combobox"
      :aria-expanded="open"
      aria-haspopup="listbox"
      :aria-controls="listboxId"
      :aria-activedescendant="activeDescendant"
      :aria-labelledby="formItem?.labelId.value"
      :aria-invalid="formItem?.invalid.value || undefined"
      :aria-describedby="formItem?.describedBy.value"
      class="c-select-trigger c-control-focus inline-flex w-full min-w-0 items-center justify-between gap-2 rounded-md border border-border bg-surface text-text hover:border-border-strong disabled:bg-surface-2 disabled:text-muted/60"
      :class="triggerSizeClass"
      @click.stop="toggle"
      @keydown.stop="onComboboxKeydown"
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
            role="combobox"
            aria-label="筛选选项"
            aria-expanded="true"
            aria-autocomplete="list"
            :aria-controls="listboxId"
            :aria-activedescendant="activeDescendant"
            class="c-select-filter c-control-focus h-9 w-full rounded-md border border-border bg-surface px-3 text-sm text-text"
            placeholder="搜索..."
            @keydown.stop="onFilterKeydown"
          />
        </div>
        <div :id="listboxId" class="c-select-options min-h-0 overflow-y-auto" role="listbox">
          <div
            v-for="(opt, index) in filteredOptions"
            :id="optionId(index)"
            :key="opt.value"
            role="option"
            :aria-selected="opt.value === modelValue"
            class="c-select-option flex h-9 cursor-pointer items-center justify-between gap-2 rounded-md px-3 text-sm text-text hover:bg-surface-2"
            :class="[
              opt.value === modelValue ? 'bg-soft-brand text-tone-brand' : '',
              index === activeIndex && opt.value !== modelValue ? 'bg-surface-2' : '',
            ]"
            @click="select(opt)"
            @mouseenter="activeIndex = index"
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
