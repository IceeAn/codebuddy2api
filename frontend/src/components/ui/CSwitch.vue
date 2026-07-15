<script setup lang="ts">
import { computed, inject, useId } from 'vue';
import { formItemControlKey } from './formContext';

interface Props {
  modelValue?: boolean;
  size?: 'sm' | 'md';
  disabled?: boolean;
  id?: string;
}

type SwitchSize = NonNullable<Props['size']>;

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
  size: 'md',
  disabled: false,
  id: undefined,
});

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
}>();
const formItem = inject(formItemControlKey, null);
const ownId = `c-switch-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
const controlId = computed(() => props.id || formItem?.controlId || ownId);

function toggle(): void {
  emit('update:modelValue', !props.modelValue);
  formItem?.onInput();
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
    :id="controlId"
    role="switch"
    :aria-checked="modelValue"
    :aria-labelledby="formItem?.labelId.value"
    :aria-invalid="formItem?.invalid.value || undefined"
    :aria-describedby="formItem?.describedBy.value"
    :disabled="disabled"
    :class="[
      'relative inline-flex items-center rounded-full transition-[background-color,box-shadow] duration-[var(--duration-fast)]',
      trackClass,
      modelValue ? 'bg-switch-on' : 'bg-switch-off',
      disabled ? 'opacity-50' : '',
    ]"
    @click="toggle"
    @blur="formItem?.onBlur()"
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
