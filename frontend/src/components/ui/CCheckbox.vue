<script setup lang="ts">
import { computed } from 'vue';
import { Check } from '@lucide/vue';

interface Props {
  modelValue?: boolean;
  disabled?: boolean;
  indeterminate?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
  disabled: false,
  indeterminate: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
}>();

const isActive = computed(() => props.modelValue || props.indeterminate);

function handleChange(event: Event): void {
  emit('update:modelValue', (event.currentTarget as HTMLInputElement).checked);
}
</script>

<template>
  <label
    :class="[
      'inline-flex items-center gap-2',
      disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
    ]"
  >
    <input
      type="checkbox"
      class="peer sr-only"
      :checked="modelValue"
      :disabled="disabled"
      @change="handleChange"
    />
    <span
      :class="[
        'c-checkbox-box flex h-4 w-4 items-center justify-center rounded-xs border-[1.5px] transition-[background-color,border-color,box-shadow] hover:border-brand-500',
        isActive ? 'border-brand-600 bg-brand-600' : 'border-border-strong bg-surface',
      ]"
    >
      <Check
        v-if="modelValue && !indeterminate"
        :stroke-width="4"
        stroke-linecap="butt"
        stroke-linejoin="miter"
        class="text-white"
      />
      <div v-else-if="indeterminate" class="h-0.5 w-2 bg-white" />
    </span>
    <span v-if="$slots.default" class="text-sm text-text">
      <slot />
    </span>
  </label>
</template>
