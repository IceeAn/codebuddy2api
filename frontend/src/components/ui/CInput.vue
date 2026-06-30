<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Eye, EyeOff } from '@lucide/vue';

interface Props {
  modelValue?: string;
  type?: 'text' | 'password' | 'textarea';
  size?: 'sm' | 'md';
  placeholder?: string;
  readonly?: boolean;
  disabled?: boolean;
  error?: boolean;
  showPasswordToggle?: boolean;
  autosize?: { minRows: number; maxRows?: number };
  autofocus?: boolean;
  autocomplete?: string;
}

type InputType = NonNullable<Props['type']>;
type InputSize = NonNullable<Props['size']>;

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  type: 'text',
  size: 'md',
  placeholder: undefined,
  readonly: false,
  disabled: false,
  error: false,
  showPasswordToggle: true,
  autosize: undefined,
  autofocus: false,
  autocomplete: undefined,
});

const emit = defineEmits<{
  'update:modelValue': [value: string];
  keyup: [event: KeyboardEvent];
  enter: [event: KeyboardEvent];
}>();

const isPasswordVisible = ref(false);
const currentType = computed<InputType>(() => props.type);
const currentSize = computed<InputSize>(() => props.size);

const actualInputType = computed(() =>
  currentType.value === 'password' && isPasswordVisible.value ? 'text' : currentType.value,
);

function togglePassword(): void {
  isPasswordVisible.value = !isPasswordVisible.value;
}

const inputRef = ref<HTMLInputElement | HTMLTextAreaElement | null>(null);

onMounted(() => {
  if (props.autofocus && inputRef.value) {
    inputRef.value.focus();
  }
});

function onInput(event: Event): void {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement;
  emit('update:modelValue', target.value);
}

function onKeyup(event: KeyboardEvent): void {
  emit('keyup', event);
  if (event.key === 'Enter') {
    emit('enter', event);
  }
}

const sizeClasses: Record<InputSize, string> = {
  sm: 'h-8 px-3 text-[13px] rounded-sm',
  md: 'h-[38px] px-3 text-sm rounded-md',
};
const sizeClass = computed(() => sizeClasses[currentSize.value]);

const textareaStyle = computed(() => {
  const minRows = props.autosize?.minRows ?? 3;
  return { minHeight: `${(minRows * 1.6).toFixed(1)}rem` };
});
</script>

<template>
  <div class="relative inline-flex w-full min-w-0">
    <textarea
      v-if="currentType === 'textarea'"
      ref="inputRef"
      :value="modelValue"
      :placeholder="placeholder"
      :readonly="readonly"
      :disabled="disabled"
      :autocomplete="autocomplete"
      :aria-invalid="error || undefined"
      :class="[
        'c-control-focus readonly:bg-surface-2 readonly:text-text readonly:font-mono readonly:text-[13px] w-full min-w-0 resize-y rounded-md border border-border bg-surface px-3 py-2 text-sm leading-relaxed text-text placeholder:text-muted/60 hover:border-border-strong disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted/60',
        error ? 'border-error-500 ring-2 ring-error-500/20' : '',
      ]"
      :style="textareaStyle"
      @input="onInput"
      @keyup="onKeyup"
    />
    <input
      v-else
      ref="inputRef"
      :type="actualInputType"
      :value="modelValue"
      :placeholder="placeholder"
      :readonly="readonly"
      :disabled="disabled"
      :autocomplete="autocomplete"
      :aria-invalid="error || undefined"
      :class="[
        'c-control-focus readonly:bg-surface-2 readonly:text-text readonly:font-mono readonly:text-[13px] w-full min-w-0 border border-border bg-surface text-text placeholder:text-muted/60 hover:border-border-strong disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted/60',
        sizeClass,
        error ? 'border-error-500 ring-2 ring-error-500/20' : '',
        currentType === 'password' ? 'pr-[38px]' : '',
      ]"
      @input="onInput"
      @keyup="onKeyup"
    />
    <button
      v-if="currentType === 'password' && showPasswordToggle"
      type="button"
      class="absolute top-0 right-0 inline-flex h-full w-[38px] items-center justify-center bg-transparent text-muted hover:text-text"
      tabindex="-1"
      @click="togglePassword"
    >
      <Eye v-if="isPasswordVisible" :size="16" />
      <EyeOff v-else :size="16" />
    </button>
  </div>
</template>
