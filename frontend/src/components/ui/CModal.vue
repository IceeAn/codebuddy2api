<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue';
import { X } from '@lucide/vue';

interface Props {
  open: boolean;
  title?: string;
  closable?: boolean;
  width?: string;
}

const props = withDefaults(defineProps<Props>(), {
  open: false,
  title: undefined,
  closable: true,
  width: 'min(30rem, 90vw)',
});

const emit = defineEmits<{
  'update:open': [value: boolean];
}>();

function close(): void {
  emit('update:open', false);
}

/** ESC 键处理（仅在 open 时通过 listener 注册，故无需再判 open）。 */
function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    close();
  }
}

function setBodyLock(locked: boolean): void {
  document.body.style.overflow = locked ? 'hidden' : '';
}

watch(
  () => props.open,
  (open) => {
    setBodyLock(open);
    if (open) {
      document.addEventListener('keydown', handleKeydown);
    } else {
      document.removeEventListener('keydown', handleKeydown);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown);
  setBodyLock(false);
});
</script>

<template>
  <Teleport to="body">
    <Transition name="c-modal-mask">
      <div
        v-if="open"
        class="c-modal-mask fixed inset-0 z-40 flex items-center justify-center bg-[var(--color-overlay)] p-4 backdrop-blur-[2px]"
        @click="close"
      >
        <Transition name="c-modal-panel">
          <div
            class="c-modal-panel flex max-h-[85vh] flex-col overflow-hidden rounded-2xl bg-surface shadow-[var(--shadow-card-lg)]"
            :style="{ width: width }"
            @click.stop
          >
            <div
              v-if="title || closable"
              class="c-modal-header flex h-14 items-center border-b border-border px-5"
            >
              <div v-if="title" class="c-modal-title flex-1 font-display text-md font-semibold">
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
