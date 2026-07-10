import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buildChartGeometry,
  buildPaginationItems,
  buildPresetRange,
  buildStatsSearchParams,
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
  metricDisplayValue,
  resolveBrowserTimeZone,
  sourceLabel,
  toLocalInputValue,
} from '../utils/stats';

describe('统计工具', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('为分页生成首尾页、当前页窗口和指向相邻隐藏页的省略号', () => {
    expect(buildPaginationItems(1, 0)).toEqual([]);
    expect(buildPaginationItems(1, 1)).toEqual([1]);
    expect(buildPaginationItems(1, 99)).toEqual([1, 2, 3, { type: 'ellipsis', page: 4 }, 99]);
    expect(buildPaginationItems(2, 99)).toEqual([1, 2, 3, 4, { type: 'ellipsis', page: 5 }, 99]);
    expect(buildPaginationItems(7, 99)).toEqual([
      1,
      { type: 'ellipsis', page: 4 },
      5,
      6,
      7,
      8,
      9,
      { type: 'ellipsis', page: 10 },
      99,
    ]);
    expect(buildPaginationItems(98, 99)).toEqual([
      1,
      { type: 'ellipsis', page: 95 },
      96,
      97,
      98,
      99,
    ]);
    expect(buildPaginationItems(99, 99)).toEqual([1, { type: 'ellipsis', page: 96 }, 97, 98, 99]);
    expect(buildPaginationItems(4, 8)).toEqual([
      1,
      2,
      3,
      4,
      5,
      6,
      { type: 'ellipsis', page: 7 },
      8,
    ]);
  });

  it('解析浏览器时区并在缺失时回退 UTC', () => {
    expect(resolveBrowserTimeZone()).toBeTruthy();
    vi.spyOn(Intl, 'DateTimeFormat').mockReturnValue({
      resolvedOptions: () => ({ timeZone: '' }),
    } as Intl.DateTimeFormat);
    expect(resolveBrowserTimeZone()).toBe('UTC');
  });

  it('构造今日、固定天数和全部范围', () => {
    const now = new Date(2026, 6, 10, 15, 30, 45).getTime();
    const today = buildPresetRange('today', now);
    expect(today).toEqual({
      startAt: Math.floor(new Date(2026, 6, 10).getTime() / 1000),
      endAt: Math.floor(now / 1000) + 1,
    });
    expect(buildPresetRange('7d', now)).toEqual({
      startAt: Math.floor(now / 1000) + 1 - 7 * 86_400,
      endAt: Math.floor(now / 1000) + 1,
    });
    expect(buildPresetRange('30d', now).startAt).toBe(Math.floor(now / 1000) + 1 - 30 * 86_400);
    expect(buildPresetRange('90d', now).startAt).toBe(Math.floor(now / 1000) + 1 - 90 * 86_400);
    expect(buildPresetRange('all', now)).toEqual({
      startAt: 0,
      endAt: Math.floor(now / 1000) + 1,
    });
  });

  it('本地日期输入与 Unix 秒互转并拒绝非法值', () => {
    const timestamp = Math.floor(new Date(2026, 0, 2, 3, 4).getTime() / 1000);
    expect(toLocalInputValue(timestamp)).toBe('2026-01-02T03:04');
    expect(fromLocalInputValue('2026-01-02T03:04')).toBe(timestamp);
    expect(fromLocalInputValue('')).toBeNull();
    expect(fromLocalInputValue('not-a-date')).toBeNull();
  });

  it('格式化数字、比例、耗时、积分、时间和覆盖率', () => {
    expect(formatCompactNumber(null)).toBe('-');
    expect(formatCompactNumber(0)).toBe('0');
    expect(formatCompactNumber(12_345)).toContain('万');
    expect(formatTokenNumber(null)).toBe('-');
    expect(formatTokenNumber(999)).toBe('999');
    expect(formatTokenNumber(1_234)).toBe('1.23k');
    expect(formatTokenNumber(12_345)).toBe('12.3k');
    expect(formatTokenNumber(123_456)).toBe('123.5k');
    expect(formatTokenNumber(999_949)).toBe('999.9k');
    expect(formatTokenNumber(999_950)).toBe('1.00M');
    expect(formatTokenNumber(999_999)).toBe('1.00M');
    expect(formatTokenNumber(1_250_000)).toBe('1.25M');
    expect(formatPercent(null)).toBe('-');
    expect(formatPercent(0.987)).toBe('98.7%');
    expect(formatDurationMs(null)).toBe('-');
    expect(formatDurationMs(875)).toBe('875 ms');
    expect(formatDurationMs(1_250)).toBe('1.25 s');
    expect(formatDurationMs(600_000, true)).toBe('≥ 10 分钟');
    expect(formatLatencyPercentile(null)).toBe('-');
    expect(formatLatencyPercentile(50)).toBe('< 50 ms');
    expect(formatLatencyPercentile(1_000)).toBe('< 1 s');
    expect(formatLatencyPercentile(10_000)).toBe('< 10 s');
    expect(formatLatencyPercentile(30_000)).toBe('< 30 s');
    expect(formatLatencyPercentile(600_000)).toBe('< 10 min');
    expect(formatLatencyPercentile(600_000, true)).toBe('≥ 10 min');
    expect(() => formatLatencyPercentile(1_500)).toThrow('未知的延迟分桶上界');
    expect(formatCredit(null)).toBe('-');
    expect(formatCredit(3)).toBe('3.00');
    expect(formatCredit(3.1)).toBe('3.10');
    expect(formatCredit(3.14159)).toBe('3.14');
    expect(formatTimestamp(null)).toBe('-');
    expect(formatTimestamp(1_767_225_600)).not.toBe('-');
    expect(formatTokenCoverage(null)).toBe('暂无 usage 覆盖数据');
    expect(formatTokenCoverage(0.75)).toBe('usage 覆盖率 75.0%');
  });

  it('计算缓存命中率，并在任一分项未知或样本为零时保持未知', () => {
    expect(cacheHitPercentage(80, 20)).toBe(80);
    expect(cacheHitPercentage(2, 1)).toBe(67);
    expect(cacheHitPercentage(null, 20)).toBeNull();
    expect(cacheHitPercentage(80, null)).toBeNull();
    expect(cacheHitPercentage(0, 0)).toBeNull();
  });

  it('提供来源和趋势指标中文标签', () => {
    expect(sourceLabel('external_api')).toBe('外部 API');
    expect(sourceLabel('admin_playground')).toBe('管理台 Playground');
    expect(sourceLabel('credential_test')).toBe('凭证测试');
    expect(sourceLabel('future_source')).toBe('future_source');
    expect(metricLabel('request_count')).toBe('请求数');
    expect(metricLabel('total_tokens')).toBe('Token');
    expect(metricLabel('total_credit')).toBe('积分');
    expect(metricLabel('success_rate')).toBe('成功率');
    expect(metricLabel('p95_first_output_ms')).toBe('p95 首输出');
    expect(metricLabel('p95_total_ms')).toBe('p95 总耗时');
    expect(metricDisplayValue('request_count', 12)).toBe('12');
    expect(metricDisplayValue('success_rate', 0.5)).toBe('50.0%');
    expect(metricDisplayValue('p95_first_output_ms', 500)).toBe('< 500 ms');
    expect(metricDisplayValue('p95_total_ms', 30_000)).toBe('< 30 s');
    expect(metricDisplayValue('p95_total_ms', 600_000, true)).toBe('≥ 10 min');
    expect(metricDisplayValue('total_credit', 1.25)).toBe('1.25');
    expect(metricDisplayValue('total_tokens', 12_345)).toBe('12.3k');
  });

  it('无可选筛选时只序列化基础参数', () => {
    expect(
      buildStatsSearchParams({
        start_at: 1,
        end_at: 2,
        timezone: 'UTC',
        traffic: 'all',
      }),
    ).toBe('start_at=1&end_at=2&timezone=UTC&traffic=all');
  });

  it('为统计接口构造稳定查询字符串并省略空筛选', () => {
    const params = buildStatsSearchParams({
      start_at: 10,
      end_at: 20,
      timezone: 'Asia/Taipei',
      traffic: 'external',
      model: 'glm/5',
      api_key_id: '',
      credential_id: 'credential id',
      outcome: 'success',
      page: 3,
      page_size: 50,
      snapshot_id: 123,
      snapshot_time: 15,
    });
    expect(params).toBe(
      'start_at=10&end_at=20&timezone=Asia%2FTaipei&traffic=external&model=glm%2F5&credential_id=credential+id&outcome=success&page=3&page_size=50&snapshot_id=123&snapshot_time=15',
    );
  });

  it('为趋势图生成空、单点、缺口和普通折线路径', () => {
    expect(buildChartGeometry([], 'request_count')).toEqual({
      linePath: '',
      areaPath: '',
      points: [],
      maxValue: 0,
    });

    const one = buildChartGeometry([{ period_start: 1, request_count: 4 }], 'request_count');
    expect(one.points).toHaveLength(1);
    expect(one.linePath).toContain('M 400');
    expect(one.areaPath).toContain('Z');
    expect(one.maxValue).toBe(4);

    const unknown = buildChartGeometry(
      [{ period_start: 1, success_rate: null }, { period_start: 2 }],
      'success_rate',
    );
    expect(unknown).toEqual({ linePath: '', areaPath: '', points: [], maxValue: 0 });

    const zeroAfterLeadingGap = buildChartGeometry(
      [
        { period_start: 1, total_tokens: null },
        { period_start: 2, total_tokens: 0 },
        { period_start: 3, total_tokens: 0 },
      ],
      'total_tokens',
    );
    expect(zeroAfterLeadingGap.maxValue).toBe(0);
    expect(zeroAfterLeadingGap.points).toHaveLength(2);
    expect(zeroAfterLeadingGap.linePath).toContain('L');

    const gaps = buildChartGeometry(
      [
        { period_start: 1, total_tokens: 10 },
        { period_start: 2, total_tokens: null },
        { period_start: 3, total_tokens: 20 },
        { period_start: 4, total_tokens: 5 },
        { period_start: 5 },
      ],
      'total_tokens',
      400,
      120,
    );
    expect(gaps.points.map((point) => point.x)).toEqual([32, 200, 284]);
    expect(gaps.linePath.match(/M /g)).toHaveLength(2);
    expect(gaps.linePath.match(/L /g)).toHaveLength(1);
    expect(gaps.areaPath.match(/Z/g)).toHaveLength(2);
    expect(gaps.maxValue).toBe(20);

    const normal = buildChartGeometry(
      [
        { period_start: 1, total_tokens: 10 },
        { period_start: 2, total_tokens: 20 },
        { period_start: 3, total_tokens: 5 },
      ],
      'total_tokens',
      400,
      120,
    );
    expect(normal.points[0]).toEqual(expect.objectContaining({ x: 32, value: 10 }));
    expect(normal.points[1]!.y).toBe(24);
    expect(normal.maxValue).toBe(20);

    const irregular = buildChartGeometry(
      [
        { period_start: 0, request_count: 1 },
        { period_start: 1, request_count: 1 },
        { period_start: 10, request_count: 1 },
      ],
      'request_count',
      400,
      120,
    );
    expect(irregular.points[1]!.x).toBeCloseTo(65.6);

    const firstOutputOverflow = buildChartGeometry(
      [
        {
          period_start: 1,
          p95_first_output_ms: 600_000,
          p95_first_output_ms_overflow: true,
        },
      ],
      'p95_first_output_ms',
    );
    expect(firstOutputOverflow.points[0]!.overflow).toBe(true);
    const totalOverflow = buildChartGeometry(
      [{ period_start: 1, p95_total_ms: 600_000, p95_total_ms_overflow: true }],
      'p95_total_ms',
    );
    expect(totalOverflow.points[0]!.overflow).toBe(true);
  });
});
