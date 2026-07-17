<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import {
  Activity,
  BotMessageSquare,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleDollarSign,
  Clock3,
  MessageSquareText,
  TimerReset,
} from '@lucide/vue';
import { adminApi } from '../api/admin';
import type {
  StatsDimension,
  StatsDimensionItem,
  StatsMetric,
  StatsOverviewQuery,
  StatsRangePreset,
  StatsRequestRecord,
  StatsRequestsResponse,
  StatsTraffic,
} from '../types';
import StatTile from '../components/StatTile.vue';
import StatsTrendChart from '../components/StatsTrendChart.vue';
import RefreshButton from '../components/RefreshButton.vue';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CCard from '../components/ui/CCard.vue';
import CDrawer from '../components/ui/CDrawer.vue';
import CInputNumber from '../components/ui/CInputNumber.vue';
import CProgress from '../components/ui/CProgress.vue';
import CRadioButton from '../components/ui/CRadioButton.vue';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import CSelect from '../components/ui/CSelect.vue';
import CSpin from '../components/ui/CSpin.vue';
import CTag from '../components/ui/CTag.vue';
import CTooltip from '../components/ui/CTooltip.vue';
import {
  buildPresetRange,
  buildPaginationItems,
  cacheHitPercentage,
  formatCompactNumber,
  formatCredit,
  formatDurationMs,
  formatLatencyPercentile,
  formatPercent,
  formatTimestamp,
  formatTokenNumber,
  formatTokenCoverage,
  fromLocalInputValue,
  metricLabel,
  resolveBrowserTimeZone,
  sourceLabel,
  toLocalInputValue,
} from '../utils/stats';
import { useSessionStore } from '../stores/session';
import { adminQueryKeys } from '../utils/adminQueryKeys';

const REQUEST_PAGE_SIZES = [10, 20, 50, 100] as const;
const DIMENSION_PAGE_SIZE = 50;
const timezone = resolveBrowserTimeZone();
const initialRange = buildPresetRange('7d');
const session = useSessionStore();
const queryKeys = adminQueryKeys(session.username);

const rangePreset = ref<StatsRangePreset>('7d');
const range = reactive({ startAt: initialRange.startAt, endAt: initialRange.endAt });
const customStart = ref(toLocalInputValue(initialRange.startAt));
const customEnd = ref(toLocalInputValue(initialRange.endAt));
const customRangeError = ref('');
const filters = reactive({
  traffic: 'all' as StatsTraffic,
  model: '',
  apiKeyId: '',
  credentialId: '',
  outcome: '',
});
const trafficOptions: Array<{ value: StatsTraffic; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'external', label: '外部 API' },
  { value: 'admin', label: '管理台请求' },
];
const queryParams = computed<StatsOverviewQuery>(() => ({
  start_at: range.startAt,
  end_at: range.endAt,
  timezone,
  traffic: filters.traffic,
  ...(filters.model ? { model: filters.model } : {}),
  ...(filters.apiKeyId ? { api_key_id: filters.apiKeyId } : {}),
  ...(filters.credentialId ? { credential_id: filters.credentialId } : {}),
  ...(filters.outcome ? { outcome: filters.outcome } : {}),
}));

function queryParamsForFetch(): StatsOverviewQuery {
  if (rangePreset.value === 'custom') return { ...queryParams.value };
  const currentRange = buildPresetRange(rangePreset.value);
  return {
    ...queryParams.value,
    start_at: currentRange.startAt,
    end_at: currentRange.endAt,
  };
}

interface StatsRequestsFirstPage extends StatsRequestsResponse {
  pagination_snapshot: StatsOverviewQuery;
}

interface RequestPaginationSnapshot {
  id: number;
  time: number;
}

const requestPageSizeOptions = REQUEST_PAGE_SIZES.map((size) => ({
  value: size,
  label: `${size} 条/页`,
}));
const requestPageSize = ref<number>(20);
const requestPageData = ref<StatsRequestsResponse | null>(null);
const requestSnapshot = ref<RequestPaginationSnapshot | null>(null);
const requestParamsSnapshot = ref<StatsOverviewQuery | null>(null);
const requestJumpPage = ref<number | null>(1);
const requestMobileJumpInput = ref('1');
const requestPageLoading = ref(false);
const requestPageError = ref('');
let requestPageGeneration = 0;

function resetPagination(): void {
  requestPageGeneration += 1;
  requestPageData.value = null;
  requestSnapshot.value = null;
  requestParamsSnapshot.value = null;
  requestJumpPage.value = 1;
  requestPageLoading.value = false;
  requestPageError.value = '';
}

async function fetchRequestsFirstPage(): Promise<StatsRequestsFirstPage> {
  const paginationSnapshot = queryParamsForFetch();
  const page = await adminApi.statsRequests({
    ...paginationSnapshot,
    page: 1,
    page_size: requestPageSize.value,
  });
  return { ...page, pagination_snapshot: paginationSnapshot };
}

const overviewQuery = useQuery({
  queryKey: computed(() => queryKeys.statsOverview(queryParams.value)),
  queryFn: () => adminApi.statsOverview(queryParamsForFetch()),
  placeholderData: (previousData) => previousData,
  refetchOnMount: 'always',
  refetchOnWindowFocus: 'always',
});

const requestsQuery = useQuery({
  queryKey: computed(() => [...queryKeys.statsRequests(queryParams.value), requestPageSize.value]),
  queryFn: fetchRequestsFirstPage,
  placeholderData: (previousData) => previousData,
  refetchOnMount: 'always',
  refetchOnWindowFocus: 'always',
});

watch(
  () => requestsQuery.data.value,
  (data) => {
    requestPageData.value = null;
    requestSnapshot.value = data ? { id: data.snapshot_id, time: data.snapshot_time } : null;
    requestParamsSnapshot.value = data
      ? { ...(data.pagination_snapshot ?? queryParams.value) }
      : null;
    requestJumpPage.value = 1;
    requestPageError.value = '';
  },
  { immediate: true },
);

watch(
  () => requestsQuery.isFetching.value,
  (isFetching) => {
    if (isFetching) resetPagination();
  },
  { flush: 'sync' },
);

watch(queryParams, resetPagination, { flush: 'sync' });
watch(requestPageSize, resetPagination, { flush: 'sync' });

const activeRequestPage = computed(() => requestPageData.value ?? requestsQuery.data.value);
const requestItems = computed(() => activeRequestPage.value?.items ?? []);
const requestRows = computed(() => {
  const page = activeRequestPage.value;
  if (!page) return [];
  const snapshot: RequestPaginationSnapshot = {
    id: page.snapshot_id,
    time: page.snapshot_time,
  };
  return page.items.map((request) => ({ request, snapshot }));
});
const currentRequestPage = computed(() => activeRequestPage.value?.page ?? 1);
const requestTotal = computed(() => activeRequestPage.value?.total ?? 0);
const requestTotalPages = computed(() => activeRequestPage.value?.total_pages ?? 0);
const requestPaginationItems = computed(() =>
  buildPaginationItems(currentRequestPage.value, requestTotalPages.value),
);

function syncRequestMobileJumpInput(): void {
  requestMobileJumpInput.value = String(
    requestTotalPages.value === 0 ? 0 : currentRequestPage.value,
  );
}

watch([currentRequestPage, requestTotalPages], syncRequestMobileJumpInput, { immediate: true });

const selectedMetric = ref<StatsMetric>('request_count');
const metricOptions: StatsMetric[] = [
  'request_count',
  'total_tokens',
  'total_credit',
  'success_rate',
  'p95_first_output_ms',
  'p95_total_ms',
];
const metricSelectOptions = metricOptions.map((metric) => ({
  value: metric,
  label: metricLabel(metric),
}));

function selectMetric(metric: string | number): void {
  selectedMetric.value = metric as StatsMetric;
}

const rangeOptions: Array<{ value: StatsRangePreset; label: string }> = [
  { value: 'today', label: '今日' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: '90d', label: '90 天' },
  { value: 'all', label: '全部' },
  { value: 'custom', label: '自定义' },
];

const totals = computed(() => overviewQuery.data.value?.totals);
const successRatePercentage = computed(() => {
  const rate = totals.value?.success_rate;
  return rate === null || rate === undefined ? null : Math.round(rate * 100);
});
const successRateTooltip = computed(() => {
  const currentTotals = totals.value;
  if (!currentTotals || currentTotals.success_rate === null) return '暂无成功率数据';
  const rate = currentTotals.success_rate;
  const requestCount = currentTotals.request_count;
  const successCount = Math.round(requestCount * rate);
  return `成功 ${formatCompactNumber(successCount)} / 总请求 ${formatCompactNumber(requestCount)}（${formatPercent(rate)}）`;
});
const inputCacheHitPercentage = computed(() =>
  cacheHitPercentage(
    totals.value?.cache_hit_tokens ?? null,
    totals.value?.cache_miss_tokens ?? null,
  ),
);
const inputCacheMeta = computed(() => {
  const hitTokens = totals.value?.cache_hit_tokens ?? null;
  const missTokens = totals.value?.cache_miss_tokens ?? null;
  if (hitTokens === null || missTokens === null) return '暂无缓存命中数据';
  return `命中 ${formatTokenNumber(hitTokens)} / 未命中 ${formatTokenNumber(missTokens)}`;
});
const inputCacheTooltip = computed(() => {
  const hitTokens = totals.value?.cache_hit_tokens ?? null;
  const missTokens = totals.value?.cache_miss_tokens ?? null;
  if (inputCacheHitPercentage.value === null || hitTokens === null || missTokens === null) {
    return '暂无缓存命中数据';
  }
  return `命中 ${formatTokenNumber(hitTokens)} / 未命中 ${formatTokenNumber(missTokens)}（${formatPercent(hitTokens / (hitTokens + missTokens))}）`;
});
const dimensions = computed(() => overviewQuery.data.value?.dimensions);
const breakdowns = computed(() => overviewQuery.data.value?.breakdowns);
interface FilterOption {
  value: string;
  label: string;
}

function filterOptions(
  allLabel: string,
  options: FilterOption[],
  selectedValue: string,
): FilterOption[] {
  const result = [{ value: '', label: allLabel }, ...options];
  if (selectedValue && !options.some((option) => option.value === selectedValue)) {
    result.push({ value: selectedValue, label: selectedValue });
  }
  return result;
}

const modelOptions = computed(() =>
  filterOptions(
    '全部模型',
    (dimensions.value?.models ?? []).map((model) => ({ value: model, label: model })),
    filters.model,
  ),
);
const apiKeyOptions = computed(() =>
  filterOptions(
    '全部 API Key',
    (dimensions.value?.api_keys ?? []).map((apiKey) => ({
      value: apiKey.id,
      label: apiKey.name,
    })),
    filters.apiKeyId,
  ),
);
const credentialOptions = computed(() =>
  filterOptions(
    '全部凭证',
    (dimensions.value?.credentials ?? []).map((credential) => ({
      value: credential.id,
      label: credential.label,
    })),
    filters.credentialId,
  ),
);

function staticOutcomeLabel(value: string): string {
  const labels: Record<string, string> = {
    success: '成功',
    failure: '失败',
    cancelled: '客户端中断',
  };
  return labels[value] ?? value;
}

const outcomeOptions = computed(() =>
  filterOptions(
    '全部结果',
    (dimensions.value?.outcomes ?? []).map((outcome) => ({
      value: outcome,
      label: staticOutcomeLabel(outcome),
    })),
    filters.outcome,
  ),
);
const combinedFetching = computed(
  () =>
    overviewQuery.isFetching.value || requestsQuery.isFetching.value || requestPageLoading.value,
);

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

const queryErrorMessage = computed(() =>
  errorMessage(overviewQuery.error.value ?? requestsQuery.error.value, '未知错误'),
);

async function refreshAll(): Promise<unknown> {
  resetPagination();
  const result = await Promise.all([overviewQuery.refetch(), requestsQuery.refetch()]);
  return { isError: result.some((item) => (item as { isError?: boolean }).isError === true) };
}

const combinedQuery = {
  isFetching: combinedFetching,
  refetch: refreshAll,
};

function selectRange(preset: StatsRangePreset): void {
  rangePreset.value = preset;
  customRangeError.value = '';
  if (preset === 'custom') {
    customStart.value = toLocalInputValue(range.startAt);
    customEnd.value = toLocalInputValue(range.endAt);
    return;
  }
  const next = buildPresetRange(preset);
  range.startAt = next.startAt;
  range.endAt = next.endAt;
}

function applyCustomRange(): void {
  const startAt = fromLocalInputValue(customStart.value);
  const endAt = fromLocalInputValue(customEnd.value);
  if (startAt === null || endAt === null) {
    customRangeError.value = '请输入有效的开始和结束时间';
    return;
  }
  if (endAt <= startAt) {
    customRangeError.value = '结束时间必须晚于开始时间';
    return;
  }
  customRangeError.value = '';
  range.startAt = startAt;
  range.endAt = endAt;
}

function setTraffic(traffic: StatsTraffic): void {
  filters.traffic = traffic;
}

async function goToRequestPage(targetPage: number): Promise<void> {
  const snapshot = requestSnapshot.value;
  const paramsSnapshot = requestParamsSnapshot.value;
  const totalPages = requestTotalPages.value;
  if (
    requestPageLoading.value ||
    requestsQuery.isFetching.value ||
    !snapshot ||
    !paramsSnapshot ||
    !Number.isInteger(targetPage) ||
    targetPage < 1 ||
    targetPage > totalPages ||
    targetPage === currentRequestPage.value
  ) {
    return;
  }
  requestPageError.value = '';
  requestJumpPage.value = targetPage;
  if (targetPage === 1) {
    requestPageGeneration += 1;
    requestPageData.value = null;
    return;
  }
  const generation = ++requestPageGeneration;
  requestPageLoading.value = true;
  try {
    const page = await adminApi.statsRequests({
      ...paramsSnapshot,
      page: targetPage,
      page_size: requestPageSize.value,
      snapshot_id: snapshot.id,
      snapshot_time: snapshot.time,
    });
    if (generation !== requestPageGeneration) return;
    if (page.snapshot_id !== snapshot.id || page.snapshot_time !== snapshot.time) {
      throw new Error('请求分页快照不匹配');
    }
    requestPageData.value = page;
  } catch (error) {
    if (generation === requestPageGeneration) {
      requestJumpPage.value = currentRequestPage.value;
      requestPageError.value = errorMessage(error, '加载请求分页失败');
    }
  } finally {
    if (generation === requestPageGeneration) requestPageLoading.value = false;
  }
}

async function jumpToRequestPage(): Promise<void> {
  if (requestJumpPage.value === null) return;
  await goToRequestPage(requestJumpPage.value);
}

function normalizeRequestMobileJumpInput(rawValue: string): number {
  const totalPages = requestTotalPages.value;
  if (totalPages === 0) return 0;
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed)) return currentRequestPage.value;
  return Math.min(totalPages, Math.max(1, Math.trunc(parsed)));
}

function updateRequestMobileJumpInput(event: Event): void {
  requestMobileJumpInput.value = (event.target as HTMLInputElement).value;
}

async function applyRequestMobileJumpInput(): Promise<void> {
  const targetPage = normalizeRequestMobileJumpInput(requestMobileJumpInput.value);
  requestMobileJumpInput.value = String(targetPage);
  requestJumpPage.value = targetPage;
  if (targetPage === 0) return;
  await goToRequestPage(targetPage);
  syncRequestMobileJumpInput();
}

function confirmRequestMobileJumpInput(event: KeyboardEvent): void {
  event.preventDefault();
  (event.target as HTMLInputElement).blur();
}

function changeRequestPageSize(value: string | number): void {
  const size = Number(value);
  if (!REQUEST_PAGE_SIZES.includes(size as (typeof REQUEST_PAGE_SIZES)[number])) return;
  requestPageSize.value = size;
}

const detailOpen = ref(false);
const detailLoading = ref(false);
const detailError = ref('');
const detail = ref<StatsRequestRecord | null>(null);
let detailRequestGeneration = 0;

async function openDetail(
  request: StatsRequestRecord,
  snapshot: RequestPaginationSnapshot,
): Promise<void> {
  const generation = ++detailRequestGeneration;
  detailOpen.value = true;
  detailLoading.value = true;
  detailError.value = '';
  detail.value = null;
  try {
    const result = await adminApi.statsRequestDetail(request.id, snapshot);
    if (generation === detailRequestGeneration) detail.value = result;
  } catch (error) {
    if (generation === detailRequestGeneration) {
      detailError.value = errorMessage(error, '加载请求详情失败');
    }
  } finally {
    if (generation === detailRequestGeneration) detailLoading.value = false;
  }
}

function closeDetail(): void {
  detailRequestGeneration += 1;
  detailOpen.value = false;
  detailLoading.value = false;
}

function setDetailOpen(open: boolean): void {
  if (open) detailOpen.value = true;
  else closeDetail();
}

function outcomeLabel(value: string): string {
  const option = outcomeOptions.value.find((item) => item.value === value);
  return option?.label ?? staticOutcomeLabel(value);
}

function outcomeTag(value: string): 'success' | 'error' | 'warning' | 'default' {
  if (value === 'success') return 'success';
  if (value === 'cancelled') return 'warning';
  return value === 'failure' ? 'error' : 'default';
}

function displayNullable(value: string | number | null): string | number {
  return value ?? '-';
}

function displayBoolean(value: boolean | null): string {
  return value === null ? '-' : value ? '是' : '否';
}

function displayBytes(value: number | null): string {
  return value === null ? '-' : `${value} B`;
}

function requestCacheHitLabel(request: StatsRequestRecord): string {
  const percentage = cacheHitPercentage(request.cache_hit_tokens, request.cache_miss_tokens);
  return percentage === null ? '缓存命中率未知' : `${percentage}%缓存命中`;
}

interface RankingRow {
  id: string;
  label: string;
  request_count: number;
  success_rate: number | null;
  total_tokens: number | null;
  total_credit: number | null;
  p95_total_ms: number | null;
  p95_total_ms_overflow: boolean;
}

type DimensionExplorerMode = 'ranking' | 'select';

const dimensionOpen = ref(false);
const dimensionKind = ref<StatsDimension>('models');
const dimensionMode = ref<DimensionExplorerMode>('ranking');
const dimensionItems = ref<StatsDimensionItem[]>([]);
const dimensionLoading = ref(false);
const dimensionError = ref('');
const dimensionSearch = ref('');
const dimensionAppliedSearch = ref('');
const dimensionParamsSnapshot = ref<StatsOverviewQuery | null>(null);
const dimensionCursor = ref<string | null>(null);
const dimensionNextCursor = ref<string | null>(null);
const dimensionCursorHistory = ref<Array<string | null>>([]);
let dimensionRequestGeneration = 0;

const dimensionTitle = computed(() => {
  const labels: Record<StatsDimension, string> = {
    models: '全部模型',
    api_keys: '全部 API Key',
    credentials: '全部凭证',
  };
  return labels[dimensionKind.value];
});

function dimensionQuery(kind: StatsDimension): StatsOverviewQuery {
  const query = { ...queryParamsForFetch() };
  if (kind === 'models') delete query.model;
  if (kind === 'api_keys') delete query.api_key_id;
  if (kind === 'credentials') delete query.credential_id;
  return query;
}

async function loadDimensionPage(
  cursor: string | null,
  paramsSnapshot: StatsOverviewQuery,
  appliedSearch: string,
): Promise<boolean> {
  const generation = ++dimensionRequestGeneration;
  const kind = dimensionKind.value;
  dimensionLoading.value = true;
  dimensionError.value = '';
  try {
    const page = await adminApi.statsDimensions(kind, {
      ...paramsSnapshot,
      ...(appliedSearch ? { search: appliedSearch } : {}),
      ...(cursor ? { cursor } : {}),
      limit: DIMENSION_PAGE_SIZE,
    });
    if (generation !== dimensionRequestGeneration) return false;
    dimensionItems.value = page.items;
    dimensionCursor.value = cursor;
    dimensionNextCursor.value = page.next_cursor;
    return true;
  } catch (error) {
    if (generation === dimensionRequestGeneration) {
      dimensionError.value = errorMessage(error, '加载完整维度失败');
    }
    return false;
  } finally {
    if (generation === dimensionRequestGeneration) dimensionLoading.value = false;
  }
}

async function openDimensionExplorer(
  kind: StatsDimension,
  mode: DimensionExplorerMode = 'ranking',
): Promise<void> {
  dimensionRequestGeneration += 1;
  dimensionKind.value = kind;
  dimensionMode.value = mode;
  dimensionItems.value = [];
  dimensionSearch.value = '';
  dimensionAppliedSearch.value = '';
  dimensionParamsSnapshot.value = dimensionQuery(kind);
  dimensionCursor.value = null;
  dimensionNextCursor.value = null;
  dimensionCursorHistory.value = [];
  dimensionOpen.value = true;
  await loadDimensionPage(null, dimensionParamsSnapshot.value, '');
}

function closeDimensionExplorer(): void {
  dimensionRequestGeneration += 1;
  dimensionOpen.value = false;
  dimensionLoading.value = false;
  dimensionParamsSnapshot.value = null;
}

function setDimensionOpen(open: boolean): void {
  if (open) dimensionOpen.value = true;
  else closeDimensionExplorer();
}

async function searchDimensions(): Promise<void> {
  const paramsSnapshot = dimensionQuery(dimensionKind.value);
  const appliedSearch = dimensionSearch.value.trim();
  if (await loadDimensionPage(null, paramsSnapshot, appliedSearch)) {
    dimensionParamsSnapshot.value = paramsSnapshot;
    dimensionAppliedSearch.value = appliedSearch;
    dimensionCursorHistory.value = [];
  }
}

async function nextDimensionPage(): Promise<void> {
  const paramsSnapshot = dimensionParamsSnapshot.value;
  if (!dimensionNextCursor.value || dimensionLoading.value || !paramsSnapshot) return;
  const currentCursor = dimensionCursor.value;
  const nextCursor = dimensionNextCursor.value;
  if (await loadDimensionPage(nextCursor, paramsSnapshot, dimensionAppliedSearch.value)) {
    dimensionCursorHistory.value.push(currentCursor);
  }
}

async function previousDimensionPage(): Promise<void> {
  const paramsSnapshot = dimensionParamsSnapshot.value;
  if (dimensionCursorHistory.value.length === 0 || dimensionLoading.value || !paramsSnapshot)
    return;
  const cursor = dimensionCursorHistory.value.at(-1) ?? null;
  if (await loadDimensionPage(cursor, paramsSnapshot, dimensionAppliedSearch.value)) {
    dimensionCursorHistory.value.pop();
  }
}

function selectDimensionItem(item: StatsDimensionItem): void {
  if (dimensionKind.value === 'models') filters.model = item.id;
  if (dimensionKind.value === 'api_keys') filters.apiKeyId = item.id;
  if (dimensionKind.value === 'credentials') filters.credentialId = item.id;
  closeDimensionExplorer();
}

function breakdownRows(kind: 'models' | 'api_keys' | 'credentials'): RankingRow[] {
  const data = breakdowns.value;
  if (!data) return [];
  if (kind === 'models') {
    return data.models.map((row) => ({ ...row, id: row.model, label: row.model }));
  }
  if (kind === 'api_keys') {
    return data.api_keys.map((row) => ({ ...row, label: row.name }));
  }
  return data.credentials;
}
</script>

<template>
  <div class="stats-view section-grid">
    <div class="toolbar flex-wrap gap-3">
      <div>
        <div class="text-base font-semibold text-text-strong">持久化用量统计</div>
      </div>
      <RefreshButton :query="combinedQuery" />
    </div>

    <CCard>
      <div class="flex flex-col gap-4">
        <div class="flex flex-wrap items-center gap-2">
          <span class="mr-1 text-xs font-semibold text-muted">时间范围</span>
          <CButton
            v-for="option in rangeOptions"
            :key="option.value"
            size="sm"
            :variant="rangePreset === option.value ? 'primary' : 'secondary'"
            @click="selectRange(option.value)"
          >
            {{ option.label }}
          </CButton>
        </div>

        <div v-if="rangePreset === 'custom'" class="flex flex-wrap items-end gap-3">
          <label class="grid gap-1 text-xs text-muted">
            开始时间
            <input
              v-model="customStart"
              type="datetime-local"
              class="c-control-focus h-[38px] rounded-md border border-border bg-surface px-3 text-sm text-text"
            />
          </label>
          <label class="grid gap-1 text-xs text-muted">
            结束时间
            <input
              v-model="customEnd"
              type="datetime-local"
              class="c-control-focus h-[38px] rounded-md border border-border bg-surface px-3 text-sm text-text"
            />
          </label>
          <CButton variant="primary" @click="applyCustomRange">应用范围</CButton>
          <span v-if="customRangeError" class="text-sm text-error-600">{{ customRangeError }}</span>
        </div>

        <div class="flex flex-wrap items-center gap-3">
          <span class="text-xs font-semibold text-muted">流量</span>
          <CRadioGroup v-model="filters.traffic" aria-label="流量">
            <CRadioButton
              v-for="option in trafficOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
              :data-traffic="option.value"
            />
          </CRadioGroup>
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <CSelect
            v-model="filters.model"
            :options="modelOptions"
            placeholder="全部模型"
            filterable
            footer-action-label="从详细列表选择…"
            @footer-action="openDimensionExplorer('models', 'select')"
          />
          <CSelect
            v-model="filters.apiKeyId"
            :options="apiKeyOptions"
            placeholder="全部 API Key"
            filterable
            footer-action-label="从详细列表选择…"
            @footer-action="openDimensionExplorer('api_keys', 'select')"
          />
          <CSelect
            v-model="filters.credentialId"
            :options="credentialOptions"
            placeholder="全部凭证"
            filterable
            footer-action-label="从详细列表选择…"
            @footer-action="openDimensionExplorer('credentials', 'select')"
          />
          <CSelect v-model="filters.outcome" :options="outcomeOptions" placeholder="全部结果" />
        </div>
      </div>
    </CCard>

    <div class="stats-content relative section-grid">
      <CAlert v-if="overviewQuery.isError.value || requestsQuery.isError.value" type="error">
        <div class="toolbar">
          <span>加载统计失败：{{ queryErrorMessage }}</span>
          <RefreshButton :query="combinedQuery" label="重试" size="sm" />
        </div>
      </CAlert>

      <div class="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <StatTile
          class="sm:order-1 xl:order-1"
          label="请求数"
          :value="formatCompactNumber(totals?.request_count ?? 0)"
          tone="brand"
          :icon="Activity"
          meta="当前筛选范围"
        >
          <template #corner>
            <CTooltip :content="successRateTooltip">
              <CProgress
                class="rounded-full"
                :percentage="successRatePercentage ?? 0"
                :label="successRatePercentage === null ? '-' : undefined"
                variant="success-rate"
                :stroke-width="5"
                :size="52"
                aria-label="成功率"
                tabindex="0"
              />
            </CTooltip>
          </template>
        </StatTile>
        <StatTile
          class="sm:order-3 xl:order-2"
          label="输入 Token"
          :value="formatTokenNumber(totals?.input_tokens ?? null)"
          tone="accent"
          :icon="MessageSquareText"
          :meta="inputCacheMeta"
        >
          <template #corner>
            <CTooltip :content="inputCacheTooltip">
              <CProgress
                class="rounded-full"
                :percentage="inputCacheHitPercentage ?? 0"
                :label="inputCacheHitPercentage === null ? '-' : undefined"
                variant="cache-hit"
                :stroke-width="5"
                :size="52"
                aria-label="缓存命中率"
                tabindex="0"
              />
            </CTooltip>
          </template>
        </StatTile>
        <StatTile
          class="sm:order-4 xl:order-3"
          label="输出 Token"
          :value="formatTokenNumber(totals?.output_tokens ?? null)"
          tone="accent"
          :icon="BotMessageSquare"
          :meta="formatTokenCoverage(totals?.usage_coverage ?? null)"
        />
        <StatTile
          class="sm:order-2 xl:order-4"
          label="积分消耗"
          :value="formatCredit(totals?.total_credit ?? null)"
          tone="warning"
          :icon="CircleDollarSign"
          meta="数据舍入误差 <0.05%"
        />
        <StatTile
          class="sm:order-5 xl:order-5"
          label="p95 首个有效输出"
          :value="
            formatLatencyPercentile(
              totals?.p95_first_output_ms ?? null,
              totals?.p95_first_output_ms_overflow,
            )
          "
          tone="brand"
          :icon="TimerReset"
          meta="仅成功请求"
        />
        <StatTile
          class="sm:order-6 xl:order-6"
          label="p95 总耗时"
          :value="
            formatLatencyPercentile(totals?.p95_total_ms ?? null, totals?.p95_total_ms_overflow)
          "
          tone="success"
          :icon="Clock3"
          meta="仅成功请求"
        />
      </div>

      <CCard title="趋势">
        <template #header-extra>
          <div class="stats-trend-metric-select w-40 lg:hidden">
            <label for="stats-trend-metric" class="sr-only">趋势指标</label>
            <CSelect
              id="stats-trend-metric"
              :model-value="selectedMetric"
              :options="metricSelectOptions"
              size="sm"
              @update:model-value="selectMetric"
            />
          </div>
          <div class="stats-trend-metric-buttons hidden flex-wrap justify-end gap-1.5 lg:flex">
            <CButton
              v-for="metric in metricOptions"
              :key="metric"
              size="sm"
              :variant="selectedMetric === metric ? 'primary' : 'ghost'"
              @click="selectedMetric = metric"
            >
              {{ metricLabel(metric) }}
            </CButton>
          </div>
        </template>
        <StatsTrendChart
          :points="overviewQuery.data.value?.series ?? []"
          :metric="selectedMetric"
          :timezone="timezone"
        />
      </CCard>

      <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <CCard
          v-for="ranking in [
            { key: 'models', title: '模型排行' },
            { key: 'api_keys', title: 'API Key 排行' },
            { key: 'credentials', title: '凭证排行' },
          ] as const"
          :key="ranking.key"
          :title="ranking.title"
        >
          <template #header-extra>
            <CButton size="sm" variant="ghost" @click="openDimensionExplorer(ranking.key)">
              查看全部
            </CButton>
          </template>
          <div v-if="breakdownRows(ranking.key).length" class="overflow-x-auto">
            <table class="w-full min-w-[34rem] text-sm">
              <thead class="text-xs text-muted">
                <tr class="border-b border-border">
                  <th class="px-2 py-2 text-left">名称</th>
                  <th class="px-2 py-2 text-right">调用</th>
                  <th class="px-2 py-2 text-right">成功率</th>
                  <th class="px-2 py-2 text-right">Token</th>
                  <th class="px-2 py-2 text-right">积分</th>
                  <th class="px-2 py-2 text-right">p95</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in breakdownRows(ranking.key)"
                  :key="row.id"
                  class="border-b border-border/60"
                >
                  <td class="px-2 py-2 font-medium text-text-strong">{{ row.label }}</td>
                  <td class="px-2 py-2 text-right tabular-nums">{{ row.request_count }}</td>
                  <td class="px-2 py-2 text-right tabular-nums">
                    {{ formatPercent(row.success_rate) }}
                  </td>
                  <td class="px-2 py-2 text-right tabular-nums">
                    {{ formatTokenNumber(row.total_tokens) }}
                  </td>
                  <td class="px-2 py-2 text-right tabular-nums">
                    {{ formatCredit(row.total_credit) }}
                  </td>
                  <td class="px-2 py-2 text-right tabular-nums">
                    {{ formatLatencyPercentile(row.p95_total_ms, row.p95_total_ms_overflow) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="grid min-h-24 place-items-center text-sm text-muted">暂无排行数据</div>
        </CCard>
      </div>

      <CCard title="请求明细（最近 90 天）">
        <div class="overflow-x-auto rounded-lg border border-border">
          <table class="w-full min-w-[64rem] text-sm">
            <thead class="bg-surface-2/50 text-xs text-muted">
              <tr>
                <th class="px-4 py-3 text-left">时间</th>
                <th class="px-4 py-3 text-left">来源</th>
                <th class="px-4 py-3 text-left">模型</th>
                <th class="px-4 py-3 text-left">API Key</th>
                <th class="px-4 py-3 text-left">凭证</th>
                <th class="px-4 py-3 text-left">结果</th>
                <th class="px-4 py-3 text-right">Token</th>
                <th class="px-4 py-3 text-right">积分消耗</th>
                <th class="px-4 py-3 text-right">总耗时</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="{ request, snapshot } in requestRows"
                :key="request.id"
                class="cursor-pointer border-t border-border/60 transition-colors hover:bg-surface-2"
                tabindex="0"
                @click="openDetail(request, snapshot)"
                @keyup.enter="openDetail(request, snapshot)"
              >
                <td class="px-4 py-3 whitespace-nowrap">
                  {{ formatTimestamp(request.started_at, timezone) }}
                </td>
                <td class="px-4 py-3">{{ sourceLabel(request.source) }}</td>
                <td class="max-w-56 truncate px-4 py-3" :title="request.requested_model">
                  {{ request.requested_model }}
                </td>
                <td class="px-4 py-3">{{ request.api_key_name ?? '-' }}</td>
                <td class="px-4 py-3">{{ request.credential_label ?? '-' }}</td>
                <td class="px-4 py-3">
                  <CTag :type="outcomeTag(request.outcome)">{{
                    outcomeLabel(request.outcome)
                  }}</CTag>
                </td>
                <td class="px-4 py-3 text-right tabular-nums">
                  <CTooltip class="request-token-tooltip">
                    <span class="cursor-help underline decoration-dotted underline-offset-2">
                      {{ formatTokenNumber(request.total_tokens) }}
                    </span>
                    <template #content>
                      <span class="block whitespace-nowrap">
                        输入：{{ formatTokenNumber(request.input_tokens) }}（{{
                          requestCacheHitLabel(request)
                        }}）
                      </span>
                      <span class="block whitespace-nowrap">
                        输出：{{ formatTokenNumber(request.output_tokens) }}
                      </span>
                    </template>
                  </CTooltip>
                </td>
                <td class="px-4 py-3 text-right tabular-nums">
                  {{ formatCredit(request.credit) }}
                </td>
                <td class="px-4 py-3 text-right tabular-nums">
                  {{ formatDurationMs(request.duration_ms) }}
                </td>
              </tr>
            </tbody>
          </table>
          <div
            v-if="requestsQuery.isLoading.value && requestItems.length === 0"
            class="grid min-h-28 place-items-center text-sm text-muted"
          >
            正在加载请求明细…
          </div>
          <div
            v-else-if="requestItems.length === 0 && !requestsQuery.isError.value"
            class="grid min-h-28 place-items-center text-sm text-muted"
          >
            暂无请求明细
          </div>
        </div>
        <div class="mt-4 space-y-3 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex flex-wrap items-center gap-3">
              <span class="text-muted">共 {{ requestTotal }} 条</span>
              <div class="w-28">
                <CSelect
                  :model-value="requestPageSize"
                  :options="requestPageSizeOptions"
                  size="sm"
                  :disabled="requestPageLoading"
                  @update:model-value="changeRequestPageSize"
                />
              </div>
            </div>
            <div
              class="request-pagination-desktop hidden flex-wrap items-center justify-end gap-1.5 md:flex"
            >
              <CButton
                class="request-pagination-nav w-8 !px-0"
                size="sm"
                variant="secondary"
                aria-label="首页"
                title="首页"
                :disabled="currentRequestPage <= 1 || requestPageLoading"
                @click="goToRequestPage(1)"
              >
                <template #icon><ChevronsLeft :size="18" /></template>
              </CButton>
              <CButton
                class="request-pagination-nav w-8 !px-0"
                size="sm"
                variant="secondary"
                aria-label="上一页"
                title="上一页"
                :disabled="currentRequestPage <= 1 || requestPageLoading"
                @click="goToRequestPage(currentRequestPage - 1)"
              >
                <template #icon><ChevronLeft :size="18" /></template>
              </CButton>
              <template
                v-for="item in requestPaginationItems"
                :key="typeof item === 'number' ? item : `ellipsis-${item.page}`"
              >
                <CButton
                  v-if="typeof item !== 'number'"
                  class="w-8 !px-0"
                  size="sm"
                  variant="secondary"
                  :aria-label="`跳转到第 ${item.page} 页`"
                  :title="`跳转到第 ${item.page} 页`"
                  :disabled="requestPageLoading"
                  @click="goToRequestPage(item.page)"
                >
                  …
                </CButton>
                <CButton
                  v-else
                  class="w-8 !px-0 tabular-nums"
                  size="sm"
                  :variant="item === currentRequestPage ? 'primary' : 'secondary'"
                  :aria-label="`第 ${item} 页`"
                  :aria-current="item === currentRequestPage ? 'page' : undefined"
                  :disabled="requestPageLoading"
                  @click="goToRequestPage(item)"
                >
                  {{ item }}
                </CButton>
              </template>
              <CButton
                class="request-pagination-nav w-8 !px-0"
                size="sm"
                variant="secondary"
                aria-label="下一页"
                title="下一页"
                :disabled="currentRequestPage >= requestTotalPages || requestPageLoading"
                @click="goToRequestPage(currentRequestPage + 1)"
              >
                <template #icon><ChevronRight :size="18" /></template>
              </CButton>
              <CButton
                class="request-pagination-nav w-8 !px-0"
                size="sm"
                variant="secondary"
                aria-label="末页"
                title="末页"
                :disabled="currentRequestPage >= requestTotalPages || requestPageLoading"
                @click="goToRequestPage(requestTotalPages)"
              >
                <template #icon><ChevronsRight :size="18" /></template>
              </CButton>
              <span class="text-muted">跳至</span>
              <div class="w-20">
                <CInputNumber
                  v-model="requestJumpPage"
                  size="sm"
                  :min="requestTotalPages === 0 ? 0 : 1"
                  :max="requestTotalPages"
                  :disabled="requestTotalPages === 0 || requestPageLoading"
                  @keyup.enter="jumpToRequestPage"
                />
              </div>
              <CButton
                size="sm"
                variant="secondary"
                aria-label="跳转"
                :disabled="requestTotalPages === 0 || requestPageLoading"
                @click="jumpToRequestPage"
              >
                跳转
              </CButton>
            </div>
          </div>

          <div
            class="request-pagination-mobile flex min-w-0 items-center justify-center gap-1 md:hidden"
          >
            <CButton
              class="!h-8 !w-8 !px-0"
              size="sm"
              variant="secondary"
              aria-label="首页"
              title="首页"
              :disabled="currentRequestPage <= 1 || requestPageLoading"
              @click="goToRequestPage(1)"
            >
              <template #icon><ChevronsLeft :size="15" /></template>
            </CButton>
            <CButton
              class="!h-8 !w-8 !px-0"
              size="sm"
              variant="secondary"
              aria-label="上一页"
              title="上一页"
              :disabled="currentRequestPage <= 1 || requestPageLoading"
              @click="goToRequestPage(currentRequestPage - 1)"
            >
              <template #icon><ChevronLeft :size="15" /></template>
            </CButton>
            <label
              class="inline-flex h-8 min-w-0 flex-1 items-center justify-center gap-1 rounded-md border border-border bg-surface px-2 text-[13px] whitespace-nowrap text-text shadow-[var(--shadow-xs)]"
            >
              <span>第</span>
              <input
                class="request-pagination-mobile-input c-control-focus h-6 w-12 rounded border border-border bg-surface-2 px-1 text-center text-[13px] text-text tabular-nums disabled:bg-surface-3 disabled:text-muted/60"
                type="number"
                inputmode="numeric"
                enterkeyhint="done"
                :value="requestMobileJumpInput"
                :min="requestTotalPages === 0 ? 0 : 1"
                :max="requestTotalPages"
                :disabled="requestTotalPages === 0 || requestPageLoading"
                aria-label="页码"
                @input="updateRequestMobileJumpInput"
                @blur="applyRequestMobileJumpInput"
                @keyup.enter="confirmRequestMobileJumpInput"
              />
              <span class="request-pagination-mobile-total"> / {{ requestTotalPages }} 页 </span>
            </label>
            <CButton
              class="!h-8 !w-8 !px-0"
              size="sm"
              variant="secondary"
              aria-label="下一页"
              title="下一页"
              :disabled="currentRequestPage >= requestTotalPages || requestPageLoading"
              @click="goToRequestPage(currentRequestPage + 1)"
            >
              <template #icon><ChevronRight :size="15" /></template>
            </CButton>
            <CButton
              class="!h-8 !w-8 !px-0"
              size="sm"
              variant="secondary"
              aria-label="末页"
              title="末页"
              :disabled="currentRequestPage >= requestTotalPages || requestPageLoading"
              @click="goToRequestPage(requestTotalPages)"
            >
              <template #icon><ChevronsRight :size="15" /></template>
            </CButton>
          </div>

          <span v-if="requestPageError" class="block text-sm text-error-600 md:text-right">
            {{ requestPageError }}
          </span>
        </div>
      </CCard>

      <Transition name="c-data-table-loading">
        <div
          v-if="combinedFetching"
          class="stats-loading-overlay c-data-table-loading absolute inset-0 z-30 bg-surface/60 text-brand-500 backdrop-blur-[1px]"
          role="status"
          aria-label="正在刷新统计数据"
        >
          <div class="c-data-table-loading-indicator flex w-full justify-center">
            <CSpin size="lg" aria-hidden="true" />
          </div>
        </div>
      </Transition>
    </div>

    <CDrawer
      :open="detailOpen"
      placement="right"
      :width="520"
      title="请求详情"
      @update:open="setDetailOpen"
    >
      <div v-if="detailLoading" class="grid min-h-48 place-items-center text-sm text-muted">
        正在加载详情…
      </div>
      <CAlert v-else-if="detailError" type="error">{{ detailError }}</CAlert>
      <div v-else-if="detail" class="space-y-5">
        <CAlert type="info">此处仅展示脱敏指标，不保存请求提示词、回答、Token 或工具参数。</CAlert>

        <section>
          <h3 class="mb-2 text-sm font-semibold text-text-strong">请求</h3>
          <dl
            class="stats-request-detail-list grid grid-cols-1 gap-x-3 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,0.8fr)_minmax(0,1.2fr)] sm:gap-y-2 [&_dd]:min-w-0 [&_dd]:break-words"
          >
            <dt class="text-muted">ID</dt>
            <dd class="font-mono break-all">{{ detail.id }}</dd>
            <dt class="text-muted">时间</dt>
            <dd>{{ formatTimestamp(detail.started_at, timezone) }}</dd>
            <dt class="text-muted">来源</dt>
            <dd>{{ sourceLabel(detail.source) }}</dd>
            <dt class="text-muted">请求模型</dt>
            <dd class="break-all">{{ detail.requested_model }}</dd>
            <dt class="text-muted">上游模型</dt>
            <dd class="break-all">{{ displayNullable(detail.upstream_model) }}</dd>
            <dt class="text-muted">API Key</dt>
            <dd>{{ displayNullable(detail.api_key_name) }}</dd>
            <dt class="text-muted">凭证</dt>
            <dd>{{ displayNullable(detail.credential_label) }}</dd>
            <dt class="text-muted">流式</dt>
            <dd>{{ displayBoolean(detail.client_stream) }}</dd>
            <dt class="text-muted">思考模式</dt>
            <dd>{{ displayNullable(detail.thinking_mode) }}</dd>
          </dl>
        </section>

        <section>
          <h3 class="mb-2 text-sm font-semibold text-text-strong">结果</h3>
          <dl
            class="stats-request-detail-list grid grid-cols-1 gap-x-3 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,0.8fr)_minmax(0,1.2fr)] sm:gap-y-2 [&_dd]:min-w-0 [&_dd]:break-words"
          >
            <dt class="text-muted">结果</dt>
            <dd>{{ outcomeLabel(detail.outcome) }}</dd>
            <dt class="text-muted">HTTP 状态</dt>
            <dd>{{ displayNullable(detail.http_status) }}</dd>
            <dt class="text-muted">逻辑状态</dt>
            <dd>{{ displayNullable(detail.result_status) }}</dd>
            <dt class="text-muted">错误类型</dt>
            <dd>{{ displayNullable(detail.error_type) }}</dd>
            <dt class="text-muted">结束原因</dt>
            <dd>{{ displayNullable(detail.finish_reason) }}</dd>
            <dt class="text-muted">消息数</dt>
            <dd>{{ displayNullable(detail.message_count) }}</dd>
            <dt class="text-muted">声明工具数</dt>
            <dd>{{ displayNullable(detail.tool_count) }}</dd>
            <dt class="text-muted">工具调用数</dt>
            <dd>{{ displayNullable(detail.tool_call_count) }}</dd>
            <dt class="text-muted">重试次数</dt>
            <dd>{{ displayNullable(detail.retry_count) }}</dd>
            <dt class="text-muted">请求大小</dt>
            <dd>{{ displayBytes(detail.request_bytes) }}</dd>
            <dt class="text-muted">响应大小</dt>
            <dd>{{ displayBytes(detail.response_bytes) }}</dd>
          </dl>
        </section>

        <section>
          <h3 class="mb-2 text-sm font-semibold text-text-strong">用量</h3>
          <dl
            class="stats-request-detail-list grid grid-cols-1 gap-x-3 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,0.8fr)_minmax(0,1.2fr)] sm:gap-y-2 [&_dd]:min-w-0 [&_dd]:break-words"
          >
            <dt class="text-muted">输入 Token</dt>
            <dd>{{ formatTokenNumber(detail.input_tokens) }}</dd>
            <dt class="text-muted">输出 Token</dt>
            <dd>{{ formatTokenNumber(detail.output_tokens) }}</dd>
            <dt class="text-muted">总 Token</dt>
            <dd>{{ formatTokenNumber(detail.total_tokens) }}</dd>
            <dt class="text-muted">推理 Token</dt>
            <dd>{{ formatTokenNumber(detail.reasoning_tokens) }}</dd>
            <dt class="text-muted">缓存命中 Token</dt>
            <dd>{{ formatTokenNumber(detail.cache_hit_tokens) }}</dd>
            <dt class="text-muted">缓存未命中 Token</dt>
            <dd>{{ formatTokenNumber(detail.cache_miss_tokens) }}</dd>
            <dt class="text-muted">缓存写入 Token</dt>
            <dd>{{ formatTokenNumber(detail.cache_write_tokens) }}</dd>
            <dt class="text-muted">积分</dt>
            <dd>{{ formatCredit(detail.credit) }}</dd>
          </dl>
        </section>

        <section>
          <h3 class="mb-2 text-sm font-semibold text-text-strong">性能</h3>
          <dl
            class="stats-request-detail-list grid grid-cols-1 gap-x-3 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,0.8fr)_minmax(0,1.2fr)] sm:gap-y-2 [&_dd]:min-w-0 [&_dd]:break-words"
          >
            <dt class="text-muted">总耗时</dt>
            <dd>{{ formatDurationMs(detail.duration_ms) }}</dd>
            <dt class="text-muted">首个 SSE 事件</dt>
            <dd>{{ formatDurationMs(detail.first_event_ms) }}</dd>
            <dt class="text-muted">首个有效输出</dt>
            <dd>{{ formatDurationMs(detail.first_output_ms) }}</dd>
            <dt class="text-muted">首个推理</dt>
            <dd>{{ formatDurationMs(detail.first_reasoning_ms) }}</dd>
            <dt class="text-muted">首个正文</dt>
            <dd>{{ formatDurationMs(detail.first_content_ms) }}</dd>
          </dl>
        </section>
      </div>
    </CDrawer>

    <CDrawer
      :open="dimensionOpen"
      placement="right"
      :width="720"
      :title="dimensionTitle"
      @update:open="setDimensionOpen"
    >
      <form class="mb-4 flex gap-2" @submit.prevent="searchDimensions">
        <input
          v-model="dimensionSearch"
          type="search"
          maxlength="100"
          class="c-control-focus h-[38px] min-w-0 flex-1 rounded-md border border-border bg-surface px-3 text-sm text-text"
          placeholder="按名称或标识搜索"
        />
        <CButton type="submit" variant="primary" :loading="dimensionLoading">搜索</CButton>
      </form>

      <CAlert v-if="dimensionError" type="error">{{ dimensionError }}</CAlert>
      <div
        v-else-if="dimensionLoading && dimensionItems.length === 0"
        class="grid min-h-40 place-items-center text-sm text-muted"
      >
        正在加载…
      </div>
      <div
        v-else-if="dimensionItems.length"
        class="overflow-x-auto rounded-lg border border-border"
      >
        <table class="w-full min-w-[38rem] text-sm">
          <thead class="bg-surface-2/50 text-xs text-muted">
            <tr>
              <th class="px-3 py-2 text-left">名称</th>
              <th class="px-3 py-2 text-right">调用</th>
              <th class="px-3 py-2 text-right">成功率</th>
              <th class="px-3 py-2 text-right">Token</th>
              <th class="px-3 py-2 text-right">积分</th>
              <th class="px-3 py-2 text-right">p95</th>
              <th v-if="dimensionMode === 'select'" class="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in dimensionItems" :key="item.id" class="border-t border-border/60">
              <td class="px-3 py-2">
                <div class="font-medium text-text-strong">{{ item.label }}</div>
                <div v-if="item.label !== item.id" class="mt-0.5 font-mono text-xs text-muted">
                  {{ item.id }}
                </div>
              </td>
              <td class="px-3 py-2 text-right tabular-nums">{{ item.request_count }}</td>
              <td class="px-3 py-2 text-right tabular-nums">
                {{ formatPercent(item.success_rate) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">
                {{ formatTokenNumber(item.total_tokens) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">
                {{ formatCredit(item.total_credit) }}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">
                {{ formatLatencyPercentile(item.p95_total_ms, item.p95_total_ms_overflow) }}
              </td>
              <td v-if="dimensionMode === 'select'" class="px-3 py-2 text-right">
                <CButton size="sm" variant="secondary" @click="selectDimensionItem(item)">
                  选择
                </CButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="grid min-h-40 place-items-center text-sm text-muted">暂无匹配数据</div>

      <div class="mt-4 flex items-center justify-between gap-3">
        <CButton
          variant="secondary"
          :disabled="dimensionCursorHistory.length === 0"
          :loading="dimensionLoading"
          @click="previousDimensionPage"
        >
          上一页
        </CButton>
        <CButton
          variant="secondary"
          :disabled="!dimensionNextCursor"
          :loading="dimensionLoading"
          @click="nextDimensionPage"
        >
          下一页
        </CButton>
      </div>
    </CDrawer>
  </div>
</template>
