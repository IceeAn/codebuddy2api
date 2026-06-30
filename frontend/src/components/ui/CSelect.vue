<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { Check, ChevronDown } from '@lucide/vue';
import CSpin from './CSpin.vue';

interface Option {
  label: string;
  value: string | number;
}

interface Props {
  modelValue?: string | number;
  options?: Option[];
  placeholder?: string;
  loading?: boolean;
  filterable?: boolean;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  options: () => [],
  placeholder: undefined,
  loading: false,
  filterable: false,
  disabled: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: string | number];
}>();

const open = ref(false);
const query = ref('');
const filterInputRef = ref<HTMLInputElement | null>(null);

const selectedLabel = computed(() => {
  const opt = props.options.find((o) => o.value === props.modelValue);
  return opt?.label ?? '';
});

const filteredOptions = computed(() => {
  if (!props.filterable || !query.value) return props.options;
  const q = query.value.toLowerCase();
  return props.options.filter((o) => o.label.toLowerCase().includes(q));
});

function toggle(): void {
  open.value = !open.value;
  if (!open.value) {
    query.value = '';
  } else if (props.filterable) {
    nextTick(() => filterInputRef.value?.focus());
  }
}

function select(opt: Option): void {
  emit('update:modelValue', opt.value);
  open.value = false;
  query.value = '';
}

function onDocumentClick(): void {
  if (!open.value) return;
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
  document.addEventListener('click', onDocumentClick);
  document.addEventListener('keydown', onKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick);
  document.removeEventListener('keydown', onKeydown);
});
</script>

<template>
  <div class="c-select relative inline-flex w-full">
    <button
      type="button"
      :disabled="disabled"
      class="c-select-trigger c-control-focus inline-flex h-[38px] w-full min-w-0 items-center justify-between gap-2 rounded-md border border-border bg-surface px-3 text-sm text-text hover:border-border-strong disabled:bg-surface-2 disabled:text-muted/60"
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
      enter-active-class="transition-[opacity,transform] duration-[var(--duration-fast)] ease-[var(--ease-out-quad)]"
      leave-active-class="transition-[opacity,transform] duration-[var(--duration-fast)] ease-[var(--ease-out-quad)]"
      enter-from-class="opacity-0 scale-95 origin-top"
      leave-to-class="opacity-0 scale-95 origin-top"
    >
      <div
        v-if="open"
        class="c-select-panel absolute top-full left-0 z-50 mt-1 flex max-h-[18rem] w-full min-w-full flex-col overflow-hidden rounded-lg border border-border bg-surface p-1 shadow-[var(--shadow-popover)]"
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
            :class="
              opt.value === modelValue
                ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300'
                : ''
            "
            @click="select(opt)"
          >
            <span class="min-w-0 truncate">{{ opt.label }}</span>
            <Check v-if="opt.value === modelValue" :size="14" />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>
