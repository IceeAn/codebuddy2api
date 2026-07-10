<script setup lang="ts">
import { onBeforeUnmount, onUpdated, provide, reactive, ref, watch } from 'vue';

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
const buttons = new Map<string, HTMLButtonElement>();
const indicatorVisible = ref(false);
const indicatorStyle = reactive({
  width: '0px',
  transform: 'translateX(0px)',
});

function syncIndicator(): void {
  const activeButton = buttons.get(current.value);
  if (!activeButton) {
    indicatorVisible.value = false;
    return;
  }
  indicatorStyle.width = `${activeButton.offsetWidth}px`;
  indicatorStyle.transform = `translateX(${activeButton.offsetLeft}px)`;
  indicatorVisible.value = true;
}

const resizeObserver = new ResizeObserver(syncIndicator);

function registerButton(value: string, element: HTMLButtonElement): void {
  buttons.set(value, element);
  resizeObserver.observe(element);
  syncIndicator();
}

function unregisterButton(value: string, element: HTMLButtonElement): void {
  resizeObserver.unobserve(element);
  buttons.delete(value);
  syncIndicator();
}

watch(
  () => props.modelValue,
  (v) => {
    current.value = v;
    syncIndicator();
  },
);

onUpdated(syncIndicator);

onBeforeUnmount(() => {
  resizeObserver.disconnect();
});

function select(value: string): void {
  if (value === current.value) return;
  emit('update:modelValue', value);
}

provide('c-radio-group', { current, select, registerButton, unregisterButton });
</script>

<template>
  <div class="c-radio-group relative inline-flex rounded-md bg-surface-2 p-0.5" role="radiogroup">
    <span
      v-show="indicatorVisible"
      class="c-radio-group-indicator pointer-events-none absolute top-0.5 bottom-0.5 left-0 rounded-sm bg-segment-active shadow-[var(--shadow-xs)] transition-transform duration-200 ease-out-quad"
      :style="indicatorStyle"
      aria-hidden="true"
    />
    <slot />
  </div>
</template>
