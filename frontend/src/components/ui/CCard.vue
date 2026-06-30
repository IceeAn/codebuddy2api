<script setup lang="ts">
import { computed, useSlots } from 'vue';

interface Props {
  title?: string;
  size?: 'default' | 'small';
  interactive?: boolean;
}

type CardSize = NonNullable<Props['size']>;

const props = withDefaults(defineProps<Props>(), {
  title: undefined,
  size: 'default',
  interactive: false,
});

const slots = useSlots();
const currentSize = computed<CardSize>(() => props.size);

const sizeClasses: Record<CardSize, string> = {
  default: 'p-5 rounded-xl',
  small: 'p-4 rounded-lg',
};
const sizeClass = computed(() => sizeClasses[currentSize.value]);

function hasHeader(): boolean {
  return Boolean(props.title) || Boolean(slots.header) || Boolean(slots['header-extra']);
}
</script>

<template>
  <div
    :class="[
      'border border-border bg-surface shadow-[var(--shadow-card)]',
      'flex h-full min-w-0 flex-col',
      sizeClass,
      interactive
        ? 'transition-[transform,box-shadow] duration-[var(--duration-base)] hover:-translate-y-0.5 hover:shadow-[var(--shadow-card-lg)]'
        : '',
    ]"
  >
    <div
      v-if="hasHeader()"
      class="c-card-header flex items-center justify-between gap-3 border-b border-border pb-4"
    >
      <div class="min-w-0 flex-1">
        <slot name="header">
          <div
            v-if="title"
            class="c-card-title font-display text-md font-semibold text-text-strong"
          >
            {{ title }}
          </div>
        </slot>
      </div>
      <div v-if="$slots['header-extra']" class="c-card-header-extra ml-auto shrink-0">
        <slot name="header-extra" />
      </div>
    </div>

    <div :class="['c-card-body min-w-0 flex-1', hasHeader() ? 'pt-4' : '']">
      <slot />
    </div>

    <div v-if="$slots.footer" class="c-card-footer border-t border-border pt-4">
      <slot name="footer" />
    </div>
  </div>
</template>
