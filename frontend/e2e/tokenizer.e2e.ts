import { expect, test } from '@playwright/test';

const APP_URL = 'http://127.0.0.1:4175';
const resources = {
  builtin_resources: [
    {
      id: 'official',
      model: 'glm-5.2',
      repo: 'zai-org/GLM-5.2',
      revision: 'abc',
      license: 'MIT',
      encoder: 'jinja',
    },
  ],
  pending_models: ['glm-5.0'],
  user_resources: [],
  mappings: {},
  encoders: ['auto', 'jinja'],
  limits: {
    upload_max_bytes: 67108864,
    timeout_seconds: 30,
    queue_timeout_seconds: 30,
    max_mappings: 512,
  },
};
test('Tokenizer 设置与计数入口支持键盘输入、脏编辑保护及两种协议', async ({
  page,
  request,
}, testInfo) => {
  await request.post(`${APP_URL}/__app_control/reset?mode=formal`);
  await page.route('**/api/admin/tokenizers', (route) => route.fulfill({ json: resources }));
  await page.goto(`${APP_URL}/#/settings?tab=tokenizer`);
  await expect(page.getByRole('tab', { name: 'Tokenizer' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByText('待配置（尚无已确认的官方匹配资源）：glm-5.0')).toBeVisible();
  await page.getByLabel('资源名称').fill('尚未上传');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('tab', { name: '服务配置' }).click();
  await expect(page).toHaveURL(/tab=tokenizer/);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('tab', { name: '服务配置' }).click();
  await expect(page).toHaveURL(/tab=service/);
  await page.goto(`${APP_URL}/#/console`);
  await page.getByRole('button', { name: 'Tokenizer', exact: true }).click();
  await page.getByLabel('模型 ID（可手动输入）').fill('glm-5.2');
  await page.getByLabel('文本', { exact: true }).fill('你好');
  await page.route('**/api/admin/tokenizers/encode', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ model: 'glm-5.2', text: '你好' });
    await route.fulfill({
      json: {
        text: '你好',
        input_tokens: 3,
        tokens: [
          { id: 256, start: 0, end: 2 },
          { id: 160, start: 2, end: 3 },
          { id: 100, start: 3, end: 6 },
        ],
      },
      headers: {
        'X-Tokenizer-Method': 'text',
        'X-Tokenizer-Source': 'builtin',
        'X-Tokenizer-Revision': 'abc',
      },
    });
  });
  await page.getByRole('button', { name: '计数', exact: true }).click();
  const visualization = page.getByTestId('token-text');
  await expect(visualization).toHaveText('你好');
  await expect(page.locator('pre')).toHaveCount(0);
  const character = visualization.locator('span').first();
  await expect(character).toHaveText('你');
  expect(
    await character.evaluate((element) => getComputedStyle(element).backgroundImage),
  ).toContain('66.6667%');
  await expect(character).not.toHaveAttribute('title');
  await expect(character).toHaveCSS('display', 'inline');
  await character.hover();
  await expect(page.getByRole('tooltip')).toHaveText(
    'Token 1 · ID 256（2 字节/3 字节）\nToken 2 · ID 160（1 字节/3 字节）',
  );
  await expect(page.getByRole('tooltip').locator('span')).toHaveCSS('white-space', 'pre-line');
  expect(await page.getByRole('tooltip').innerText()).toBe(
    'Token 1 · ID 256（2 字节/3 字节）\nToken 2 · ID 160（1 字节/3 字节）',
  );
  await page.getByRole('button', { name: '显示 Token ID' }).hover();
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('分词可视化.png'), fullPage: true });
  await page.getByRole('button', { name: '显示 Token ID' }).click();
  await expect(page.getByTestId('token-ids')).toHaveText('256160100');
  await page.getByRole('button', { name: '显示文字', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(visualization).toBeVisible();
  await expect
    .poll(async () =>
      page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    )
    .toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByText('Anthropic Messages', { exact: true }).click();
  await page
    .getByLabel('完整 Anthropic 请求 JSON')
    .fill('{"messages":[{"role":"user","content":"你好"}]}');
  await page.route('**/api/admin/playground/anthropic/v1/messages/count_tokens', async (route) => {
    expect(route.request().postDataJSON().model).toBe('glm-5.2');
    await route.fulfill({
      json: { input_tokens: 12 },
      headers: { 'X-Tokenizer-Method': 'template' },
    });
  });
  await page.getByRole('button', { name: '计数', exact: true }).click();
  await expect(page.locator('pre')).toContainText('"input_tokens": 12');
});
