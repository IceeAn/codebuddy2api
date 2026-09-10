import { expect, test, type APIRequestContext } from '@playwright/test';

const APP_URL = 'http://127.0.0.1:4175';

interface AppState {
  mode: string;
  settingsRequests: number;
  adminRequests: number;
  passwordChanges: Array<Record<string, unknown>>;
}

async function reset(request: APIRequestContext, mode: 'bootstrap' | 'formal'): Promise<void> {
  const response = await request.post(`${APP_URL}/__app_control/reset?mode=${mode}`);
  expect(response.ok()).toBe(true);
}

async function state(request: APIRequestContext): Promise<AppState> {
  const response = await request.get(`${APP_URL}/__app_control/state`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<AppState>;
}

test.describe('账号初始化与自身改密', () => {
  test('初始账号登录后被强制改密，成功后返回预填用户名的登录页', async ({ page, request }) => {
    await reset(request, 'bootstrap');
    await page.goto(APP_URL);

    await expect(page).toHaveTitle('登录 · CodeBuddy2API');
    await expect(page.getByText('请使用初始账号密码登录')).toBeVisible();
    await page.getByLabel('用户名').fill('admin');
    await page.getByRole('textbox', { name: '密码*', exact: true }).fill('admin');
    await page.getByRole('button', { name: '登录', exact: true }).click();

    await expect(page).toHaveTitle('修改密码 · CodeBuddy2API');
    await expect(page.getByRole('heading', { name: '首次登录后必须修改密码' })).toBeVisible();
    await expect(page.getByLabel('当前密码')).toHaveCount(0);
    await page.getByLabel('新密码', { exact: true }).fill('new-password');
    await page.getByLabel('确认新密码').fill('new-password');
    await page.getByRole('button', { name: '修改密码并重新登录' }).click();

    await expect(page).toHaveTitle('登录 · CodeBuddy2API');
    await expect(page.getByText('密码已修改，请使用新密码重新登录')).toBeVisible();
    await expect(page.getByLabel('用户名')).toHaveValue('admin');
    expect(await state(request)).toMatchObject({
      mode: 'logged_out',
      adminRequests: 0,
      passwordChanges: [{ new_password: 'new-password' }],
    });
  });

  test('账号安全标签可直达且不会预取服务配置，脏表单保护标签切换', async ({ page, request }) => {
    await reset(request, 'formal');
    await page.goto(`${APP_URL}/#/settings?tab=account`);

    await expect(page.locator('[data-password-change-form]')).toBeVisible();
    await expect(page.getByRole('tab', { name: '账号安全' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect((await state(request)).settingsRequests).toBe(0);

    await page.getByLabel('当前密码').fill('formal-password');
    const dismissedDialogPromise = page.waitForEvent('dialog');
    const dismissedClickPromise = page.getByRole('tab', { name: '服务配置' }).click();
    const dismissedDialog = await dismissedDialogPromise;
    expect(dismissedDialog.message()).toContain('未提交内容');
    await dismissedDialog.dismiss();
    await dismissedClickPromise;
    await expect(page).toHaveURL(/tab=account/);

    const acceptedDialogPromise = page.waitForEvent('dialog');
    const acceptedClickPromise = page.getByRole('tab', { name: '服务配置' }).click();
    const acceptedDialog = await acceptedDialogPromise;
    await acceptedDialog.accept();
    await acceptedClickPromise;

    await expect(page).toHaveURL(/tab=service/);
    await expect.poll(async () => (await state(request)).settingsRequests).toBe(1);
  });
});
