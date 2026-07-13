<script setup lang="ts">
import { computed, ref } from 'vue';
import { AlertTriangle, CheckCircle2, Info, XCircle, X } from '@lucide/vue';

interface Props {
  type?: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  closable?: boolean;
  showIcon?: boolean;
}

type AlertType = NonNullable<Props['type']>;

const props = withDefaults(defineProps<Props>(), {
  type: 'info',
  title: undefined,
  closable: false,
  showIcon: true,
});

const emit = defineEmits<{
  close: [];
}>();

const visible = ref(true);
const currentType = computed<AlertType>(() => props.type);
const alertRole = computed(() =>
  currentType.value === 'error' || currentType.value === 'warning' ? 'alert' : 'status',
);

const typeClasses: Record<AlertType, string> = {
  info: 'border-l-brand-500 bg-brand-500/[0.07]',
  success: 'border-l-success-500 bg-success-500/[0.08]',
  warning: 'border-l-warning-500 bg-warning-500/[0.10]',
  error: 'border-l-error-500 bg-error-500/[0.08]',
};

const iconColorClasses: Record<AlertType, string> = {
  info: 'text-tone-brand',
  success: 'text-tone-success',
  warning: 'text-tone-warning',
  error: 'text-tone-error',
};

const iconMap = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const;

const typeClass = computed(() => typeClasses[currentType.value]);
const iconColorClass = computed(() => iconColorClasses[currentType.value]);
const iconComponent = computed(() => iconMap[currentType.value]);

function handleClose(): void {
  visible.value = false;
  emit('close');
}
</script>

<template>
  <div
    v-if="visible"
    :role="alertRole"
    :class="['flex items-start gap-3 rounded-lg border-l-4 p-3.5', typeClass]"
  >
    <span v-if="showIcon" :class="['c-alert-icon shrink-0', iconColorClass]">
      <component :is="iconComponent" :size="18" />
    </span>

    <div class="min-w-0 flex-1">
      <div v-if="title" class="c-alert-title font-display text-sm font-semibold text-text-strong">
        {{ title }}
      </div>
      <div v-if="$slots.default" class="c-alert-content text-sm text-text">
        <slot />
      </div>
    </div>

    <div v-if="$slots.action" class="c-alert-action shrink-0">
      <slot name="action" />
    </div>

    <button
      v-if="closable"
      type="button"
      class="c-alert-close inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-text"
      aria-label="关闭"
      @click="handleClose"
    >
      <X :size="14" />
    </button>
  </div>
</template>
