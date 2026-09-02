const { test, expect } = require('@playwright/test');

const sandboxUrl = process.env.INFOMANCER_E2E_SANDBOX_URL || 'http://127.0.0.1:8788';
const acceptancePassword = 'acceptance-password-123';

async function signIn(page) {
  await page.goto(`${sandboxUrl}/login`);
  await page.getByRole('textbox', { name: 'Username or email' }).fill('acceptance-librarian');
  await page.locator('input[type="password"]').fill(acceptancePassword);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/login'),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ]);
}

test('Collections card uses the Library cover hover action contract', async ({ page }) => {
  await signIn(page);
  await page.goto(`${sandboxUrl}/collections`);

  const name = 'Acceptance Hover Collection';
  const existing = page.locator('.collection-picker-card', { hasText: name });
  if (!(await existing.count())) {
    await page.locator('.collection-create-launcher > button').click();
    const dialog = page.locator('dialog.collection-create-dialog');
    await expect(dialog).toBeVisible();
    await dialog.locator('form.collection-create input[name="name"]').fill(name);
    await Promise.all([
      page.waitForURL((url) => /^\/collections\/\d+$/.test(url.pathname)),
      dialog.locator('form.collection-create button.primary').click(),
    ]);
    await page.goto(`${sandboxUrl}/collections`);
  }

  const card = page.locator('.collection-picker-card', { hasText: name }).first();
  const actions = card.locator('.collection-picker-card-actions');
  const menu = card.locator('.collection-picker-menu');
  const trigger = menu.locator('> summary');

  await expect(card).toBeVisible();
  await expect(card).toHaveClass(/\bcover-card\b/);
  await expect(card.locator('.collection-picker-card-link')).toHaveClass(/\bcover-card-link\b/);
  await expect(actions).toHaveClass(/\bcover-card-actions\b/);
  await expect(menu).toHaveClass(/\bcover-row-menu\b/);
  await expect(card).not.toHaveClass(/library-hover-match/);
  await expect(actions).toBeHidden();

  await card.hover();
  await expect(actions).toBeVisible();
  await expect(trigger).toBeVisible();

  await page.mouse.move(0, 0);
  await expect(actions).toBeHidden();

  await card.hover();
  await expect(actions).toBeVisible();
  await trigger.click();
  await expect(menu).toHaveAttribute('open', '');
  await expect(card.getByRole('link', { name: 'Edit collection' })).toBeVisible();
});
