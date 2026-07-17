<script setup lang="ts">
import { computed } from 'vue';
import type { StatsMetric, StatsSeriesPoint } from '../types';
import { buildChartGeometry, metricDisplayValue, metricLabel } from '../utils/stats';
import type { ChartPoint } from '../utils/stats';
import CTooltip from './ui/CTooltip.vue';

const props = defineProps<{
  points: StatsSeriesPoint[];
  metric: StatsMetric;
  timezone: string;
}>();

const geometry = computed(() => buildChartGeometry(props.points, props.metric, 800, 240, 0));
const maximumOverflow = computed(() =>
  geometry.value.points.some((point) => point.value === geometry.value.maxValue && point.overflow),
);
const chartMinimumWidth = computed(() =>
  Math.max(320, Math.max(0, geometry.value.points.length - 1) * 24 + 24),
);
const firstPoint = computed(() => props.points[0]!);
const lastPoint = computed(() => props.points[props.points.length - 1]!);

function formatPointTime(point: Pick<ChartPoint, 'bucketStart' | 'period'>): string {
  const hourly = point.period?.includes('T') === true;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: props.timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(hourly
      ? {
          hour: '2-digit',
          minute: '2-digit',
          hourCycle: 'h23' as const,
        }
      : {}),
  }).format(new Date(point.bucketStart * 1000));
}

function formatSeriesPointTime(point: StatsSeriesPoint): string {
  return formatPointTime({ bucketStart: point.period_start, period: point.period });
}
</script>

<template>
  <div v-if="points.length === 0" class="grid min-h-64 place-items-center text-sm text-muted">
    当前筛选范围内暂无趋势数据
  </div>
  <div
    v-else-if="geometry.points.length === 0"
    class="grid min-h-64 place-items-center text-sm text-muted"
  >
    该指标暂无已知数据
  </div>
  <div v-else class="min-w-0">
    <div class="mb-2 flex items-center justify-between text-xs text-muted">
      <span>{{ metricLabel(metric) }}</span>
      <span>最大值 {{ metricDisplayValue(metric, geometry.maxValue, maximumOverflow) }}</span>
    </div>
    <div class="stats-trend-scroll overflow-x-auto pb-1">
      <div class="stats-trend-plot w-full" :style="{ minWidth: `${chartMinimumWidth}px` }">
        <div class="stats-trend-canvas stats-trend-fixed-gutter relative">
          <svg
            class="block h-60 w-full overflow-visible"
            viewBox="0 0 800 240"
            preserveAspectRatio="none"
            role="img"
            :aria-label="`${metricLabel(metric)}趋势图`"
          >
            <defs>
              <linearGradient id="stats-area-gradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="var(--color-brand-500)" stop-opacity="0.28" />
                <stop offset="100%" stop-color="var(--color-brand-500)" stop-opacity="0.02" />
              </linearGradient>
            </defs>
            <line x1="0" y1="216" x2="800" y2="216" stroke="var(--border)" stroke-width="1" />
            <line x1="0" y1="120" x2="800" y2="120" stroke="var(--border)" stroke-width="1" />
            <line x1="0" y1="24" x2="800" y2="24" stroke="var(--border)" stroke-width="1" />
            <path :d="geometry.areaPath" fill="url(#stats-area-gradient)" />
            <path
              class="stats-trend-line"
              :d="geometry.linePath"
              fill="none"
              stroke="var(--color-brand-500)"
              stroke-width="3"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <div class="pointer-events-none absolute inset-0">
            <div
              v-for="point in geometry.points"
              :key="point.bucketStart"
              class="stats-trend-point-anchor pointer-events-auto absolute flex h-6 w-6 -translate-x-1/2 -translate-y-1/2"
              :style="{ left: `${(point.x / 800) * 100}%`, top: `${(point.y / 240) * 100}%` }"
            >
              <CTooltip
                :content="`${formatPointTime(point)} · ${metricDisplayValue(metric, point.value, point.overflow)}`"
                clickable
              >
                <button
                  type="button"
                  class="stats-trend-point-trigger relative h-6 w-6 cursor-pointer rounded-full border-0 bg-transparent p-0 outline-none"
                  :aria-label="`${formatPointTime(point)}，${metricDisplayValue(metric, point.value, point.overflow)}`"
                />
              </CTooltip>
            </div>
          </div>
        </div>
        <div
          class="stats-trend-axis stats-trend-fixed-gutter mt-2 flex justify-between gap-3 text-xs text-muted"
        >
          <time>{{ formatSeriesPointTime(firstPoint) }}</time>
          <time>{{ formatSeriesPointTime(lastPoint) }}</time>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stats-trend-fixed-gutter {
  margin-inline: 12px;
}

.stats-trend-point-trigger::before,
.stats-trend-point-trigger::after {
  position: absolute;
  top: 50%;
  left: 50%;
  box-sizing: border-box;
  content: '';
  border-radius: 9999px;
  transform: translate(-50%, -50%);
}

.stats-trend-point-trigger::before {
  width: 8px;
  height: 8px;
  background: var(--surface);
  border: 2px solid var(--color-brand-500);
}

.stats-trend-point-trigger::after {
  width: 8px;
  height: 8px;
  pointer-events: none;
  border: 1px solid var(--color-brand-500);
  opacity: 0;
  transition: opacity var(--duration-fast) var(--ease-out-quad);
}

.stats-trend-point-trigger:focus::after {
  opacity: 1;
  box-shadow:
    0 0 0 3px var(--focus-ring),
    0 0 10px var(--focus-ring);
}

.stats-trend-point-trigger:focus-visible {
  outline: none;
  box-shadow: none;
}

@media (forced-colors: active) {
  .stats-trend-point-trigger:focus-visible {
    outline: 2px solid Highlight;
    outline-offset: 2px;
  }
}
</style>
