<script setup lang="ts">
import { computed, provide, ref } from 'vue';

export interface FormRule {
  required?: boolean;
  message?: string;
  trigger?: 'blur' | 'input';
  whitespace?: boolean;
  /**
   * 自定义校验器。
   * 返回 true 表示通过；返回 false 表示失败（使用 message）；返回 string 表示失败且用该字符串作为错误消息。
   */
  validator?: (value: unknown) => boolean | string;
}

export type FormRules = Record<string, FormRule | FormRule[]>;

interface FormItemExpose {
  path: string;
  validate: () => Promise<string | null>;
  restoreValidation: () => void;
}

interface Props {
  model: Record<string, unknown>;
  rules?: FormRules;
  labelPlacement?: 'left' | 'top';
  labelWidth?: string;
  requireMarkPlacement?: 'right-hanging';
}

const props = withDefaults(defineProps<Props>(), {
  rules: () => ({}),
  labelPlacement: 'left',
  labelWidth: '12rem',
  requireMarkPlacement: 'right-hanging',
});

const items = ref<FormItemExpose[]>([]);

function registerItem(item: FormItemExpose): void {
  items.value.push(item);
}

function unregisterItem(item: FormItemExpose): void {
  const idx = items.value.indexOf(item);
  items.value.splice(idx, 1);
}

provide('c-form-context', {
  // 用 computed 包装，使 FormItem 能响应 props 变化
  rules: computed(() => props.rules),
  model: computed(() => props.model),
  registerItem,
  unregisterItem,
  labelPlacement: computed(() => props.labelPlacement),
});

async function validate(): Promise<void> {
  const errors: { field: string; message: string }[] = [];
  await Promise.all(
    items.value.map(async (item) => {
      const msg = await item.validate();
      if (msg) {
        errors.push({ field: item.path, message: msg });
      }
    }),
  );
  if (errors.length > 0) {
    throw errors;
  }
}

function restoreValidation(): void {
  items.value.forEach((item) => item.restoreValidation());
}

defineExpose({ validate, restoreValidation });
</script>

<template>
  <form
    :class="[
      labelPlacement === 'left'
        ? 'grid grid-cols-1 items-start gap-y-0 md:grid-cols-[var(--label-width,12rem)_minmax(0,1fr)] md:gap-x-4 md:gap-y-0'
        : 'flex flex-col gap-0',
    ]"
    :style="{ '--label-width': labelWidth }"
    @submit.prevent
  >
    <slot />
  </form>
</template>
