<script setup lang="ts">
import { provide, ref, watch } from 'vue';

interface Props {
  modelValue?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
});

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const current = ref(props.modelValue);

watch(
  () => props.modelValue,
  (v) => {
    current.value = v;
  },
);

function select(value: string): void {
  if (value === current.value) return;
  emit('update:modelValue', value);
}

provide('c-radio-group', { current, select });
</script>

<template>
  <div class="c-radio-group inline-flex rounded-md bg-surface-2 p-0.5">
    <slot />
  </div>
</template>
