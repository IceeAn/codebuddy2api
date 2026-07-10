<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue';

interface Props {
  content?: string;
  placement?: 'top' | 'bottom';
  delay?: number;
  clickable?: boolean;
}

type Placement = NonNullable<Props['placement']>;

const props = withDefaults(defineProps<Props>(), {
  content: undefined,
  placement: 'top',
  delay: 300,
  clickable: false,
});

const currentPlacement = computed<Placement>(() => props.placement);

const visible = ref(false);
const positioned = ref(false);
const pinned = ref(false);
const triggerRef = ref<HTMLElement | null>(null);
const popoverRef = ref<HTMLElement | null>(null);
const positionStyle = ref<Record<string, string>>({
  left: '0px',
  top: '0px',
});

let showTimer: ReturnType<typeof setTimeout> | null = null;
const viewportPadding = 8;
const popoverGap = 4;

function handleEnter(): void {
  showTimer = setTimeout(() => {
    void show();
  }, props.delay);
}

function handleLeave(): void {
  if (showTimer !== null) {
    clearTimeout(showTimer);
    showTimer = null;
  }
  if (!pinned.value) hide();
}

function handleClick(): void {
  if (!props.clickable) return;
  if (showTimer !== null) {
    clearTimeout(showTimer);
    showTimer = null;
  }
  if (visible.value && pinned.value) {
    hide();
    return;
  }
  pinned.value = true;
  if (!visible.value) void show();
}

function handleKeydown(event: KeyboardEvent): void {
  if (!props.clickable) return;
  if (event.key === 'Escape') {
    hide();
    return;
  }
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  handleClick();
}

function handleOutsidePointer(event: PointerEvent): void {
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (triggerRef.value?.contains(target) || popoverRef.value?.contains(target)) return;
  hide();
}

async function show(): Promise<void> {
  positioned.value = false;
  visible.value = true;
  await nextTick();
  updatePosition();
  window.addEventListener('scroll', updatePosition, true);
  window.addEventListener('resize', updatePosition);
  if (props.clickable) window.addEventListener('pointerdown', handleOutsidePointer);
}

function hide(): void {
  visible.value = false;
  positioned.value = false;
  pinned.value = false;
  const activeElement = document.activeElement;
  if (activeElement instanceof HTMLElement && triggerRef.value?.contains(activeElement)) {
    activeElement.blur();
  }
  removeListeners();
}

function removeListeners(): void {
  window.removeEventListener('scroll', updatePosition, true);
  window.removeEventListener('resize', updatePosition);
  window.removeEventListener('pointerdown', handleOutsidePointer);
}

/** 根据触发元素和浮层尺寸计算坐标，并限制在视口内。 */
function updatePosition(): void {
  const trigger = triggerRef.value;
  const popover = popoverRef.value;
  if (!trigger || !popover) return;

  const triggerRect = trigger.getBoundingClientRect();
  const popoverRect = popover.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const preferredTop =
    currentPlacement.value === 'bottom'
      ? triggerRect.bottom + popoverGap
      : triggerRect.top - popoverRect.height - popoverGap;
  const centeredLeft = triggerRect.left + triggerRect.width / 2 - popoverRect.width / 2;
  const maxLeft = Math.max(viewportPadding, viewportWidth - popoverRect.width - viewportPadding);
  const maxTop = Math.max(viewportPadding, viewportHeight - popoverRect.height - viewportPadding);

  positionStyle.value = {
    left: `${Math.min(Math.max(centeredLeft, viewportPadding), maxLeft)}px`,
    top: `${Math.min(Math.max(preferredTop, viewportPadding), maxTop)}px`,
  };
  positioned.value = true;
}

const placementClasses: Record<Placement, string> = {
  top: 'c-tooltip-placement-top',
  bottom: 'c-tooltip-placement-bottom',
};
const placementClass = computed(() => placementClasses[currentPlacement.value]);

onBeforeUnmount(() => {
  if (showTimer !== null) {
    clearTimeout(showTimer);
    showTimer = null;
  }
  removeListeners();
});
</script>

<template>
  <span
    ref="triggerRef"
    class="relative inline-flex"
    :aria-expanded="clickable ? visible : undefined"
    @mouseenter="handleEnter"
    @mouseleave="handleLeave"
    @click="handleClick"
    @keydown="handleKeydown"
  >
    <slot />
    <Teleport to="body">
      <Transition name="c-tooltip">
        <span
          v-if="visible"
          ref="popoverRef"
          :style="positionStyle"
          :class="[
            'c-tooltip-popover fixed z-50 w-max max-w-[20rem] rounded-md bg-slate-950 px-2.5 py-1.5 text-xs break-words whitespace-normal text-slate-50 shadow-(--shadow-popover) dark:bg-slate-700 dark:text-slate-100',
            positioned ? '' : 'pointer-events-none opacity-0',
            placementClass,
          ]"
        >
          <slot name="content">{{ content }}</slot>
        </span>
      </Transition>
    </Teleport>
  </span>
</template>
