import type {
  StatsMetric,
  StatsDimensionQuery,
  StatsOverviewQuery,
  StatsRangePreset,
  StatsRequestsQuery,
  StatsSeriesPoint,
} from '../types';

const DAY_SECONDS = 86_400;
const TOKEN_MILLION_ROUNDING_THRESHOLD = 999_950;
const LATENCY_BUCKET_UPPER_BOUNDS_MS = [
  50, 100, 250, 500, 1_000, 2_000, 5_000, 10_000, 30_000, 60_000, 120_000, 300_000, 600_000,
] as const;

export interface StatsRange {
  startAt: number;
  endAt: number;
}

export interface ChartPoint {
  x: number;
  y: number;
  value: number;
  bucketStart: number;
  period?: string;
  overflow: boolean;
}

export interface ChartGeometry {
  linePath: string;
  areaPath: string;
  points: ChartPoint[];
  maxValue: number;
}

export interface PaginationEllipsis {
  type: 'ellipsis';
  page: number;
}

export type PaginationItem = number | PaginationEllipsis;

export function buildPaginationItems(currentPage: number, totalPages: number): PaginationItem[] {
  if (totalPages < 1) return [];
  const visiblePages = new Set<number>([1, totalPages]);
  const windowStart = Math.max(1, currentPage - 2);
  const windowEnd = Math.min(totalPages, currentPage + 2);
  for (let page = windowStart; page <= windowEnd; page += 1) visiblePages.add(page);

  const pages = [...visiblePages].sort((left, right) => left - right);
  const items: PaginationItem[] = [];
  for (const [index, page] of pages.entries()) {
    const previousPage = pages[index - 1];
    if (previousPage !== undefined && page - previousPage > 1) {
      items.push({
        type: 'ellipsis',
        page: page <= currentPage ? page - 1 : previousPage + 1,
      });
    }
    items.push(page);
  }
  return items;
}

export function resolveBrowserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

export function buildPresetRange(
  preset: Exclude<StatsRangePreset, 'custom'>,
  nowMs = Date.now(),
): StatsRange {
  // 后端使用 [start_at, end_at)，加一秒才能纳入当前整秒内已完成的请求。
  const endAt = Math.floor(nowMs / 1000) + 1;
  if (preset === 'today') {
    const now = new Date(nowMs);
    return {
      startAt: Math.floor(
        new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000,
      ),
      endAt,
    };
  }
  if (preset === 'all') return { startAt: 0, endAt };
  const days = preset === '7d' ? 7 : preset === '30d' ? 30 : 90;
  return { startAt: endAt - days * DAY_SECONDS, endAt };
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

export function toLocalInputValue(timestampSeconds: number): string {
  const date = new Date(timestampSeconds * 1000);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromLocalInputValue(value: string): number | null {
  if (!value) return null;
  const milliseconds = new Date(value).getTime();
  return Number.isNaN(milliseconds) ? null : Math.floor(milliseconds / 1000);
}

export function formatCompactNumber(value: number | null): string {
  if (value === null) return '-';
  return new Intl.NumberFormat('zh-CN', {
    notation: value >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatTokenNumber(value: number | null): string {
  if (value === null) return '-';
  const absoluteValue = Math.abs(value);
  if (absoluteValue < 1_000) return new Intl.NumberFormat('en-US').format(value);

  const useMillions = absoluteValue >= TOKEN_MILLION_ROUNDING_THRESHOLD;
  const divisor = useMillions ? 1_000_000 : 1_000;
  const suffix = useMillions ? 'M' : 'k';
  const scaledValue = value / divisor;
  const fractionDigits = Math.abs(scaledValue) < 10 ? 2 : 1;
  return `${new Intl.NumberFormat('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(scaledValue)}${suffix}`;
}

export function formatPercent(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDurationMs(value: number | null, overflow = false): string {
  if (value === null) return '-';
  if (overflow) return '≥ 10 分钟';
  return value < 1_000 ? `${Math.round(value)} ms` : `${(value / 1_000).toFixed(2)} s`;
}

function latencyBoundaryParts(value: number): { amount: number; unit: string } {
  if (value >= 60_000) return { amount: value / 60_000, unit: 'min' };
  if (value >= 1_000) return { amount: value / 1_000, unit: 's' };
  return { amount: value, unit: 'ms' };
}

export function formatLatencyPercentile(value: number | null, overflow = false): string {
  if (value === null) return '-';
  if (overflow) return '≥ 10 min';
  const index = LATENCY_BUCKET_UPPER_BOUNDS_MS.indexOf(
    value as (typeof LATENCY_BUCKET_UPPER_BOUNDS_MS)[number],
  );
  if (index === -1) throw new Error(`未知的延迟分桶上界：${value}`);
  const upper = latencyBoundaryParts(value);
  return `< ${upper.amount} ${upper.unit}`;
}

export function formatCredit(value: number | null): string {
  if (value === null) return '-';
  return value.toFixed(2);
}

export function formatTimestamp(value: number | null, timezone?: string): string {
  if (value === null) return '-';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value * 1000));
}

export function formatTokenCoverage(value: number | null): string {
  return value === null ? '暂无 usage 覆盖数据' : `usage 覆盖率 ${formatPercent(value)}`;
}

export function cacheHitPercentage(
  hitTokens: number | null,
  missTokens: number | null,
): number | null {
  if (hitTokens === null || missTokens === null) return null;
  const cacheableTokens = hitTokens + missTokens;
  return cacheableTokens === 0 ? null : Math.round((hitTokens / cacheableTokens) * 100);
}

export function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    external_api: '外部 API',
    admin_playground: '管理台 Playground',
    credential_test: '凭证测试',
  };
  return labels[source] ?? source;
}

export function metricLabel(metric: StatsMetric): string {
  const labels: Record<StatsMetric, string> = {
    request_count: '请求数',
    total_tokens: 'Token',
    total_credit: '积分',
    success_rate: '成功率',
    p95_first_output_ms: 'p95 首输出',
    p95_total_ms: 'p95 总耗时',
  };
  return labels[metric];
}

export function metricDisplayValue(metric: StatsMetric, value: number, overflow = false): string {
  if (metric === 'success_rate') return formatPercent(value);
  if (metric === 'p95_first_output_ms' || metric === 'p95_total_ms') {
    return formatLatencyPercentile(value, overflow);
  }
  if (metric === 'total_credit') return formatCredit(value);
  if (metric === 'total_tokens') return formatTokenNumber(value);
  return formatCompactNumber(value);
}

function pointValue(point: StatsSeriesPoint, metric: StatsMetric): number | null {
  const value = point[metric];
  return typeof value === 'number' ? value : null;
}

function pointOverflow(point: StatsSeriesPoint, metric: StatsMetric): boolean {
  if (metric === 'p95_first_output_ms') return point.p95_first_output_ms_overflow === true;
  if (metric === 'p95_total_ms') return point.p95_total_ms_overflow === true;
  return false;
}

export function buildChartGeometry(
  series: StatsSeriesPoint[],
  metric: StatsMetric,
  width = 800,
  height = 240,
  horizontalPadding = 32,
): ChartGeometry {
  if (series.length === 0) {
    return { linePath: '', areaPath: '', points: [], maxValue: 0 };
  }

  const verticalPadding = 24;
  const drawableWidth = width - horizontalPadding * 2;
  const drawableHeight = height - verticalPadding * 2;
  const values = series.map((point) => pointValue(point, metric));
  const knownValues = values.filter((value): value is number => value !== null);
  if (knownValues.length === 0) {
    return { linePath: '', areaPath: '', points: [], maxValue: 0 };
  }

  const maxValue = Math.max(...knownValues);
  const denominator = maxValue || 1;
  const segments: ChartPoint[][] = [];
  let segment: ChartPoint[] = [];
  const minimumBucket = Math.min(...series.map((point) => point.period_start));
  const maximumBucket = Math.max(...series.map((point) => point.period_start));
  const bucketSpan = maximumBucket - minimumBucket;
  for (const [index, sourcePoint] of series.entries()) {
    const value = values[index];
    if (value === null) {
      if (segment.length > 0) segments.push(segment);
      segment = [];
      continue;
    }
    segment.push({
      x:
        bucketSpan === 0
          ? width / 2
          : horizontalPadding +
            ((sourcePoint.period_start - minimumBucket) / bucketSpan) * drawableWidth,
      y: verticalPadding + (1 - value / denominator) * drawableHeight,
      value,
      bucketStart: sourcePoint.period_start,
      period: sourcePoint.period,
      overflow: pointOverflow(sourcePoint, metric),
    });
  }
  if (segment.length > 0) segments.push(segment);

  const linePaths = segments.map((currentSegment) =>
    currentSegment
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
      .join(' '),
  );
  const linePath = linePaths.join(' ');
  const baseline = height - verticalPadding;
  const areaPath = segments
    .map(
      (currentSegment, index) =>
        `${linePaths[index]} L ${currentSegment[currentSegment.length - 1]!.x} ${baseline} L ${currentSegment[0]!.x} ${baseline} Z`,
    )
    .join(' ');
  const points = segments.flat();
  return { linePath, areaPath, points, maxValue };
}

export function buildStatsSearchParams(
  query: StatsOverviewQuery | StatsRequestsQuery | StatsDimensionQuery,
): string {
  const params = new URLSearchParams({
    start_at: String(query.start_at),
    end_at: String(query.end_at),
    timezone: query.timezone,
    traffic: query.traffic,
  });
  const optionalKeys = ['model', 'api_key_id', 'credential_id', 'outcome'] as const;
  for (const key of optionalKeys) {
    const value = (query as StatsRequestsQuery)[key];
    if (value) params.set(key, value);
  }
  if ('page' in query && query.page !== undefined) params.set('page', String(query.page));
  if ('page_size' in query && query.page_size !== undefined) {
    params.set('page_size', String(query.page_size));
  }
  if ('snapshot_id' in query && query.snapshot_id !== undefined) {
    params.set('snapshot_id', String(query.snapshot_id));
  }
  if ('snapshot_time' in query && query.snapshot_time !== undefined) {
    params.set('snapshot_time', String(query.snapshot_time));
  }
  if ('search' in query && query.search) params.set('search', query.search);
  if ('cursor' in query && query.cursor) params.set('cursor', query.cursor);
  if ('limit' in query && query.limit !== undefined) params.set('limit', String(query.limit));
  return params.toString();
}
