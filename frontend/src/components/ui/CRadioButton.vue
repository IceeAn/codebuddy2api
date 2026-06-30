<script setup lang="ts">
import { computed, inject, type Ref } from 'vue';

interface RadioGroupContext {
  current: Ref<string>;
  select: (value: string) => void;
}

interface Props {
  value: string;
  label?: string;
}

const props = withDefaults(defineProps<Props>(), {
  label: undefined,
});

const ctx = inject<RadioGroupContext>('c-radio-group');

if (!ctx) {
  throw new Error('CRadioButton 必须在 CRadioGroup 内使用');
}

const groupCtx: RadioGroupContext = ctx;

const isSelected = computed(() => groupCtx.current.value === props.value);

function handleClick(): void {
  groupCtx.select(props.value);
}
</script>

<template>
  <button
    type="button"
    :class="[
      'c-radio-button h-7 rounded-sm px-3 text-xs font-medium transition-colors',
      isSelected
        ? 'bg-surface text-text-strong shadow-[var(--shadow-xs)] dark:bg-surface-3'
        : 'text-muted',
    ]"
    @click="handleClick"
  >
    <slot>{{ label }}</slot>
  </button>
</template>
