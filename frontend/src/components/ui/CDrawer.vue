<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { X } from '@lucide/vue';
import { registerOverlay } from './overlayStack';

interface Props {
  open: boolean;
  placement?: 'left' | 'right';
  width?: number;
  title?: string;
  ariaLabel?: string;
  closable?: boolean;
}

type DrawerPlacement = NonNullable<Props['placement']>;

const props = withDefaults(defineProps<Props>(), {
  open: false,
  placement: 'left',
  width: 296,
  title: undefined,
  ariaLabel: undefined,
  closable: true,
});

const emit = defineEmits<{
  'update:open': [value: boolean];
}>();

const maskRef = ref<HTMLElement | null>(null);
const panelRef = ref<HTMLElement | null>(null);
const titleId = `c-drawer-title-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
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
    elements: [mask, panel],
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

const currentPlacement = computed<DrawerPlacement>(() => props.placement);

const placementClasses: Record<DrawerPlacement, string> = {
  left: 'left-0',
  right: 'right-0',
};
const placementClass = computed(() => placementClasses[currentPlacement.value]);
const transitionName = computed(() =>
  currentPlacement.value === 'left' ? 'c-drawer-panel-left' : 'c-drawer-panel-right',
);
</script>

<template>
  <Teleport to="body">
    <Transition name="c-drawer-mask">
      <div
        v-if="open"
        ref="maskRef"
        class="c-drawer-mask fixed inset-0 z-40 bg-[var(--color-overlay)] backdrop-blur-[2px]"
        @click="close"
      />
    </Transition>
    <Transition :name="transitionName">
      <div
        v-if="open"
        ref="panelRef"
        :class="[
          'c-drawer-panel fixed top-0 bottom-0 z-50 flex flex-col bg-surface shadow-2xl',
          placementClass,
        ]"
        :style="{ width: width + 'px' }"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="title ? titleId : undefined"
        :aria-label="title ? undefined : ariaLabel || '抽屉'"
        tabindex="-1"
      >
        <div
          v-if="title || closable"
          class="c-drawer-header flex h-14 items-center justify-between border-b border-border px-4"
        >
          <div v-if="title" :id="titleId" class="c-drawer-title font-display text-md font-semibold">
            {{ title }}
          </div>
          <div v-else />
          <button
            v-if="closable"
            type="button"
            class="c-drawer-close inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-text"
            aria-label="关闭"
            @click="close"
          >
            <X :size="16" />
          </button>
        </div>
        <div class="c-drawer-body flex-1 overflow-y-auto p-4">
          <slot />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
