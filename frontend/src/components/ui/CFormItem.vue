<script setup lang="ts">
import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  provide,
  useId,
  useSlots,
  type ComputedRef,
} from 'vue';
import type { FormRule, FormRules } from './CForm.vue';
import { formItemControlKey } from './formContext';

interface FormContext {
  rules: ComputedRef<FormRules>;
  model: ComputedRef<Record<string, unknown>>;
  registerItem: (item: FormItemExpose) => void;
  unregisterItem: (item: FormItemExpose) => void;
  labelPlacement: ComputedRef<'left' | 'top'>;
  errors: ComputedRef<Record<string, string>>;
  setFieldError: (path: string, error: string | null) => void;
}

interface FormItemExpose {
  path: string;
  validate: () => Promise<string | null>;
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

const instanceId = useId().replace(/[^A-Za-z0-9_-]/g, '');
const controlId = `c-form-control-${instanceId}`;
const labelElementId = `c-form-label-${instanceId}`;
const errorId = `c-form-error-${instanceId}`;

const rules = computed<FormRule[]>(() => {
  const r = formCtx.rules.value[props.path];
  if (!r) return [];
  return Array.isArray(r) ? r : [r];
});

const isRequired = computed(() => props.required || rules.value.some((r) => r.required));
const hasLabel = computed(() => Boolean(props.label || slots.label));
const labelId = computed(() => (hasLabel.value ? labelElementId : undefined));

const labelPlacement = computed<'left' | 'top'>(() => formCtx.labelPlacement.value);
const error = computed(() => formCtx.errors.value[props.path] ?? null);

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
  return validateRules(rules.value);
}

async function validateRules(rulesToApply: FormRule[]): Promise<string | null> {
  const value = formCtx.model.value[props.path];
  for (const rule of rulesToApply) {
    const msg = applyRule(rule, value);
    if (msg) {
      formCtx.setFieldError(props.path, msg);
      return msg;
    }
  }
  formCtx.setFieldError(props.path, null);
  return null;
}

function validateForTrigger(trigger: 'input' | 'blur'): void {
  const triggeredRules = rules.value.filter((rule) => rule.trigger === trigger);
  if (triggeredRules.length === 0 && !error.value) return;
  void validateRules(error.value ? rules.value : triggeredRules);
}

const expose: FormItemExpose = {
  get path() {
    return props.path;
  },
  validate,
};

provide(formItemControlKey, {
  controlId,
  labelId,
  errorId,
  invalid: computed(() => error.value !== null),
  describedBy: computed(() => (error.value ? errorId : undefined)),
  onInput: () => validateForTrigger('input'),
  onBlur: () => validateForTrigger('blur'),
});

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
        :id="labelElementId"
        :for="controlId"
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
        :id="labelElementId"
        :for="controlId"
        class="c-form-item-label mb-1.5 block max-w-full min-w-0 text-[13px] font-medium text-text"
      >
        <slot name="label">
          <span class="inline max-w-full min-w-0 break-words whitespace-normal">{{ label }}</span>
        </slot>
        <span v-if="isRequired" class="c-form-item-required ml-0.5 text-error-500">*</span>
      </label>
    </template>

    <div class="c-form-item-control min-w-0">
      <div
        :class="[
          'c-form-item-control-inner flex min-w-0 items-center',
          labelPlacement === 'left' ? 'mt-1.5 min-h-0 md:mt-0 md:min-h-[38px]' : 'min-h-[38px]',
        ]"
      >
        <slot />
      </div>
      <div
        v-if="error"
        :id="errorId"
        class="c-form-item-error mt-1 h-4 animate-[shake_0.3s_ease] text-xs text-tone-error"
        role="alert"
        aria-live="polite"
      >
        {{ error }}
      </div>
      <div v-else class="mt-1 h-4" aria-hidden="true"></div>
    </div>
  </div>
</template>
