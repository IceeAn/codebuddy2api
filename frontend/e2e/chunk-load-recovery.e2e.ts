import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

interface FixtureState {
  phase: 'a' | 'b';
  targetMissing: boolean;
  documentDelayMs: number;
  documentRequests: number;
}

async function state(request: APIRequestContext): Promise<FixtureState> {
  const response = await request.get('/__control/state');
  expect(response.ok()).toBe(true);
  return response.json() as Promise<FixtureState>;
}

async function reset(request: APIRequestContext, page: Page, route = '/source'): Promise<void> {
  await page.goto('about:blank');
  const response = await request.post('/__control/reset');
  expect(response.ok()).toBe(true);
  await page.goto(`/#${route}`);
}

async function switchToB(
  request: APIRequestContext,
  targetMissing = false,
  documentDelayMs = 0,
): Promise<void> {
  const response = await request.post(
    `/__control/switch?missing=${targetMissing ? '1' : '0'}&document-delay-ms=${documentDelayMs}`,
  );
  expect(response.ok()).toBe(true);
}

async function forceFallbackReload(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'navigation', { configurable: true, value: undefined });
    const nativeStop = window.stop.bind(window);
    window.stop = () => {
      sessionStorage.setItem('e2e:fallback-reload-stopped', 'true');
      nativeStop();
    };
  });
}

async function observePageStop(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const nativeStop = window.stop.bind(window);
    window.stop = () => {
      sessionStorage.setItem('e2e:navigation-api-reload-stopped', 'true');
      nativeStop();
    };
  });
}

test.describe('生产构建的 chunk 恢复', () => {
  test('控制端拒绝超出上限的文档延迟', async ({ request }) => {
    const resetResponse = await request.post('/__control/reset');
    expect(resetResponse.ok()).toBe(true);

    const response = await request.post('/__control/switch?missing=0&document-delay-ms=2001');

    expect(response.status()).toBe(400);
    expect(await response.json()).toEqual({
      error: 'document-delay-ms 必须是 0 到 2000 的安全整数',
    });
    expect(await state(request)).toEqual({
      phase: 'a',
      targetMissing: false,
      documentDelayMs: 0,
      documentRequests: 0,
    });
  });

  test('push 失败只刷新一次、续接 B 版本目标并保留来源历史', async ({ page, request }) => {
    await reset(request, page);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await switchToB(request);

    await page.getByTestId('push-target').click();
    await expect(page.getByTestId('target-version')).toHaveText('目标页面 B');
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);

    await page.goBack();
    await expect(page.getByRole('heading', { name: '来源页面 B' })).toBeVisible();
  });

  test('路由模块初始化异常保留原错误且不触发 chunk 恢复', async ({ page, request }) => {
    await reset(request, page);
    await page.evaluate(() => sessionStorage.removeItem('e2e:unexpected-navigation-error'));

    await page.getByTestId('push-broken').click();

    await expect
      .poll(() => page.evaluate(() => sessionStorage.getItem('e2e:unexpected-navigation-error')))
      .toBe('ReferenceError: E2E_MODULE_INITIALIZATION_ERROR');
    await page.waitForTimeout(1_100);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    expect(
      await page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
    ).toBeNull();
    expect((await state(request)).documentRequests).toBe(1);
  });

  test('入口 HTML 响应缓慢时持续等待真实刷新，不误报为已取消', async ({ page, request }) => {
    await reset(request, page);
    await switchToB(request, false, 2_000);
    await page.evaluate(() => {
      sessionStorage.setItem('e2e:slow-reload-showed-failure', 'false');
      new MutationObserver(() => {
        if (document.querySelector('[role="alertdialog"]') !== null) {
          sessionStorage.setItem('e2e:slow-reload-showed-failure', 'true');
        }
      }).observe(document.body, { childList: true, subtree: true });
    });

    await page.getByTestId('push-target').click({ noWaitAfter: true });
    await expect(page.getByTestId('target-version')).toHaveText('目标页面 B');
    expect(
      await page.evaluate(() => sessionStorage.getItem('e2e:slow-reload-showed-failure')),
    ).toBe('false');
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);
  });

  test('无 Navigation API 时不会主动停止响应缓慢的正常刷新', async ({ page, request }) => {
    await forceFallbackReload(page);
    await reset(request, page);
    await page.evaluate(() => sessionStorage.setItem('e2e:fallback-reload-stopped', 'false'));
    await switchToB(request, false, 2_000);

    await page.getByTestId('push-target').click({ noWaitAfter: true });
    await expect(page.getByTestId('target-version')).toHaveText('目标页面 B');

    expect(await page.evaluate(() => sessionStorage.getItem('e2e:fallback-reload-stopped'))).toBe(
      'false',
    );
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);
  });

  test('无 Navigation API 时定时器前成功的更新导航会终止旧刷新', async ({ page, request }) => {
    await forceFallbackReload(page);
    await reset(request, page);
    await expect(page.getByTestId('update-source')).toBeVisible();
    await expect(page.getByTestId('push-target')).toBeVisible();
    await page.evaluate(() => {
      sessionStorage.setItem('e2e:fallback-reload-stopped', 'false');
      const updateButton = document.querySelector<HTMLButtonElement>(
        '[data-testid="update-source"]',
      );
      const targetButton = document.querySelector<HTMLButtonElement>('[data-testid="push-target"]');
      if (updateButton === null || targetButton === null) throw new Error('未找到导航按钮');
      targetButton.addEventListener(
        'click',
        () => window.setTimeout(() => updateButton.click(), 100),
        { once: true },
      );
    });
    await switchToB(request, false, 2_000);

    await page.getByTestId('push-target').click({ noWaitAfter: true });

    await expect(page).toHaveURL(/#\/source\?updated=1$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    expect(await page.evaluate(() => sessionStorage.getItem('e2e:fallback-reload-stopped'))).toBe(
      'true',
    );
    expect(
      await page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
    ).toBeNull();

    await page.waitForTimeout(2_100);
    await expect(page).toHaveURL(/#\/source\?updated=1$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);
  });

  test('Navigation API 刷新等待期间成功的更新导航会终止旧刷新', async ({ page, request }) => {
    await observePageStop(page);
    await reset(request, page);
    await expect(page.getByTestId('update-source')).toBeVisible();
    await expect(page.getByTestId('push-target')).toBeVisible();
    expect(
      await page.evaluate(
        () => window.navigation !== undefined && window.navigation.currentEntry !== null,
      ),
    ).toBe(true);
    await page.evaluate(() => {
      sessionStorage.setItem('e2e:navigation-api-reload-stopped', 'false');
      const updateButton = document.querySelector<HTMLButtonElement>(
        '[data-testid="update-source"]',
      );
      const targetButton = document.querySelector<HTMLButtonElement>('[data-testid="push-target"]');
      if (updateButton === null || targetButton === null) throw new Error('未找到导航按钮');
      targetButton.addEventListener(
        'click',
        () => window.setTimeout(() => updateButton.click(), 100),
        { once: true },
      );
    });
    await switchToB(request, false, 2_000);

    await page.getByTestId('push-target').click({ noWaitAfter: true });

    await expect(page).toHaveURL(/#\/source\?updated=1$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    expect(
      await page.evaluate(() => sessionStorage.getItem('e2e:navigation-api-reload-stopped')),
    ).toBe('true');
    expect(
      await page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
    ).toBeNull();

    await page.waitForTimeout(2_100);
    await expect(page).toHaveURL(/#\/source\?updated=1$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);
  });

  test('延迟租约暂停已启动刷新，重复导航消费记录后不再重启', async ({ page, request }) => {
    await observePageStop(page);
    await reset(request, page);
    await expect(page.getByTestId('begin-critical-operation')).toBeVisible();
    await expect(page.getByTestId('finish-critical-operation')).toBeVisible();
    expect(
      await page.evaluate(
        () => window.navigation !== undefined && window.navigation.currentEntry !== null,
      ),
    ).toBe(true);
    await page.evaluate(() => {
      sessionStorage.setItem('e2e:navigation-api-reload-stopped', 'false');
      const beginButton = document.querySelector<HTMLButtonElement>(
        '[data-testid="begin-critical-operation"]',
      );
      const targetButton = document.querySelector<HTMLButtonElement>('[data-testid="push-target"]');
      if (beginButton === null || targetButton === null) throw new Error('未找到临界操作按钮');
      targetButton.addEventListener(
        'click',
        () => window.setTimeout(() => beginButton.click(), 100),
        { once: true },
      );
    });
    await switchToB(request, false, 2_000);

    await page.getByTestId('push-target').click({ noWaitAfter: true });

    await expect
      .poll(() => page.evaluate(() => sessionStorage.getItem('e2e:navigation-api-reload-stopped')))
      .toBe('true');
    await expect(page).toHaveURL(/#\/source$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();

    await page.getByTestId('finish-critical-operation').click();

    expect(
      await page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
    ).toBeNull();
    await page.waitForTimeout(2_100);
    await expect(page).toHaveURL(/#\/source$/);
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect.poll(async () => (await state(request)).documentRequests).toBe(2);
  });

  test('无 Navigation API 时取消慢刷新后的重试沿用后发 chunk 目标', async ({ page, request }) => {
    await forceFallbackReload(page);
    await reset(request, page);
    await expect(page.getByTestId('push-before')).toBeVisible();
    await expect(page.getByTestId('push-target')).toBeVisible();
    await page.evaluate(() => {
      sessionStorage.setItem('e2e:fallback-reload-stopped', 'false');
      const beforeButton = document.querySelector<HTMLButtonElement>('[data-testid="push-before"]');
      const targetButton = document.querySelector<HTMLButtonElement>('[data-testid="push-target"]');
      if (beforeButton === null || targetButton === null) throw new Error('未找到导航按钮');
      targetButton.addEventListener(
        'click',
        () => {
          window.setTimeout(() => beforeButton.click(), 100);
          window.setTimeout(() => window.stop(), 250);
        },
        { once: true },
      );
    });
    await switchToB(request, false, 2_000);

    await page.getByTestId('push-target').click({ noWaitAfter: true });

    const recoveryDialog = page.getByRole('alertdialog');
    await expect(recoveryDialog).toBeVisible();
    await recoveryDialog.getByRole('button', { name: '重新加载' }).click();
    await expect(page).toHaveURL(/#\/before$/);
    await expect(page.getByRole('heading', { name: '前置页面 B' })).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('e2e:fallback-reload-stopped'))).toBe(
      'true',
    );
    expect(
      await page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
    ).toBeNull();
    await expect.poll(async () => (await state(request)).documentRequests).toBe(3);
  });

  test('无 Navigation API 时只有明确留页才停止已取消的回退刷新', async ({ page, request }) => {
    await forceFallbackReload(page);
    await reset(request, page);
    await page.getByTestId('draft').fill('尚未保存的草稿');
    await page.getByTestId('protect-draft').check();
    await page.evaluate(() => sessionStorage.setItem('e2e:fallback-reload-stopped', 'false'));
    await switchToB(request);
    let beforeUnloadDialogs = 0;
    const dismissed = new Promise<void>((resolvePromise) => {
      page.once('dialog', (dialog) => {
        beforeUnloadDialogs += 1;
        void dialog.dismiss().then(resolvePromise);
      });
    });

    await page.getByTestId('push-target').click();
    await dismissed;
    const recoveryDialog = page.getByRole('alertdialog');
    await expect(recoveryDialog).toBeVisible();
    await recoveryDialog.getByRole('button', { name: '留在当前页' }).click();

    await expect(page.getByTestId('draft')).toHaveValue('尚未保存的草稿');
    expect(await page.evaluate(() => sessionStorage.getItem('e2e:fallback-reload-stopped'))).toBe(
      'true',
    );
    expect((await state(request)).documentRequests).toBe(1);

    await page.evaluate(() => sessionStorage.setItem('e2e:fallback-reload-stopped', 'false'));
    await page.getByTestId('push-target').click();
    await expect(recoveryDialog).toBeVisible();
    await page.waitForTimeout(1_200);
    expect(await page.evaluate(() => sessionStorage.getItem('e2e:fallback-reload-stopped'))).toBe(
      'false',
    );
    expect((await state(request)).documentRequests).toBe(1);
    expect(beforeUnloadDialogs).toBe(1);
  });

  test('replace 续接替换来源历史项', async ({ page, request }) => {
    await reset(request, page, '/before');
    await page.getByTestId('open-source').click();
    await expect(page.getByRole('heading', { name: '来源页面 A' })).toBeVisible();
    await switchToB(request);

    await page.getByTestId('replace-target').click();
    await expect(page.getByTestId('target-version')).toHaveText('目标页面 B');

    await page.goBack();
    await expect(page.getByRole('heading', { name: '前置页面 B' })).toBeVisible();
  });

  test('取消刷新后可保留输入，留在当前页后不再自动刷新', async ({ page, request }) => {
    await reset(request, page);
    await page.getByTestId('draft').fill('尚未保存的草稿');
    await page.getByTestId('protect-draft').check();
    await switchToB(request);
    let beforeUnloadDialogs = 0;
    const dismissed = new Promise<void>((resolvePromise) => {
      page.once('dialog', (dialog) => {
        beforeUnloadDialogs += 1;
        void dialog.dismiss().then(resolvePromise);
      });
    });

    await page.getByTestId('push-target').click();
    await dismissed;
    const recoveryDialog = page.getByRole('alertdialog');
    await expect(recoveryDialog).toBeVisible();
    await recoveryDialog.getByRole('button', { name: '留在当前页' }).click();
    await expect(page.getByTestId('draft')).toHaveValue('尚未保存的草稿');
    expect((await state(request)).documentRequests).toBe(1);

    await page.getByTestId('push-target').click();
    await expect(recoveryDialog).toBeVisible();
    await page.waitForTimeout(1_200);
    expect((await state(request)).documentRequests).toBe(1);
    expect(beforeUnloadDialogs).toBe(1);
  });

  test('取消刷新后浏览器历史导航会清理旧恢复状态', async ({ page, request }) => {
    await reset(request, page, '/before');
    await page.getByTestId('open-source').click();
    await page.getByTestId('protect-draft').check();
    await switchToB(request);
    const dismissed = new Promise<void>((resolvePromise) => {
      page.once('dialog', (dialog) => {
        void dialog.dismiss().then(resolvePromise);
      });
    });

    await page.getByTestId('push-target').click();
    await dismissed;
    const recoveryDialog = page.getByRole('alertdialog');
    await expect(recoveryDialog).toBeVisible();

    await page.goBack();

    await expect(recoveryDialog).toBeHidden();
    await expect(page.getByRole('heading', { name: '前置页面 A' })).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => sessionStorage.getItem('codebuddy2api:chunk-reload-attempted')),
      )
      .toBeNull();
    expect((await state(request)).documentRequests).toBe(1);
  });

  test('纯 hash 着陆的新版 chunk 持续缺失时只自动刷新一次', async ({ page, request }) => {
    await page.goto('about:blank');
    const resetResponse = await request.post('/__control/reset');
    expect(resetResponse.ok()).toBe(true);
    await switchToB(request, true);

    await page.goto('/#/target');

    const recoveryDialog = page.getByRole('alertdialog');
    await expect(recoveryDialog).toBeVisible();
    await expect(recoveryDialog.getByRole('button', { name: '留在当前页' })).toHaveCount(0);
    await page.waitForTimeout(1_200);
    expect(await state(request)).toMatchObject({
      phase: 'b',
      targetMissing: true,
      documentRequests: 2,
    });
  });

  test('B 版本目标仍缺失时显示手动恢复且不形成刷新循环', async ({ page, request }) => {
    await reset(request, page);
    await switchToB(request, true);

    await page.getByTestId('push-target').click();
    await expect(page.getByRole('alertdialog')).toBeVisible();
    await expect(page.getByRole('button', { name: '留在当前页' })).toBeVisible();
    await page.waitForTimeout(1_200);

    const currentState = await state(request);
    expect(currentState).toMatchObject({
      phase: 'b',
      targetMissing: true,
      documentRequests: 2,
    });
  });
});
