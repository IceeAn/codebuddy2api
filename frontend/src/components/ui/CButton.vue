<script setup lang="ts">
import { computed } from 'vue';
import CSpin from './CSpin.vue';

interface Props {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  shape?: 'default' | 'circle';
  loading?: boolean;
  disabled?: boolean;
  block?: boolean;
}

type ButtonVariant = NonNullable<Props['variant']>;
type ButtonSize = NonNullable<Props['size']>;
type ButtonShape = NonNullable<Props['shape']>;

const props = withDefaults(defineProps<Props>(), {
  variant: 'secondary',
  size: 'md',
  shape: 'default',
  loading: false,
  disabled: false,
  block: false,
});

const emit = defineEmits<{
  click: [event: MouseEvent];
}>();

const currentVariant = computed<ButtonVariant>(() => props.variant);
const currentSize = computed<ButtonSize>(() => props.size);
const currentShape = computed<ButtonShape>(() => props.shape);

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-[38px] px-4 text-sm gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
};

const circleSizeClasses: Record<ButtonSize, string> = {
  sm: 'h-9 w-9 text-[13px] gap-1.5',
  md: 'h-10 w-10 text-sm gap-2',
  lg: 'h-11 w-11 text-[15px] gap-2',
};

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-brand-600 !text-white shadow-sm hover:bg-brand-500 hover:shadow-[var(--shadow-brand-glow)] active:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400 disabled:bg-brand-600/50 disabled:!text-white/60 disabled:hover:bg-brand-600/50 disabled:hover:shadow-none',
  secondary:
    'bg-surface text-text border border-border shadow-[var(--shadow-xs)] hover:bg-surface-2 hover:border-border-strong active:bg-surface-3 disabled:opacity-50',
  ghost:
    'bg-transparent text-muted hover:bg-surface-2 hover:text-text active:bg-surface-3 disabled:opacity-40',
  danger:
    'bg-error-600 !text-white shadow-sm hover:bg-error-500 active:bg-error-600 disabled:opacity-50',
};

const variantClass = computed(() => variantClasses[currentVariant.value]);
const sizeClass = computed(() =>
  currentShape.value === 'circle'
    ? `${circleSizeClasses[currentSize.value]} rounded-full p-0`
    : sizeClasses[currentSize.value],
);

function isActuallyDisabled(): boolean {
  return props.disabled || props.loading;
}
</script>

<template>
  <button
    type="button"
    :disabled="isActuallyDisabled()"
    :class="[
      'inline-flex items-center justify-center rounded-md font-medium transition-[background-color,box-shadow,transform] duration-(--duration-fast) ease-out-quad active:scale-[0.98] disabled:active:scale-100',
      'shrink-0 whitespace-nowrap',
      variantClass,
      sizeClass,
      block ? 'w-full' : '',
      loading ? 'opacity-80' : '',
    ]"
    @click="emit('click', $event)"
  >
    <CSpin v-if="loading" size="sm" inherit />
    <slot v-else name="icon" />
    <slot />
  </button>
</template>
