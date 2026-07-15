<script setup lang="ts">
import {
  computed,
  inject,
  onBeforeUnmount,
  onUpdated,
  provide,
  reactive,
  ref,
  useId,
  watch,
} from 'vue';
import { formItemControlKey } from './formContext';

interface Props {
  id?: string;
  modelValue?: string;
  ariaLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  id: undefined,
  modelValue: '',
  ariaLabel: '选项',
});

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const current = ref(props.modelValue);
const values = ref<string[]>([]);
const buttons = new Map<string, Set<HTMLButtonElement>>();
const indicatorVisible = ref(false);
const indicatorStyle = reactive({
  width: '0px',
  transform: 'translateX(0px)',
});
const formItem = inject(formItemControlKey, null);
const ownId = `c-radio-group-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
const controlId = computed(() => props.id ?? formItem?.controlId ?? ownId);

function syncIndicator(): void {
  const activeButton = buttons.get(current.value)?.values().next().value;
  if (!activeButton) {
    indicatorVisible.value = false;
    return;
  }
  indicatorStyle.width = `${activeButton.offsetWidth}px`;
  indicatorStyle.transform = `translateX(${activeButton.offsetLeft}px)`;
  indicatorVisible.value = true;
}

const resizeObserver = new ResizeObserver(syncIndicator);

function registerButton(value: string, element: HTMLButtonElement): void {
  const registered = buttons.get(value) ?? new Set<HTMLButtonElement>();
  registered.add(element);
  buttons.set(value, registered);
  if (!values.value.includes(value)) values.value = [...values.value, value];
  resizeObserver.observe(element);
  syncIndicator();
}

function unregisterButton(value: string, element: HTMLButtonElement): void {
  resizeObserver.unobserve(element);
  const registered = buttons.get(value)!;
  registered.delete(element);
  if (registered.size === 0) {
    buttons.delete(value);
    values.value = values.value.filter((candidate) => candidate !== value);
  }
  syncIndicator();
}

watch(
  () => props.modelValue,
  (value) => {
    current.value = value;
    syncIndicator();
  },
);

onUpdated(syncIndicator);

onBeforeUnmount(() => {
  resizeObserver.disconnect();
});

function select(value: string): void {
  if (value === current.value) return;
  emit('update:modelValue', value);
  formItem?.onInput();
}

function tabIndex(value: string): 0 | -1 {
  if (current.value) return current.value === value ? 0 : -1;
  return values.value[0] === value ? 0 : -1;
}

function handleKeydown(event: KeyboardEvent): void {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[role="radio"]');
  if (!target) return;
  const radios = Array.from(
    (event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="radio"]'),
  );
  const currentIndex = radios.indexOf(target as HTMLButtonElement);

  let nextIndex: number;
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    nextIndex = (currentIndex + 1) % radios.length;
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    nextIndex = (currentIndex - 1 + radios.length) % radios.length;
  } else if (event.key === 'Home') {
    nextIndex = 0;
  } else if (event.key === 'End') {
    nextIndex = radios.length - 1;
  } else {
    return;
  }
  event.preventDefault();
  radios[nextIndex].focus();
  radios[nextIndex].click();
}

function handleFocusout(event: FocusEvent): void {
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && (event.currentTarget as HTMLElement).contains(nextTarget))
    return;
  formItem?.onBlur();
}

provide('c-radio-group', { current, select, registerButton, unregisterButton, tabIndex });
</script>

<template>
  <div
    :id="controlId"
    class="c-radio-group relative inline-flex rounded-md bg-surface-2 p-0.5"
    role="radiogroup"
    :aria-label="formItem?.labelId.value ? undefined : ariaLabel"
    :aria-labelledby="formItem?.labelId.value"
    :aria-invalid="formItem?.invalid.value || undefined"
    :aria-describedby="formItem?.describedBy.value"
    @keydown="handleKeydown"
    @focusout="handleFocusout"
  >
    <span
      v-show="indicatorVisible"
      class="c-radio-group-indicator pointer-events-none absolute top-0.5 bottom-0.5 left-0 rounded-sm bg-segment-active shadow-[var(--shadow-xs)] transition-transform duration-200 ease-out-quad"
      :style="indicatorStyle"
      aria-hidden="true"
    />
    <slot />
  </div>
</template>
