<script setup lang="ts">
import { computed } from 'vue';

interface Props {
  modelValue?: boolean;
  size?: 'sm' | 'md';
  disabled?: boolean;
}

type SwitchSize = NonNullable<Props['size']>;

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
  size: 'md',
  disabled: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
}>();

function toggle(): void {
  emit('update:modelValue', !props.modelValue);
}

const currentSize = computed<SwitchSize>(() => props.size);

const trackClasses: Record<SwitchSize, string> = {
  sm: 'w-8 h-5',
  md: 'w-10 h-[22px]',
};

const thumbClasses: Record<SwitchSize, string> = {
  sm: 'w-3.5 h-3.5',
  md: 'h-[18px] w-[18px]',
};

const thumbTranslate: Record<SwitchSize, { on: string; off: string }> = {
  sm: { on: 'translate-x-[16px]', off: 'translate-x-[2px]' },
  md: { on: 'translate-x-[20px]', off: 'translate-x-[2px]' },
};
const trackClass = computed(() => trackClasses[currentSize.value]);
const thumbClass = computed(() => thumbClasses[currentSize.value]);
const thumbTranslateClass = computed(() =>
  props.modelValue ? thumbTranslate[currentSize.value].on : thumbTranslate[currentSize.value].off,
);
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    :class="[
      'relative inline-flex items-center rounded-full transition-[background-color,box-shadow] duration-[var(--duration-fast)]',
      trackClass,
      modelValue ? 'bg-switch-on' : 'bg-switch-off',
      disabled ? 'opacity-50' : '',
    ]"
    @click="toggle"
  >
    <span
      :class="[
        'c-switch-thumb inline-block rounded-full bg-white shadow-sm transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-quad)]',
        thumbClass,
        thumbTranslateClass,
      ]"
    />
  </button>
</template>
