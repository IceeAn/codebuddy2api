<script setup lang="ts">
import { computed, ref } from 'vue';
import { ChevronDown, ChevronUp, X } from '@lucide/vue';

interface Props {
  modelValue?: number | null;
  size?: 'sm' | 'md';
  min?: number;
  max?: number;
  step?: number;
  clearable?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: null,
  size: 'md',
  min: undefined,
  max: undefined,
  step: 1,
  clearable: false,
  disabled: false,
  placeholder: undefined,
});

const emit = defineEmits<{
  'update:modelValue': [value: number | null];
}>();

const inputRef = ref<HTMLInputElement | null>(null);

function clamp(value: number): number {
  let v = value;
  if (props.min !== undefined && v < props.min) v = props.min;
  if (props.max !== undefined && v > props.max) v = props.max;
  return v;
}

function applyNativeStep(direction: 1 | -1): void {
  const input = inputRef.value!;
  if (direction === 1) {
    input.stepUp();
  } else {
    input.stepDown();
  }
  const steppedValue = input.valueAsNumber;
  const fallbackValue = clamp((props.modelValue ?? 0) + direction * props.step);
  emit('update:modelValue', Number.isNaN(steppedValue) ? fallbackValue : steppedValue);
}

const displayValue = computed(() => {
  if (props.modelValue === null || props.modelValue === undefined) return '';
  return String(props.modelValue);
});

const inputSizeClass = computed(() =>
  props.size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-[38px] px-3 text-sm',
);

function onInput(event: Event): void {
  const target = event.target as HTMLInputElement;
  const raw = target.value;
  if (raw === '') {
    emit('update:modelValue', null);
    return;
  }
  // type="number" 的 input.value 不会返回非数字字符串，无需 NaN 守卫
  emit('update:modelValue', clamp(Number(raw)));
}

function clear(): void {
  emit('update:modelValue', null);
}

// 右侧控件使用 absolute 定位，需要为输入文本预留空间。
const inputPaddingRight = computed(() => (props.clearable ? 'pr-16' : 'pr-9'));
</script>

<template>
  <div class="c-input-number relative inline-flex w-full items-stretch">
    <input
      ref="inputRef"
      type="number"
      :value="displayValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :min="min"
      :max="max"
      :step="step"
      :class="[
        'c-input-number-input c-control-focus w-full rounded-md border border-border bg-surface text-text placeholder:text-muted/60 hover:border-border-strong disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted/60',
        inputSizeClass,
        inputPaddingRight,
      ]"
      @input="onInput"
    />
    <button
      v-if="clearable && modelValue !== null && modelValue !== undefined"
      type="button"
      :disabled="disabled"
      class="c-input-number-clear absolute top-1/2 right-8 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-text disabled:opacity-50"
      aria-label="清空"
      @click="clear"
    >
      <X :size="14" />
    </button>
    <div
      class="c-input-number-controls absolute inset-y-px right-px flex w-6 flex-col overflow-hidden rounded-r-[5px] border-l border-border"
    >
      <button
        type="button"
        :disabled="disabled"
        class="inline-flex min-h-0 flex-1 items-center justify-center border-b border-border text-muted hover:bg-surface-2 hover:text-text disabled:opacity-50"
        tabindex="-1"
        aria-label="增加"
        @click="applyNativeStep(1)"
      >
        <ChevronUp :size="12" />
      </button>
      <button
        type="button"
        :disabled="disabled"
        class="inline-flex min-h-0 flex-1 items-center justify-center text-muted hover:bg-surface-2 hover:text-text disabled:opacity-50"
        tabindex="-1"
        aria-label="减少"
        @click="applyNativeStep(-1)"
      >
        <ChevronDown :size="12" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.c-input-number-input {
  appearance: textfield;
}

.c-input-number-input::-webkit-inner-spin-button,
.c-input-number-input::-webkit-outer-spin-button {
  margin: 0;
  appearance: none;
}
</style>
