import { expect, test } from '@playwright/test';

const APP_URL = 'http://127.0.0.1:4175';

test('Responses 测试台发送无状态请求并识别流式完成和不完整响应', async ({ page, request }) => {
  await request.post(`${APP_URL}/__app_control/reset?mode=formal`);
  await page.route('**/api/admin/playground/openai/v1/models', (route) =>
    route.fulfill({ json: { object: 'list', data: [{ id: 'kimi-k3-1', object: 'model' }] } }),
  );
  const bodies: Array<Record<string, unknown>> = [];
  await page.route('**/api/admin/playground/openai/v1/responses', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    bodies.push(body);
    if (body.stream) {
      await route.fulfill({
        contentType: 'text/event-stream',
        body: 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"浏览器验证"}\n\nevent: response.completed\ndata: {"type":"response.completed"}\n\n',
      });
    } else {
      await route.fulfill({
        json: {
          status: 'incomplete',
          output: [],
          incomplete_details: { reason: 'max_output_tokens' },
        },
      });
    }
  });
  await page.goto(`${APP_URL}/#/console`);
  await page.getByRole('radio', { name: 'OpenAI Responses', exact: true }).click();
  await page.getByPlaceholder('消息').fill('浏览器请求');
  await page.getByText('流式响应', { exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('pre')).toContainText('浏览器验证');
  await expect(page.getByText('流式请求完成', { exact: true })).toBeVisible();
  expect(bodies[0]).toMatchObject({ input: '浏览器请求', store: false, stream: true });
  await page.getByText('流式响应', { exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('pre')).toContainText('max_output_tokens');
  await expect(page.getByText('Responses 响应未完整完成', { exact: true })).toBeVisible();
});

test('服务配置可编辑并保存 Codex 自动审批模型', async ({ page, request }) => {
  await request.post(`${APP_URL}/__app_control/reset?mode=formal`);
  const key = 'CODEBUDDY_CODEX_AUTO_REVIEW_MODEL';
  let selected = 'deepseek-v4-flash';
  await page.route('**/api/admin/settings', async (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { settings: Record<string, string> };
      selected = body.settings[key]!;
    }
    await route.fulfill({
      json: {
        settings: { [key]: selected },
        fields: [{ key, label: 'Codex 自动审批模型', type: 'text' }],
      },
    });
  });
  await page.goto(`${APP_URL}/#/settings?tab=service`);
  const input = page.getByRole('textbox');
  await expect(input).toHaveValue('deepseek-v4-flash');
  await input.fill('kimi-k3-1');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByText('设置已保存', { exact: true })).toBeVisible();
  expect(selected).toBe('kimi-k3-1');
  await page.reload();
  await expect(page.getByRole('textbox')).toHaveValue('kimi-k3-1');
});
