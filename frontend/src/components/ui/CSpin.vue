<script setup lang="ts">
import { computed } from 'vue';

interface Props {
  size?: 'sm' | 'md' | 'lg';
  inherit?: boolean;
}

type SpinSize = NonNullable<Props['size']>;

const props = withDefaults(defineProps<Props>(), {
  size: 'md',
  inherit: false,
});

const currentSize = computed<SpinSize>(() => props.size);

const sizeClasses: Record<SpinSize, string> = {
  sm: 'w-[14px] h-[14px] border-2',
  md: 'w-5 h-5 border-2',
  lg: 'w-7 h-7 border-[3px]',
};
const sizeClass = computed(() => sizeClasses[currentSize.value]);
</script>

<template>
  <span
    :class="[
      'inline-block animate-spin rounded-full border-current/25 border-t-current',
      sizeClass,
      inherit ? '' : 'text-brand-500',
    ]"
    role="status"
    aria-label="加载中"
  />
</template>
