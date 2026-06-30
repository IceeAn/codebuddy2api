<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue';
import { X } from '@lucide/vue';

interface Props {
  open: boolean;
  placement?: 'left' | 'right';
  width?: number;
  title?: string;
  closable?: boolean;
}

type DrawerPlacement = NonNullable<Props['placement']>;

const props = withDefaults(defineProps<Props>(), {
  open: false,
  placement: 'left',
  width: 296,
  title: undefined,
  closable: true,
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
        class="c-drawer-mask fixed inset-0 z-40 bg-[var(--color-overlay)] backdrop-blur-[2px]"
        @click="close"
      />
    </Transition>
    <Transition :name="transitionName">
      <div
        v-if="open"
        :class="[
          'c-drawer-panel fixed top-0 bottom-0 z-50 flex flex-col bg-surface shadow-2xl',
          placementClass,
        ]"
        :style="{ width: width + 'px' }"
      >
        <div
          v-if="title || closable"
          class="c-drawer-header flex h-14 items-center justify-between border-b border-border px-4"
        >
          <div v-if="title" class="c-drawer-title font-display text-md font-semibold">
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
