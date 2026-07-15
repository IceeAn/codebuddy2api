<script setup lang="ts">
import { computed, useId } from 'vue';

interface Props {
  percentage?: number;
  strokeWidth?: number;
  size?: number;
  thresholdColors?: boolean;
  variant?: 'credential' | 'success-rate' | 'cache-hit';
  label?: string;
}

const props = withDefaults(defineProps<Props>(), {
  percentage: 0,
  strokeWidth: 8,
  size: 80,
  thresholdColors: true,
  variant: 'credential',
  label: undefined,
});

const clampedPercentage = computed(() => {
  if (!Number.isFinite(props.percentage)) return 0;
  return Math.max(0, Math.min(100, props.percentage));
});
const gradientId = `c-progress-gradient-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;

const radius = computed(() => (props.size - props.strokeWidth) / 2);
const center = computed(() => props.size / 2);
const circumference = computed(() => 2 * Math.PI * radius.value);
const dashoffset = computed(() => circumference.value * (1 - clampedPercentage.value / 100));

const thresholdStroke = computed(() => {
  const p = clampedPercentage.value;
  if (props.variant === 'cache-hit') return 'var(--color-brand-500)';
  if (props.variant === 'success-rate') {
    if (p >= 80) return 'var(--color-success-500)';
    if (p >= 20) return 'var(--color-warning-500)';
    return 'var(--color-error-500)';
  }
  if (p >= 80) return 'var(--color-brand-500)';
  if (p >= 50) return 'var(--color-warning-500)';
  return 'var(--color-error-500)';
});

const progressStroke = computed(() => {
  return props.thresholdColors ? thresholdStroke.value : `url(#${gradientId})`;
});
const trackStroke = computed(() =>
  props.variant === 'cache-hit'
    ? 'color-mix(in oklch, var(--color-brand-500) 20%, var(--surface))'
    : 'var(--color-surface-3)',
);

const textClass = computed(() => (props.size < 64 ? 'text-[13px]' : 'text-[18px]'));
</script>

<template>
  <svg
    :width="size"
    :height="size"
    :viewBox="`0 0 ${size} ${size}`"
    :style="{ transform: 'rotate(-90deg)' }"
    role="progressbar"
    aria-label="进度"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-valuenow="clampedPercentage"
  >
    <defs v-if="!thresholdColors">
      <linearGradient :id="gradientId" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="var(--color-brand-500)" />
        <stop offset="100%" stop-color="var(--color-accent-400)" />
      </linearGradient>
    </defs>

    <circle
      :cx="center"
      :cy="center"
      :r="radius"
      fill="none"
      :stroke-width="strokeWidth"
      :stroke="trackStroke"
    />
    <circle
      :cx="center"
      :cy="center"
      :r="radius"
      fill="none"
      :stroke-width="strokeWidth"
      :stroke="progressStroke"
      stroke-linecap="round"
      :stroke-dasharray="circumference"
      :stroke-dashoffset="dashoffset"
      :style="{ transition: 'stroke-dashoffset 600ms var(--ease-out-quad)' }"
    />
    <text
      :x="center"
      :y="center"
      text-anchor="middle"
      dominant-baseline="central"
      fill="currentColor"
      :class="['font-display font-bold text-text-strong tabular-nums', textClass]"
      :style="{ transform: 'rotate(90deg)', transformOrigin: 'center' }"
    >
      {{ label ?? `${clampedPercentage}%` }}
    </text>
  </svg>
</template>
