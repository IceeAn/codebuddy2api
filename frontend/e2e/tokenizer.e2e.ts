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
test('Tokenizer 设置与计数入口支持键盘输入、脏编辑保护及两种协议', async ({ page, request }) => {
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
  await page.route('**/api/admin/playground/tokenizer/v1/count_tokens', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ model: 'glm-5.2', text: '你好' });
    await route.fulfill({
      json: { input_tokens: 1 },
      headers: {
        'X-Tokenizer-Method': 'text',
        'X-Tokenizer-Source': 'builtin',
        'X-Tokenizer-Revision': 'abc',
      },
    });
  });
  await page.getByRole('button', { name: '计数', exact: true }).click();
  await expect(page.locator('pre')).toContainText('"input_tokens": 1');
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
