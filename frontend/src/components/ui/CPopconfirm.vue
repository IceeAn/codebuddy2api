<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue';
import { AlertTriangle } from '@lucide/vue';
import CButton from './CButton.vue';

interface Props {
  title?: string;
  confirmText?: string;
  cancelText?: string;
  confirmVariant?: 'primary' | 'danger';
}

const props = withDefaults(defineProps<Props>(), {
  title: undefined,
  confirmText: '确认',
  cancelText: '取消',
  confirmVariant: 'danger',
});

const emit = defineEmits<{
  confirm: [];
  cancel: [];
}>();

const visible = ref(false);
const positioned = ref(false);
const triggerRef = ref<HTMLElement | null>(null);
const popoverRef = ref<HTMLElement | null>(null);
const positionStyle = ref<Record<string, string>>({
  left: '0px',
  top: '0px',
});

const viewportPadding = 8;
const popoverGap = 8;

function toggle(): void {
  if (visible.value) {
    close();
    return;
  }
  void open();
}

async function open(): Promise<void> {
  positioned.value = false;
  visible.value = true;
  await nextTick();
  if (!visible.value) return;
  updatePosition();
  document.addEventListener('click', handleOutsideClick);
  document.addEventListener('keydown', handleKeydown);
  window.addEventListener('scroll', updatePosition, true);
  window.addEventListener('resize', updatePosition);
}

function close(): void {
  visible.value = false;
  positioned.value = false;
  removeListeners();
}

function removeListeners(): void {
  document.removeEventListener('click', handleOutsideClick);
  document.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('scroll', updatePosition, true);
  window.removeEventListener('resize', updatePosition);
}

/** 外部点击关闭：触发区和浮层内 click.stop 阻止冒泡。 */
function handleOutsideClick(): void {
  close();
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    close();
  }
}

function handleConfirm(): void {
  emit('confirm');
  close();
}

function handleCancel(): void {
  emit('cancel');
  close();
}

function updatePosition(): void {
  const triggerRect = triggerRef.value!.getBoundingClientRect();
  const popoverRect = popoverRef.value!.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const preferredLeft = triggerRect.left + triggerRect.width / 2 - popoverRect.width / 2;
  const preferredTop = triggerRect.bottom + popoverGap;
  const maxLeft = Math.max(viewportPadding, viewportWidth - popoverRect.width - viewportPadding);
  const maxTop = Math.max(viewportPadding, viewportHeight - popoverRect.height - viewportPadding);

  positionStyle.value = {
    left: `${Math.min(Math.max(preferredLeft, viewportPadding), maxLeft)}px`,
    top: `${Math.min(Math.max(preferredTop, viewportPadding), maxTop)}px`,
  };
  positioned.value = true;
}

onBeforeUnmount(() => {
  removeListeners();
});
</script>

<template>
  <span class="relative inline-flex">
    <span ref="triggerRef" class="inline-flex" @click.stop="toggle">
      <slot />
    </span>
    <Teleport to="body">
      <Transition name="c-popconfirm">
        <div
          v-if="visible"
          ref="popoverRef"
          :style="positionStyle"
          :class="[
            'c-popconfirm-popover fixed z-50 w-[15rem] rounded-lg border border-border bg-surface p-3.5 shadow-[var(--shadow-popover)]',
            positioned ? '' : 'pointer-events-none opacity-0',
          ]"
          @click.stop
        >
          <div class="flex gap-2.5">
            <span class="c-popconfirm-icon shrink-0 text-warning-500">
              <AlertTriangle :size="18" />
            </span>
            <div v-if="title" class="c-popconfirm-desc flex-1 text-sm text-text">
              {{ title }}
            </div>
          </div>
          <div class="c-popconfirm-actions mt-3 flex justify-end gap-2">
            <CButton size="sm" variant="ghost" @click="handleCancel">
              {{ cancelText }}
            </CButton>
            <CButton
              size="sm"
              :variant="confirmVariant === 'danger' ? 'danger' : 'primary'"
              @click="handleConfirm"
            >
              {{ confirmText }}
            </CButton>
          </div>
        </div>
      </Transition>
    </Teleport>
  </span>
</template>
