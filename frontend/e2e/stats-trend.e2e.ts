import { expect, test, type Page } from '@playwright/test';
import type { StatsOverviewResponse } from '../src/types';

const APP_URL = 'http://127.0.0.1:4175';

function overview(pointCount: number): StatsOverviewResponse {
  return {
    totals: {
      request_count: 900,
      success_rate: 1,
      input_tokens: 6000,
      output_tokens: 3000,
      total_tokens: 9000,
      cache_hit_tokens: 0,
      cache_miss_tokens: 6000,
      total_credit: 1.2,
      p95_first_output_ms: 500,
      p95_first_output_ms_overflow: false,
      p95_total_ms: 2000,
      p95_total_ms_overflow: false,
      usage_coverage: 1,
    },
    series: Array.from({ length: pointCount }, (_, index) => ({
      period_start: 1_767_225_600 + index * 86_400,
      period: new Date((1_767_225_600 + index * 86_400) * 1000).toISOString().slice(0, 10),
      request_count: 20 + Math.round(Math.sin(index / 3) * 15),
    })),
    dimensions: { models: [], api_keys: [], credentials: [], outcomes: [] },
    breakdowns: { models: [], api_keys: [], credentials: [] },
    data_quality: {
      usage_coverage: 1,
      dropped_events: 0,
      detail_retention_days: 90,
      boundary_precision: 'exact',
    },
  };
}

async function openStats(page: Page, pointCount = 90): Promise<void> {
  await page.route('**/auth/session', (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        username: 'test-user',
        source: 'session_cookie',
        password_change_required: false,
      },
    }),
  );
  await page.route('**/api/admin/stats/overview?*', (route) =>
    route.fulfill({ json: overview(pointCount) }),
  );
  await page.route('**/api/admin/stats/requests?*', (route) =>
    route.fulfill({
      json: {
        items: [],
        page: 1,
        page_size: 20,
        total: 0,
        total_pages: 0,
        snapshot_id: 0,
        snapshot_time: Date.now() / 1000,
      },
    }),
  );
  await page.goto(`${APP_URL}/#/stats`);
  await expect(page.locator('.stats-trend-scroll')).toBeVisible();
  await page.locator('.stats-trend-scroll').scrollIntoViewIfNeeded();
}

test('桌面趋势提示支持键盘连续滚动、边界焦点交接和节点详情', async ({ page }) => {
  await openStats(page);
  const viewport = page.locator('.stats-trend-scroll');
  const left = page.getByRole('button', { name: '向左滚动趋势图' });
  const right = page.getByRole('button', { name: '向右滚动趋势图' });
  await expect(left).toBeHidden();
  await expect(right).toBeVisible();
  await expect(viewport).toHaveCSS('scroll-behavior', 'smooth');
  await expect(right).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(right).toHaveCSS('border-top-width', '0px');
  await expect(right).toHaveCSS('box-shadow', 'none');
  await expect(right.locator('svg')).toHaveAttribute('stroke-width', '1.75');
  await right.hover();
  await expect(right).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await page.mouse.move(0, 0);
  await expect(right).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');

  await right.focus();
  await expect(right).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Enter');
  await expect(left).toBeVisible();
  await expect
    .poll(() => viewport.evaluate((element) => element.scrollLeft / element.clientWidth))
    .toBeCloseTo(0.8, 2);
  await expect(right).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(right).toBeHidden();
  await expect(viewport).toBeFocused();

  const lastPoint = page.locator('.stats-trend-point-trigger').last();
  await lastPoint.click();
  await expect(page.locator('.c-tooltip-popover')).toContainText('2026/03/31');
  await lastPoint.click();

  await left.focus();
  const returnPosition = await viewport.evaluate(
    (element) => element.scrollLeft - element.clientWidth * 0.8,
  );
  await page.keyboard.press('Space');
  await expect(right).toBeVisible();
  await expect
    .poll(() => viewport.evaluate((element) => element.scrollLeft))
    .toBeCloseTo(returnPosition, 0);
  await page.keyboard.press('Space');
  await expect(left).toBeHidden();
  await expect(viewport).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(left).toBeVisible();
});

test('窗口变化和数据刷新及时更新提示，减少动态效果时直接滚动', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openStats(page, 20);
  const viewport = page.locator('.stats-trend-scroll');
  const right = page.getByRole('button', { name: '向右滚动趋势图' });
  await expect(right).toBeVisible();
  await expect(viewport).toHaveCSS('scroll-behavior', 'auto');

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(right).toBeHidden();
  await expect(viewport).toHaveAttribute('tabindex', '-1');
  await page.setViewportSize({ width: 320, height: 740 });
  await expect(right).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);

  await page.route('**/api/admin/stats/overview?*', (route) =>
    route.fulfill({ json: overview(2) }),
  );
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  // 320px 窄屏仍保留图表自身的最小宽度，放宽窗口后应完全收起提示。
  await page.setViewportSize({ width: 430, height: 932 });
  await expect(page.locator('.stats-trend-point-trigger')).toHaveCount(2);
  await expect(right).toBeHidden();
});

test.describe('手机趋势交互', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test('暗色模式保持触摸目标、时间标签和原生横向滑动', async ({ page, context }) => {
    await page.addInitScript(() => localStorage.setItem('admin-theme', 'dark'));
    await openStats(page);
    const viewport = page.locator('.stats-trend-scroll');
    const right = page.getByRole('button', { name: '向右滚动趋势图' });
    const left = page.getByRole('button', { name: '向左滚动趋势图' });
    await expect(right).toBeVisible();
    const target = (await right.boundingBox())!;
    expect(target.width).toBeGreaterThanOrEqual(44);
    expect(target.height).toBeGreaterThanOrEqual(44);
    const edge = page.locator('.stats-trend-edge-right');
    await expect(edge).toHaveCSS('pointer-events', 'none');
    const edgeBox = (await edge.boundingBox())!;
    const axisBox = (await page.locator('.stats-trend-axis').boundingBox())!;
    expect(edgeBox.y + edgeBox.height).toBeLessThanOrEqual(axisBox.y);

    // 从渐变覆盖区发起真实触摸手势，验证其不会吞掉图表原生滚动。
    const viewportBox = (await viewport.boundingBox())!;
    const session = await context.newCDPSession(page);
    const scrollFinished = viewport.evaluate(
      (element) =>
        new Promise<void>((resolve) =>
          element.addEventListener('scrollend', () => resolve(), { once: true }),
        ),
    );
    const startX = viewportBox.x + viewportBox.width - 10;
    const y = viewportBox.y + 55;
    await session.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x: startX, y }],
    });
    for (let distance = 20; distance <= 180; distance += 20) {
      await session.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: startX - distance, y }],
      });
    }
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await session.detach();
    await expect(left).toBeVisible();
    await expect(right).toBeVisible();
    await expect.poll(() => viewport.evaluate((element) => element.scrollLeft)).toBeGreaterThan(50);
    await scrollFinished;

    await viewport.evaluate((element) => element.scrollTo({ left: 0, behavior: 'instant' }));
    await expect(left).toBeHidden();
    await page.locator('.stats-trend-point-trigger').first().tap();
    await expect(page.locator('.c-tooltip-popover')).toContainText('2026/01/01');
    await page.locator('.stats-trend-point-trigger').first().tap();
    await expect(page.locator('.c-tooltip-popover')).toBeHidden();
    await right.tap();
    await expect(left).toBeVisible();
    await expect(right).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  });
});
