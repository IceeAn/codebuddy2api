<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref, useId } from 'vue';

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
const fallbackTabIndex = ref<0 | undefined>();
const tooltipId = `c-tooltip-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
const positionStyle = ref<Record<string, string>>({
  left: '0px',
  top: '0px',
});

let showTimer: ReturnType<typeof setTimeout> | null = null;
let hovered = false;
let focused = false;
let focusedTarget: HTMLElement | null = null;
let describedElement: HTMLElement | null = null;
let previousDescribedBy: string | null = null;
let mounted = false;
const viewportPadding = 8;
const popoverGap = 4;
const focusableDescendantSelector = [
  'a[href]',
  'area[href]',
  'button:not(:disabled)',
  'input:not(:disabled):not([type="hidden"])',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  'summary',
  'iframe',
  'audio[controls]',
  'video[controls]',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]:not([tabindex^="-"])',
].join(',');

function updateFallbackTabStop(): void {
  fallbackTabIndex.value = triggerRef.value!.querySelector(focusableDescendantSelector)
    ? undefined
    : 0;
}

function scheduleShow(): void {
  if (visible.value || showTimer !== null) return;
  showTimer = setTimeout(() => {
    showTimer = null;
    void show();
  }, props.delay);
}

function cancelShowTimer(): void {
  if (showTimer !== null) {
    clearTimeout(showTimer);
    showTimer = null;
  }
}

function handleEnter(): void {
  hovered = true;
  scheduleShow();
}

function handleLeave(): void {
  hovered = false;
  cancelShowTimer();
  if (!focused && !pinned.value) hide();
}

function handleFocusIn(event: FocusEvent): void {
  focused = true;
  focusedTarget = event.target as HTMLElement;
  scheduleShow();
}

function handleFocusOut(event: FocusEvent): void {
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && triggerRef.value?.contains(nextTarget)) return;
  focused = false;
  focusedTarget = null;
  cancelShowTimer();
  if (!hovered && !pinned.value) hide();
}

function handleClick(): void {
  if (!props.clickable) return;
  cancelShowTimer();
  if (visible.value && pinned.value) {
    hide();
    return;
  }
  pinned.value = true;
  if (!visible.value) void show();
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    cancelShowTimer();
    hide();
    return;
  }
  if (!props.clickable || (event.key !== 'Enter' && event.key !== ' ')) return;
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
  if (!mounted || !visible.value) return;
  updatePosition();
  applyDescription();
  window.addEventListener('scroll', updatePosition, true);
  window.addEventListener('resize', updatePosition);
  if (props.clickable) window.addEventListener('pointerdown', handleOutsidePointer);
}

function hide(): void {
  const shouldBlur = pinned.value;
  visible.value = false;
  positioned.value = false;
  pinned.value = false;
  restoreDescription();
  removeListeners();
  if (shouldBlur) {
    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && triggerRef.value?.contains(activeElement)) {
      activeElement.blur();
    }
  }
}

function accessibleTrigger(): HTMLElement {
  return (
    focusedTarget ??
    triggerRef.value?.querySelector<HTMLElement>(focusableDescendantSelector) ??
    triggerRef.value!
  );
}

function applyDescription(): void {
  const target = accessibleTrigger();
  describedElement = target;
  previousDescribedBy = target.getAttribute('aria-describedby');
  const ids = new Set((previousDescribedBy ?? '').split(/\s+/).filter(Boolean));
  ids.add(tooltipId);
  target.setAttribute('aria-describedby', Array.from(ids).join(' '));
}

function restoreDescription(): void {
  if (!describedElement) return;
  if (previousDescribedBy === null) describedElement.removeAttribute('aria-describedby');
  else describedElement.setAttribute('aria-describedby', previousDescribedBy);
  describedElement = null;
  previousDescribedBy = null;
}

function removeListeners(): void {
  window.removeEventListener('scroll', updatePosition, true);
  window.removeEventListener('resize', updatePosition);
  window.removeEventListener('pointerdown', handleOutsidePointer);
}

/** 根据触发元素和浮层尺寸计算坐标，并限制在视口内。 */
function updatePosition(): void {
  const trigger = triggerRef.value!;
  const popover = popoverRef.value!;

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

onMounted(() => {
  mounted = true;
  updateFallbackTabStop();
});
onUpdated(updateFallbackTabStop);

onBeforeUnmount(() => {
  mounted = false;
  cancelShowTimer();
  hide();
});
</script>

<template>
  <span
    ref="triggerRef"
    class="relative inline-flex"
    :tabindex="fallbackTabIndex"
    :aria-expanded="clickable ? visible : undefined"
    @mouseenter="handleEnter"
    @mouseleave="handleLeave"
    @focusin="handleFocusIn"
    @focusout="handleFocusOut"
    @click="handleClick"
    @keydown="handleKeydown"
  >
    <slot />
    <Teleport to="body">
      <Transition name="c-tooltip">
        <span
          v-if="visible"
          :id="tooltipId"
          ref="popoverRef"
          :style="positionStyle"
          :class="[
            'c-tooltip-popover fixed z-50 w-max max-w-[20rem] rounded-md bg-tooltip px-2.5 py-1.5 text-xs break-words whitespace-normal text-tooltip-text shadow-(--shadow-popover)',
            positioned && clickable ? '' : 'pointer-events-none',
            positioned ? '' : 'opacity-0',
            placementClass,
          ]"
          role="tooltip"
        >
          <slot name="content">{{ content }}</slot>
        </span>
      </Transition>
    </Teleport>
  </span>
</template>
