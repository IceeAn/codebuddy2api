<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { useRouter } from 'vue-router';
import { Activity, CheckCircle2, Clock3, KeyRound, Link } from '@lucide/vue';
import StatTile from '../components/StatTile.vue';
import RefreshButton from '../components/RefreshButton.vue';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CCard from '../components/ui/CCard.vue';
import CInput from '../components/ui/CInput.vue';
import CInputGroup from '../components/ui/CInputGroup.vue';
import CProgress from '../components/ui/CProgress.vue';
import CTooltip from '../components/ui/CTooltip.vue';
import { adminApi } from '../api/admin';
import { useClipboard } from '../composables/useClipboard';
import {
  buildPresetRange,
  formatCompactNumber,
  formatPercent,
  resolveBrowserTimeZone,
} from '../utils/stats';
import {
  computeValidityPercent,
  describeCredentialStatus,
  describeServiceStatus,
} from '../utils/dashboardStatus';
import { useSessionStore } from '../stores/session';
import { adminQueryKeys } from '../utils/adminQueryKeys';

const { copy } = useClipboard();
const router = useRouter();
const session = useSessionStore();
const queryKeys = adminQueryKeys(session.username);
const STATUS_REFETCH_INTERVAL_MS = 600_000;
const FOCUS_REFETCH_STALE_MS = 180_000;
const UPTIME_TICK_MS = 1_000;

const statusQuery = useQuery({
  queryKey: queryKeys.status,
  queryFn: adminApi.status,
  refetchInterval: STATUS_REFETCH_INTERVAL_MS,
  refetchOnMount: 'always',
  refetchOnWindowFocus: true,
  staleTime: FOCUS_REFETCH_STALE_MS,
});

function todayStatsParams() {
  const today = buildPresetRange('today');
  return {
    start_at: today.startAt,
    end_at: today.endAt,
    timezone: resolveBrowserTimeZone(),
    traffic: 'all' as const,
  };
}

const todayStatsQuery = useQuery({
  queryKey: queryKeys.statsOverview('dashboard-today'),
  queryFn: () => adminApi.statsOverview(todayStatsParams()),
  refetchOnMount: 'always',
  refetchOnWindowFocus: 'always',
});

const isError = computed(() => statusQuery.isError.value);
const statusData = computed(() => (isError.value ? undefined : statusQuery.data.value));
const statusLoading = computed(() => !isError.value && statusData.value === undefined);
const serviceTone = computed<'brand' | 'success' | 'warning' | 'error'>(() => {
  if (isError.value) return 'error';
  if (statusLoading.value) return 'brand';
  return statusData.value?.status === 'healthy' ? 'success' : 'warning';
});
const nowMs = ref(Date.now());
const uptimeSnapshotSeconds = ref<number | null>(null);
const uptimeSnapshotMs = ref(nowMs.value);
const uptimeTickId = window.setInterval(() => {
  nowMs.value = Date.now();
}, UPTIME_TICK_MS);

onUnmounted(() => {
  window.clearInterval(uptimeTickId);
});

function padDurationPart(value: number): string {
  return String(value).padStart(2, '0');
}

interface UptimeDisplay {
  value: string;
  label: string;
  meta: string;
}

function formatDurationTime(seconds: number): string {
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainingSeconds = seconds % 60;
  return `${padDurationPart(hours)}:${padDurationPart(minutes)}:${padDurationPart(remainingSeconds)}`;
}

function buildUptimeDisplay(seconds: number | null): UptimeDisplay {
  if (seconds === null) {
    return {
      value: '-',
      label: '服务时间',
      meta: '服务运行时长',
    };
  }

  const wholeSeconds = Math.floor(seconds);
  if (wholeSeconds < 86_400) {
    return {
      value: formatDurationTime(wholeSeconds),
      label: '服务时间',
      meta: '服务运行时长',
    };
  }

  const days = Math.floor(wholeSeconds / 86_400);
  return {
    value: `${days}天`,
    label: formatDurationTime(wholeSeconds % 86_400),
    meta: '服务运行时长',
  };
}

const statusRecovered = ref(false);
watch(
  () => statusQuery.isError.value,
  (isErr, wasErr) => {
    if (wasErr && !isErr) {
      statusRecovered.value = true;
      window.setTimeout(() => {
        statusRecovered.value = false;
      }, 600);
    }
  },
);

watch(
  () => statusData.value,
  (status) => {
    if (typeof status?.uptime_seconds !== 'number') {
      uptimeSnapshotSeconds.value = null;
      return;
    }

    const snapshotMs = Date.now();
    uptimeSnapshotSeconds.value = status.uptime_seconds;
    uptimeSnapshotMs.value = snapshotMs;
    nowMs.value = snapshotMs;
  },
  { immediate: true },
);

const runningUptimeSeconds = computed(() => {
  if (uptimeSnapshotSeconds.value === null) return null;

  const elapsedSeconds = Math.max(0, Math.floor((nowMs.value - uptimeSnapshotMs.value) / 1_000));
  return Math.max(0, uptimeSnapshotSeconds.value + elapsedSeconds);
});

const uptimeDisplay = computed(() => buildUptimeDisplay(runningUptimeSeconds.value));

const todayStatsTotals = computed(() =>
  todayStatsQuery.isError.value ? undefined : todayStatsQuery.data.value?.totals,
);
const todayRequestCount = computed(() => todayStatsTotals.value?.request_count ?? 0);
const todaySuccessRatePercentage = computed(() => {
  const rate = todayStatsTotals.value?.success_rate;
  return rate === null || rate === undefined ? null : Math.round(rate * 100);
});
const todaySuccessRateTooltip = computed(() => {
  const totals = todayStatsTotals.value;
  if (!totals || totals.success_rate === null) return '暂无成功率数据';
  const successCount = Math.round(totals.request_count * totals.success_rate);
  return `成功 ${formatCompactNumber(successCount)} / 总请求 ${formatCompactNumber(totals.request_count)}（${formatPercent(totals.success_rate)}）`;
});

const validityPercent = computed(() => {
  const valid = statusData.value?.credentials.valid ?? 0;
  const total = statusData.value?.credentials.total ?? 0;
  return computeValidityPercent(valid, total);
});

const combinedFetching = computed(
  () => statusQuery.isFetching.value || todayStatsQuery.isFetching.value,
);

async function refreshDashboard(): Promise<unknown> {
  const results = await Promise.all([statusQuery.refetch(), todayStatsQuery.refetch()]);
  return { isError: results.some((result) => (result as { isError?: boolean }).isError === true) };
}

const dashboardQuery = { isFetching: combinedFetching, refetch: refreshDashboard };

function copyApiBaseUrl() {
  const value = statusData.value?.api_base_url;
  if (!value) return;
  copy(value, '客户端入口地址已复制');
}

function openStats(): void {
  void router.push({ name: 'stats' });
}
</script>

<template>
  <div class="section-grid">
    <div class="toolbar">
      <span class="text-base font-semibold text-text-strong">总览</span>
      <RefreshButton :query="dashboardQuery" />
    </div>

    <CAlert v-if="isError" type="error">
      <div class="toolbar">
        <span>加载状态失败</span>
        <RefreshButton :query="dashboardQuery" label="重试" size="sm" />
      </div>
    </CAlert>

    <CAlert v-if="todayStatsQuery.isError.value" type="error">
      <div class="toolbar">
        <span>加载今日请求统计失败</span>
        <RefreshButton :query="todayStatsQuery" label="重试今日统计" size="sm" />
      </div>
    </CAlert>

    <div class="stats-grid grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile
        label="服务状态"
        :value="describeServiceStatus(statusData?.status, isError, statusLoading)"
        :tone="serviceTone"
        :icon="CheckCircle2"
        :meta="statusData?.service"
        :class="{ 'animate-success': statusRecovered }"
      />
      <StatTile
        label="有效凭证"
        :value="`${statusData?.credentials.valid ?? 0}/${statusData?.credentials.total ?? 0}`"
        :tone="isError ? 'error' : 'brand'"
        :icon="KeyRound"
        :meta="describeCredentialStatus(statusData?.credentials.current.status)"
      >
        <template #corner>
          <CProgress :percentage="validityPercent" :stroke-width="5" :size="52" />
        </template>
      </StatTile>
      <StatTile
        label="今日请求"
        :value="todayStatsQuery.isError.value ? '-' : todayRequestCount"
        tone="warning"
        :icon="Activity"
        meta="查看持久化统计"
        class="cursor-pointer"
        role="link"
        tabindex="0"
        @click="openStats"
        @keyup.enter="openStats"
      >
        <template #corner>
          <CTooltip :content="todaySuccessRateTooltip">
            <CProgress
              class="rounded-full"
              :percentage="todaySuccessRatePercentage ?? 0"
              :label="todaySuccessRatePercentage === null ? '-' : undefined"
              variant="success-rate"
              :stroke-width="5"
              :size="52"
              aria-label="成功率"
              :aria-valuetext="todaySuccessRatePercentage === null ? '暂无数据' : undefined"
              tabindex="0"
            />
          </CTooltip>
        </template>
      </StatTile>
      <StatTile
        :label="uptimeDisplay.label"
        :value="uptimeDisplay.value"
        :tone="isError ? 'error' : 'success'"
        :icon="Clock3"
        :meta="uptimeDisplay.meta"
        value-class="break-words text-[24px] leading-tight [overflow-wrap:anywhere]"
      />
    </div>

    <CCard title="客户端入口">
      <CInputGroup>
        <CInput :model-value="statusData?.api_base_url || ''" readonly />
        <CButton variant="secondary" @click="copyApiBaseUrl">
          <template #icon>
            <Link :size="16" />
          </template>
          复制
        </CButton>
      </CInputGroup>
    </CCard>
  </div>
</template>
