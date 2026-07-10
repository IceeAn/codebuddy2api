<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, useSlots, type ComputedRef } from 'vue';
import type { FormRule, FormRules } from './CForm.vue';

interface FormContext {
  rules: ComputedRef<FormRules>;
  model: ComputedRef<Record<string, unknown>>;
  registerItem: (item: FormItemExpose) => void;
  unregisterItem: (item: FormItemExpose) => void;
  labelPlacement: ComputedRef<'left' | 'top'>;
}

interface FormItemExpose {
  path: string;
  validate: () => Promise<string | null>;
  restoreValidation: () => void;
}

interface Props {
  label?: string;
  path: string;
  required?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  label: undefined,
  required: false,
});
const slots = useSlots();

const ctx = inject<FormContext>('c-form-context');

if (!ctx) {
  throw new Error('CFormItem 必须在 CForm 内使用');
}

const formCtx: FormContext = ctx;

const error = ref<string | null>(null);

const rules = computed<FormRule[]>(() => {
  const r = formCtx.rules.value[props.path];
  if (!r) return [];
  return Array.isArray(r) ? r : [r];
});

const isRequired = computed(() => props.required || rules.value.some((r) => r.required));
const hasLabel = computed(() => Boolean(props.label || slots.label));

const labelPlacement = computed<'left' | 'top'>(() => formCtx.labelPlacement.value);

function applyRule(rule: FormRule, value: unknown): string | null {
  if (rule.required) {
    const isEmpty = value === undefined || value === null || value === '';
    const isWhitespace = rule.whitespace && typeof value === 'string' && value.trim() === '';
    if (isEmpty || isWhitespace) {
      return rule.message ?? '该字段必填';
    }
  }
  if (rule.validator) {
    const result = rule.validator(value);
    if (typeof result === 'string') return result;
    if (!result) return rule.message ?? '校验失败';
  }
  return null;
}

async function validate(): Promise<string | null> {
  const value = formCtx.model.value[props.path];
  for (const rule of rules.value) {
    const msg = applyRule(rule, value);
    if (msg) {
      error.value = msg;
      return msg;
    }
  }
  error.value = null;
  return null;
}

function restoreValidation(): void {
  error.value = null;
}

const expose: FormItemExpose = {
  get path() {
    return props.path;
  },
  validate,
  restoreValidation,
};

onMounted(() => {
  formCtx.registerItem(expose);
});

onBeforeUnmount(() => {
  formCtx.unregisterItem(expose);
});
</script>

<template>
  <div :class="['c-form-item min-w-0', labelPlacement === 'left' ? 'contents' : '']">
    <template v-if="labelPlacement === 'left'">
      <label
        v-if="hasLabel"
        class="c-form-item-label max-w-full min-w-0 text-left text-[13px] font-medium text-text md:flex md:min-h-[38px] md:items-center md:justify-end md:text-right"
      >
        <slot name="label">
          <span class="max-w-full min-w-0 break-words whitespace-normal">{{ label }}</span>
        </slot>
        <span v-if="isRequired" class="c-form-item-required ml-0.5 text-error-500">*</span>
      </label>
      <span v-else class="hidden md:block"></span>
    </template>
    <template v-else>
      <label
        v-if="hasLabel"
        class="c-form-item-label mb-1.5 block max-w-full min-w-0 text-[13px] font-medium text-text"
      >
        <slot name="label">
          <span class="inline max-w-full min-w-0 break-words whitespace-normal">{{ label }}</span>
        </slot>
        <span v-if="isRequired" class="c-form-item-required ml-0.5 text-error-500">*</span>
      </label>
    </template>

    <div class="c-form-item-control min-w-0">
      <div class="c-form-item-control-inner flex min-h-[38px] min-w-0 items-center">
        <slot />
      </div>
      <div
        v-if="error"
        class="c-form-item-error mt-1 h-4 animate-[shake_0.3s_ease] text-xs text-error-600 dark:text-error-400"
      >
        {{ error }}
      </div>
      <div v-else class="mt-1 h-4" aria-hidden="true"></div>
    </div>
  </div>
</template>
