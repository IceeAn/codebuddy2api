import { expect, test } from '@playwright/test';

const APP_URL = 'http://127.0.0.1:4175';

test.beforeEach(async ({ request }) => {
  await request.post(`${APP_URL}/__app_control/reset?mode=formal`);
});

test('站内阅读、章节直达、历史导航及附件下载与发布版本一致', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://**/*', (route) => route.abort());
  await page.goto(`${APP_URL}/#/usage-docs`);
  await expect(page).toHaveTitle('使用文档 · CodeBuddy2API');
  await expect(page.locator('article h1')).toContainText('CodeBuddy2API');
  await expect(page.locator('article img')).toBeVisible();
  expect(
    await page.locator('article img').evaluate((image: HTMLImageElement) => image.naturalWidth),
  ).toBeGreaterThan(0);
  const article = await page.locator('article').elementHandle();
  await page.locator('article').getByRole('link', { name: 'Codex 接入', exact: true }).click();
  await expect(page.locator('article h1')).toHaveText('Codex CLI 接入');
  expect(await article!.evaluate((element) => element.isConnected)).toBe(true);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('article').getByRole('link', { name: 'Codex模型目录示例.json' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('Codex模型目录示例.json');
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(JSON.parse(Buffer.concat(chunks).toString())).toHaveProperty('models');
  await page
    .getByRole('navigation', { name: '章节目录' })
    .getByRole('link', { name: '验证与限制', exact: true })
    .click();
  await expect(
    page.locator('article').getByRole('heading', { name: '验证与限制', exact: true }),
  ).toBeInViewport();
  const sectionUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(sectionUrl);
  await expect(
    page.locator('article').getByRole('heading', { name: '验证与限制', exact: true }),
  ).toBeInViewport();
  await page.goBack();
  await expect(page.locator('article h1')).toHaveText('Codex CLI 接入');
  await page.goBack();
  await expect(page.locator('article h1')).toContainText('CodeBuddy2API');
  await page.goForward();
  await expect(page.locator('article h1')).toHaveText('Codex CLI 接入');
  expect(errors).toEqual([]);
});

test('移动端折叠目录、主题切换、显式锚点与无效文档提示', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${APP_URL}/#/usage-docs?doc=README.md#use-update-script-with-release-file`);
  await expect(page.locator('#use-update-script-with-release-file')).toBeInViewport();
  await expect(page.getByRole('navigation', { name: '文档导航' })).not.toBeVisible();
  await page.getByRole('button', { name: '文档与章节' }).click();
  await expect(page.getByRole('navigation', { name: '文档导航' })).toBeVisible();
  await page
    .getByRole('navigation', { name: '文档导航' })
    .getByRole('link', { name: 'Codex CLI 接入' })
    .click();
  await expect(page.getByRole('navigation', { name: '文档导航' })).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  // 使用管理台已有的主题按钮标识。
  const theme = page.getByRole('button', { name: /切换.*模式/ });
  await expect(theme).toBeEnabled();
  await theme.click();
  await expect(page.locator('article h1')).toBeVisible();
  await page.goto(`${APP_URL}/#/usage-docs?doc=missing.md`);
  await expect(page.getByText('文档不存在', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '返回文档首页' }).click();
  await expect(page.locator('article h1')).toContainText('CodeBuddy2API');
});

test('登录和首次改密流程仍保护使用文档入口', async ({ page, request }) => {
  await request.post(`${APP_URL}/__app_control/reset?mode=bootstrap`);
  await page.goto(`${APP_URL}/#/usage-docs`);
  await expect(page).toHaveTitle('登录 · CodeBuddy2API');
  await expect(page.locator('article')).toHaveCount(0);
  await page.getByLabel('用户名').fill('admin');
  await page.getByRole('textbox', { name: '密码*', exact: true }).fill('admin');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '首次登录后必须修改密码' })).toBeVisible();
  await expect(page.locator('article')).toHaveCount(0);
});
