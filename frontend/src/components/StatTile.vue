<script setup lang="ts">
import type { Component } from 'vue';
import { computed } from 'vue';

interface Props {
  label: string;
  value: string | number;
  tone: 'brand' | 'success' | 'warning' | 'error' | 'accent';
  icon: Component;
  meta?: string;
  valueClass?: string;
}

const props = defineProps<Props>();

const toneClasses: Record<Props['tone'], string> = {
  brand: 'bg-stat-brand-bg text-stat-brand',
  success: 'bg-stat-success-bg text-tone-success',
  warning: 'bg-stat-warning-bg text-tone-warning',
  error: 'bg-stat-error-bg text-tone-error',
  accent: 'bg-stat-accent-bg text-tone-accent',
};

const iconBoxClass = computed(() => toneClasses[props.tone]);
</script>

<template>
  <section
    class="relative flex min-h-31 items-start gap-4 rounded-xl border border-border bg-surface p-4.5 shadow-(--shadow-card) transition-[translate,box-shadow] duration-200 ease-out-quad hover:-translate-y-0.5 hover:shadow-(--shadow-card-lg)"
  >
    <div v-if="$slots.corner" class="absolute top-4 right-4">
      <slot name="corner" />
    </div>
    <div :class="['grid h-10.5 w-10.5 place-items-center rounded-md', iconBoxClass]">
      <component :is="icon" :size="22" />
    </div>
    <div class="min-w-0 pr-14">
      <div
        :class="[
          'mt-0.5 font-display text-[28px] leading-none font-bold text-text-strong tabular-nums',
          valueClass,
        ]"
      >
        {{ value }}
      </div>
      <div class="mt-2 text-[13px] text-muted">{{ label }}</div>
      <div v-if="meta" class="mt-2.5 text-xs text-muted">{{ meta }}</div>
    </div>
  </section>
</template>
