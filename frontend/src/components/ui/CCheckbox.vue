<script setup lang="ts">
import { computed, inject, onMounted, ref, useId, watch } from 'vue';
import { Check } from '@lucide/vue';
import { formItemControlKey } from './formContext';

interface Props {
  id?: string;
  modelValue?: boolean;
  disabled?: boolean;
  indeterminate?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  id: undefined,
  modelValue: false,
  disabled: false,
  indeterminate: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
}>();

const isActive = computed(() => props.modelValue || props.indeterminate);
const inputRef = ref<HTMLInputElement | null>(null);
const formItem = inject(formItemControlKey, null);
const ownId = `c-checkbox-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
const controlId = computed(() => props.id ?? formItem?.controlId ?? ownId);

function syncIndeterminate(): void {
  inputRef.value!.indeterminate = props.indeterminate;
}

onMounted(syncIndeterminate);
watch(() => props.indeterminate, syncIndeterminate, { flush: 'post' });

function handleChange(event: Event): void {
  emit('update:modelValue', (event.currentTarget as HTMLInputElement).checked);
  formItem?.onInput();
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
      :id="controlId"
      ref="inputRef"
      type="checkbox"
      class="peer sr-only"
      :checked="modelValue"
      :disabled="disabled"
      :aria-checked="indeterminate ? 'mixed' : modelValue"
      :aria-labelledby="formItem?.labelId.value"
      :aria-invalid="formItem?.invalid.value || undefined"
      :aria-describedby="formItem?.describedBy.value"
      @change="handleChange"
      @blur="formItem?.onBlur"
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
