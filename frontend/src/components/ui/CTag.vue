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
  brand: 'bg-soft-brand text-tone-brand',
  success: 'bg-soft-success text-tone-success',
  warning: 'bg-soft-warning text-tone-warning',
  error: 'bg-soft-error text-tone-error',
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
