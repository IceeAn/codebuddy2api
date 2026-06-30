<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { Activity, CheckCircle2, Clock3, KeyRound, Link } from '@lucide/vue';
import StatTile from '../components/StatTile.vue';
import RefreshButton from '../components/RefreshButton.vue';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CCard from '../components/ui/CCard.vue';
import CDataTable, { type Column } from '../components/ui/CDataTable.vue';
import CInput from '../components/ui/CInput.vue';
import CInputGroup from '../components/ui/CInputGroup.vue';
import CProgress from '../components/ui/CProgress.vue';
import { adminApi } from '../api/admin';
import { useClipboard } from '../composables/useClipboard';
import {
  computeValidityPercent,
  describeCredentialStatus,
  describeServiceStatus,
} from '../utils/dashboardStatus';

const { copy } = useClipboard();
const STATUS_REFETCH_INTERVAL_MS = 600_000;
const FOCUS_REFETCH_STALE_MS = 180_000;
const UPTIME_TICK_MS = 1_000;

const statusQuery = useQuery({
  queryKey: ['admin-status'],
  queryFn: adminApi.status,
  refetchInterval: STATUS_REFETCH_INTERVAL_MS,
  refetchOnMount: 'always',
  refetchOnWindowFocus: true,
  staleTime: FOCUS_REFETCH_STALE_MS,
});

const isError = computed(() => statusQuery.isError.value);
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
  () => statusQuery.data.value,
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

  const elapsedSeconds = Math.floor((nowMs.value - uptimeSnapshotMs.value) / 1_000);
  return uptimeSnapshotSeconds.value + elapsedSeconds;
});

const uptimeDisplay = computed(() => buildUptimeDisplay(runningUptimeSeconds.value));

const totalCalls = computed(() => {
  const usage = statusQuery.data.value?.usage.model_usage || {};
  return Object.values(usage).reduce((sum, count) => sum + count, 0);
});

const modelRows = computed(
  () =>
    Object.entries(statusQuery.data.value?.usage.model_usage || {})
      .sort((a, b) => b[1] - a[1])
      .map(([model, count]) => ({ model, count })) as Record<string, unknown>[],
);

const credentialRows = computed(
  () =>
    Object.entries(statusQuery.data.value?.usage.credential_usage || {})
      .sort((a, b) => b[1] - a[1])
      .map(([credential, count]) => ({
        credential: credential.split('/').pop() || credential,
        count,
      })) as Record<string, unknown>[],
);

const validityPercent = computed(() => {
  const valid = statusQuery.data.value?.credentials.valid ?? 0;
  const total = statusQuery.data.value?.credentials.total ?? 0;
  return computeValidityPercent(valid, total);
});

const usageColumns: Column[] = [
  { title: '模型', key: 'model', ellipsis: { tooltip: true } },
  { title: '调用', key: 'count', align: 'right', width: 120 },
];

const credentialColumns: Column[] = [
  { title: '凭证', key: 'credential', ellipsis: { tooltip: true } },
  { title: '调用', key: 'count', align: 'right', width: 120 },
];

function copyApiBaseUrl() {
  const value = statusQuery.data.value?.api_base_url;
  if (!value) return;
  copy(value, '客户端入口地址已复制');
}
</script>

<template>
  <div class="section-grid">
    <div class="toolbar">
      <span class="text-base font-semibold text-text-strong">总览</span>
      <RefreshButton :query="statusQuery" />
    </div>

    <CAlert v-if="isError" type="error">
      <div class="toolbar">
        <span>加载状态失败</span>
        <RefreshButton :query="statusQuery" label="重试" size="sm" />
      </div>
    </CAlert>

    <div class="stats-grid grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile
        label="服务状态"
        :value="describeServiceStatus(statusQuery.data.value?.status, isError)"
        tone="success"
        :icon="CheckCircle2"
        :meta="statusQuery.data.value?.service"
        :class="{ 'animate-success': statusRecovered }"
      />
      <StatTile
        label="有效凭证"
        :value="`${statusQuery.data.value?.credentials.valid ?? 0}/${statusQuery.data.value?.credentials.total ?? 0}`"
        tone="brand"
        :icon="KeyRound"
        :meta="describeCredentialStatus(statusQuery.data.value?.credentials.current.status)"
      >
        <template #corner>
          <CProgress :percentage="validityPercent" :stroke-width="5" :size="52" />
        </template>
      </StatTile>
      <StatTile
        label="API 调用"
        :value="totalCalls"
        tone="warning"
        :icon="Activity"
        meta="按当前进程内统计"
      />
      <StatTile
        :label="uptimeDisplay.label"
        :value="uptimeDisplay.value"
        tone="success"
        :icon="Clock3"
        :meta="uptimeDisplay.meta"
        value-class="break-words text-[24px] leading-tight [overflow-wrap:anywhere]"
      />
    </div>

    <div
      class="grid split-grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]"
    >
      <CCard title="模型使用">
        <CDataTable
          :columns="usageColumns"
          :data="modelRows"
          :loading="statusQuery.isLoading.value || statusQuery.isFetching.value"
          :error="statusQuery.isError.value"
          :bordered="false"
          size="small"
        >
          <template #empty>暂无使用数据</template>
        </CDataTable>
      </CCard>

      <CCard title="凭证使用">
        <CDataTable
          :columns="credentialColumns"
          :data="credentialRows"
          :loading="statusQuery.isLoading.value || statusQuery.isFetching.value"
          :error="statusQuery.isError.value"
          :bordered="false"
          size="small"
        >
          <template #empty>暂无使用数据</template>
        </CDataTable>
      </CCard>
    </div>

    <CCard title="客户端入口">
      <CInputGroup>
        <CInput :model-value="statusQuery.data.value?.api_base_url || ''" readonly />
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
