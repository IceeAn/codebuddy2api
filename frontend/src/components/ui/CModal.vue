<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { X } from '@lucide/vue';
import { registerOverlay } from './overlayStack';

interface Props {
  open: boolean;
  title?: string;
  ariaLabel?: string;
  closable?: boolean;
  width?: string;
}

const props = withDefaults(defineProps<Props>(), {
  open: false,
  title: undefined,
  ariaLabel: undefined,
  closable: true,
  width: 'min(30rem, 90vw)',
});

const emit = defineEmits<{
  'update:open': [value: boolean];
}>();

const maskRef = ref<HTMLElement | null>(null);
const panelRef = ref<HTMLElement | null>(null);
const titleId = `c-modal-title-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
let unregisterOverlay: (() => void) | null = null;

function close(): void {
  if (!props.closable) return;
  emit('update:open', false);
}

function deactivateOverlay(): void {
  unregisterOverlay?.();
  unregisterOverlay = null;
}

function activateOverlay(): void {
  const mask = maskRef.value!;
  const panel = panelRef.value!;
  unregisterOverlay = registerOverlay({
    elements: [mask],
    focusRoot: panel,
    modal: true,
    onEscape: props.closable ? close : undefined,
  });
}

watch(
  () => props.open,
  (open) => {
    if (open) activateOverlay();
    else deactivateOverlay();
  },
  { flush: 'post' },
);

onMounted(() => {
  if (props.open) activateOverlay();
});

onBeforeUnmount(() => {
  deactivateOverlay();
});
</script>

<template>
  <Teleport to="body">
    <Transition name="c-modal-mask">
      <div
        v-if="open"
        ref="maskRef"
        class="c-modal-mask fixed inset-0 z-40 flex items-center justify-center bg-[var(--color-overlay)] p-4 backdrop-blur-[2px]"
        @click="close"
      >
        <Transition name="c-modal-panel">
          <div
            ref="panelRef"
            class="c-modal-panel flex max-h-[85vh] flex-col overflow-hidden rounded-2xl bg-surface shadow-[var(--shadow-card-lg)]"
            :style="{ width: width }"
            role="dialog"
            aria-modal="true"
            :aria-labelledby="title ? titleId : undefined"
            :aria-label="title ? undefined : ariaLabel || '对话框'"
            tabindex="-1"
            @click.stop
          >
            <div
              v-if="title || closable"
              class="c-modal-header flex h-14 items-center border-b border-border px-5"
            >
              <div
                v-if="title"
                :id="titleId"
                class="c-modal-title flex-1 font-display text-md font-semibold"
              >
                {{ title }}
              </div>
              <div v-else class="flex-1" />
              <button
                v-if="closable"
                type="button"
                class="c-modal-close inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-text"
                aria-label="关闭"
                @click="close"
              >
                <X :size="16" />
              </button>
            </div>
            <div class="c-modal-body overflow-y-auto p-5">
              <slot />
            </div>
            <div
              v-if="$slots.footer"
              class="c-modal-footer flex justify-end gap-2 border-t border-border bg-surface-2/40 px-5 py-4"
            >
              <slot name="footer" />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
