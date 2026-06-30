<script setup lang="ts">
import { computed } from 'vue';

interface Props {
  type?: 'default' | 'brand' | 'success' | 'warning' | 'error';
  dot?: boolean;
}

type TagType = NonNullable<Props['type']>;

const props = withDefaults(defineProps<Props>(), {
  type: 'default',
  dot: false,
});

const currentType = computed<TagType>(() => props.type);

const typeClasses: Record<TagType, string> = {
  default: 'bg-surface-2 text-muted',
  brand: 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300',
  success: 'bg-success-500/12 text-success-600 dark:bg-success-500/15 dark:text-success-400',
  warning: 'bg-warning-500/15 text-warning-600 dark:bg-warning-500/15 dark:text-warning-400',
  error: 'bg-error-500/12 text-error-600 dark:bg-error-500/15 dark:text-error-400',
};

const dotClasses: Record<TagType, string> = {
  default: 'bg-muted',
  brand: 'bg-brand-500',
  success: 'bg-success-500',
  warning: 'bg-warning-500',
  error: 'bg-error-500',
};
const typeClass = computed(() => typeClasses[currentType.value]);
const dotClass = computed(() => dotClasses[currentType.value]);
</script>

<template>
  <span
    :class="[
      'inline-flex h-[22px] max-w-full items-center gap-1.5 overflow-hidden rounded-sm px-2 text-xs font-semibold whitespace-nowrap',
      typeClass,
    ]"
  >
    <span v-if="dot" :class="['h-1.5 w-1.5 rounded-full', dotClass]" />
    <slot />
  </span>
</template>
