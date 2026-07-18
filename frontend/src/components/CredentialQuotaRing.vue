<script lang="ts">
import type { CredentialQuota } from '../types';

export type CredentialQuotaTone = 'danger' | 'warning' | 'success' | 'muted';

export function quotaTone(quota: CredentialQuota): CredentialQuotaTone {
  const percent = quota.remaining_percent;
  if (
    quota.quota_available === false ||
    percent === null ||
    quota.status === 'unknown' ||
    quota.status === 'error'
  )
    return 'muted';
  if (percent <= 10) return 'danger';
  if (percent <= 30) return 'warning';
  return 'success';
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function unavailableLabel(quota: CredentialQuota): string {
  return `未探测到${quota.quota_type === 'enterprise' ? '企业版' : '个人版'}额度`;
}

function pendingLabel(quota: CredentialQuota): string {
  return `尚未探测${quota.quota_type === 'enterprise' ? '企业版' : '个人版'}额度`;
}

function formatTime(value: number | null): string {
  if (value === null) return '--';
  return new Date(value * 1000).toLocaleString();
}

function packagePeriod(cycleStart: string | null, cycleEnd: string | null): string {
  if (cycleStart && cycleEnd) return `${cycleStart} 至 ${cycleEnd}`;
  if (cycleStart) return `周期始于 ${cycleStart}`;
  if (cycleEnd) return `周期截至 ${cycleEnd}`;
  return '';
}

export function formatQuotaTooltipSections(quota: CredentialQuota): string[] {
  if (quota.status === 'unknown') return [pendingLabel(quota)];
  if (quota.status === 'error' || quota.remaining === null || quota.total === null) {
    return [`额度查询失败\n最近尝试：${formatTime(quota.last_attempt_at)}`];
  }
  if (quota.quota_available === false) {
    const lines = [unavailableLabel(quota)];
    if (quota.status === 'stale') {
      lines.push(`最近校准：${formatTime(quota.last_success_at)}`);
      lines.push('最近刷新失败，当前值已陈旧');
      lines.push(`最近尝试：${formatTime(quota.last_attempt_at)}`);
    }
    return [lines.join('\n')];
  }
  const lines = [
    `${quota.estimated ? '当前估算' : '当前额度'}：${formatNumber(quota.remaining)} / ${formatNumber(quota.total)}`,
    `最近校准：${formatTime(quota.last_success_at)}`,
  ];
  if (quota.estimated) {
    lines.push(`校准后已观测消耗：${formatNumber(quota.estimated_credit_since_sync)}`);
    lines.push(`最近估算：${formatTime(quota.last_estimated_at)}`);
  }
  if (quota.status === 'stale') lines.push('最近刷新失败，当前值已陈旧');
  const sections = [lines.join('\n')];
  for (const item of quota.packages) {
    const packageLines = [
      `${item.name}：${formatNumber(item.remaining)} / ${formatNumber(item.total)}`,
    ];
    const period = packagePeriod(item.cycle_start, item.cycle_end);
    if (period) packageLines.push(period);
    sections.push(packageLines.join('\n'));
  }
  return sections;
}

export function formatQuotaTooltip(quota: CredentialQuota): string {
  return formatQuotaTooltipSections(quota).join('\n\n');
}
</script>

<script setup lang="ts">
import { computed } from 'vue';
import CTooltip from './ui/CTooltip.vue';

const props = defineProps<{ quota: CredentialQuota }>();

const tone = computed(() => quotaTone(props.quota));
const percent = computed(() => props.quota.remaining_percent ?? 0);
const dasharray = computed(() => (percent.value >= 100 ? undefined : `${percent.value} 100`));
const label = computed(() => {
  if (props.quota.status === 'unknown') return pendingLabel(props.quota);
  if (
    props.quota.status === 'error' ||
    props.quota.remaining === null ||
    props.quota.total === null
  ) {
    return '额度查询失败';
  }
  if (props.quota.quota_available === false) {
    return `${unavailableLabel(props.quota)}${props.quota.status === 'stale' ? '，数据已陈旧' : ''}`;
  }
  const prefix = props.quota.estimated ? '估算剩余额度' : '剩余额度';
  return `${prefix} ${formatNumber(props.quota.remaining)} / ${formatNumber(props.quota.total)}，${percent.value}%${props.quota.status === 'stale' ? '，数据已陈旧' : ''}`;
});
const tooltipSections = computed(() => formatQuotaTooltipSections(props.quota));
const tooltip = computed(() => tooltipSections.value.join('\n\n'));
const color = computed(() => {
  const colors: Record<CredentialQuotaTone, string> = {
    danger: 'var(--tone-error)',
    warning: 'var(--tone-warning)',
    success: 'var(--tone-success)',
    muted: 'var(--muted)',
  };
  return colors[tone.value];
});
const ringStyle = computed(() => ({ color: color.value }));
</script>

<template>
  <CTooltip class="align-middle leading-none" :content="tooltip" placement="top">
    <template #content>
      <span class="credential-quota-tooltip-content block space-y-1">
        <span
          v-for="(section, index) in tooltipSections"
          :key="`${index}:${section}`"
          class="credential-quota-tooltip-section block whitespace-pre-line"
        >
          {{ section }}
        </span>
      </span>
    </template>
    <svg
      class="credential-quota-ring relative inline-grid size-4 place-items-center rounded-full"
      :class="[
        `credential-quota-ring-${tone}`,
        { 'credential-quota-ring-stale': quota.status === 'stale' },
      ]"
      :style="ringStyle"
      viewBox="0 0 16 16"
      role="img"
      :aria-label="label"
    >
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="var(--border)" stroke-width="3" />
      <circle
        class="credential-quota-ring-value"
        cx="8"
        cy="8"
        r="6.25"
        fill="none"
        stroke="currentColor"
        stroke-width="3"
        pathLength="100"
        :stroke-dasharray="dasharray"
        transform="rotate(-90 8 8)"
      />
    </svg>
  </CTooltip>
</template>

<style scoped>
.credential-quota-ring-stale {
  opacity: 0.62;
  outline: 1px dashed var(--tone-warning);
  outline-offset: 2px;
}
</style>
