<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, type Ref } from 'vue';

interface RadioGroupContext {
  current: Ref<string>;
  select: (value: string) => void;
  registerButton: (value: string, element: HTMLButtonElement) => void;
  unregisterButton: (value: string, element: HTMLButtonElement) => void;
  tabIndex: (value: string) => 0 | -1;
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
const buttonRef = ref<HTMLButtonElement | null>(null);

const isSelected = computed(() => groupCtx.current.value === props.value);

function handleClick(): void {
  groupCtx.select(props.value);
}

onMounted(() => {
  groupCtx.registerButton(props.value, buttonRef.value!);
});

onBeforeUnmount(() => {
  groupCtx.unregisterButton(props.value, buttonRef.value!);
});
</script>

<template>
  <button
    ref="buttonRef"
    type="button"
    role="radio"
    :aria-checked="isSelected"
    :tabindex="groupCtx.tabIndex(value)"
    :class="[
      'c-radio-button relative z-10 h-7 rounded-sm px-3 text-xs font-medium transition-colors',
      isSelected ? 'text-text-strong' : 'text-muted',
    ]"
    @click="handleClick"
  >
    <slot>{{ label }}</slot>
  </button>
</template>
