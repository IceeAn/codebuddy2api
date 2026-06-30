<script setup lang="ts">
import { computed } from 'vue';

interface Props {
  percentage?: number;
  strokeWidth?: number;
  size?: number;
  thresholdColors?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  percentage: 0,
  strokeWidth: 8,
  size: 80,
  thresholdColors: true,
});

const clampedPercentage = computed(() => {
  return Math.max(0, Math.min(100, props.percentage));
});

const radius = computed(() => (props.size - props.strokeWidth) / 2);
const center = computed(() => props.size / 2);
const circumference = computed(() => 2 * Math.PI * radius.value);
const dashoffset = computed(() => circumference.value * (1 - clampedPercentage.value / 100));

const thresholdStroke = computed(() => {
  const p = clampedPercentage.value;
  if (p >= 80) return 'var(--color-brand-500)';
  if (p >= 50) return 'var(--color-warning-500)';
  return 'var(--color-error-500)';
});

const progressStroke = computed(() => {
  return props.thresholdColors ? thresholdStroke.value : 'url(#c-progress-gradient)';
});

const textClass = computed(() => (props.size < 64 ? 'text-[13px]' : 'text-[18px]'));
</script>

<template>
  <svg
    :width="size"
    :height="size"
    :viewBox="`0 0 ${size} ${size}`"
    :style="{ transform: 'rotate(-90deg)' }"
  >
    <defs v-if="!thresholdColors">
      <linearGradient id="c-progress-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
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
      stroke="var(--color-surface-3)"
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
      {{ clampedPercentage }}%
    </text>
  </svg>
</template>
