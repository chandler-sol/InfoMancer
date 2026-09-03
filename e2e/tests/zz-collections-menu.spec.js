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

async function transformY(locator) {
  return locator.evaluate((element) => {
    const transform = getComputedStyle(element).transform;
    if (!transform || transform === 'none') return 0;
    return new DOMMatrixReadOnly(transform).m42;
  });
}

async function hoverFrameState(locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element, '::before');
    const transform = style.transform;
    const transformY = !transform || transform === 'none'
      ? 0
      : new DOMMatrixReadOnly(transform).m42;
    const color = style.borderTopColor || '';
    const match = color.match(/rgba?\(([^)]+)\)/i);
    let alpha = 1;
    if (match) {
      const parts = match[1].split(',').map((part) => part.trim());
      if (parts.length >= 4) alpha = Number.parseFloat(parts[3]);
    }
    return { transformY, color, alpha };
  });
}

test('Collections card uses the complete Library cover hover contract', async ({ page }) => {
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
  const art = card.locator('.collection-art');
  const actions = card.locator('.collection-picker-card-actions');
  const menu = card.locator('.collection-picker-menu');
  const trigger = menu.locator('> summary');

  await expect(card).toBeVisible();
  await expect(card).toHaveClass(/\bcover-card\b/);
  await expect(card.locator('.collection-picker-card-link')).toHaveClass(/\bcover-card-link\b/);
  await expect(art).toHaveClass(/\bcover-art\b/);
  await expect(actions).toHaveClass(/\bcover-card-actions\b/);
  await expect(menu).toHaveClass(/\bcover-row-menu\b/);
  await expect(card).not.toHaveClass(/library-hover-match/);
  await expect(actions).toBeHidden();
  await expect.poll(() => transformY(art)).toBeCloseTo(0, 1);
  await expect.poll(() => transformY(actions)).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).transformY).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).alpha).toBeCloseTo(0, 2);

  await card.hover();
  await expect(actions).toBeVisible();
  await expect(trigger).toBeVisible();
  await expect.poll(() => transformY(art)).toBeCloseTo(-4, 1);
  await expect.poll(() => transformY(actions)).toBeCloseTo(-4, 1);
  await expect.poll(async () => (await hoverFrameState(card)).transformY).toBeCloseTo(-4, 1);
  await expect.poll(async () => (await hoverFrameState(card)).alpha).toBeCloseTo(1, 2);

  await page.mouse.move(0, 0);
  await expect(actions).toBeHidden();
  await expect.poll(() => transformY(art)).toBeCloseTo(0, 1);
  await expect.poll(() => transformY(actions)).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).transformY).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).alpha).toBeCloseTo(0, 2);

  await card.hover();
  await expect(actions).toBeVisible();
  await trigger.click();
  await expect(menu).toHaveAttribute('open', '');
  await expect(card.getByRole('link', { name: 'Edit collection' })).toBeVisible();

  // A focused/open action menu may remain available, but every visual layer must
  // settle together when pointer hover ends so the moving lime frame cannot stick.
  await page.mouse.move(0, 0);
  await expect.poll(() => transformY(art)).toBeCloseTo(0, 1);
  await expect.poll(() => transformY(actions)).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).transformY).toBeCloseTo(0, 1);
  await expect.poll(async () => (await hoverFrameState(card)).alpha).toBeCloseTo(0, 2);
});

test('Smart Collection editing opens in a modal from the Collections picker', async ({ page }) => {
  await signIn(page);
  await page.goto(`${sandboxUrl}/collections`);

  const name = 'Acceptance Modal Smart Collection';
  let card = page.locator('.collection-picker-card', { hasText: name }).first();
  if (!(await card.count())) {
    await page.locator('.collection-create-launcher > button').click();
    const createDialog = page.locator('dialog.collection-create-dialog');
    await expect(createDialog).toBeVisible();
    await createDialog.getByRole('button', { name: 'Smart' }).click();
    const smartForm = createDialog.locator('form.smart-filter-grid');
    await smartForm.locator('input[name="name"]').fill(name);
    await smartForm.locator('select[name="resolution"]').selectOption('720');
    await Promise.all([
      page.waitForURL((url) => url.pathname === '/collections/smart/preview'),
      smartForm.getByRole('button', { name: 'Preview matching titles' }).click(),
    ]);
    await Promise.all([
      page.waitForURL((url) => /^\/collections\/\d+$/.test(url.pathname)),
      page.getByRole('button', { name: 'Save Smart Collection' }).click(),
    ]);
    await page.goto(`${sandboxUrl}/collections`);
    card = page.locator('.collection-picker-card', { hasText: name }).first();
  }

  await expect(card).toBeVisible();
  await card.hover();
  const menu = card.locator('.collection-picker-menu');
  await menu.locator('> summary').click();
  const smartEdit = card.getByRole('link', { name: 'Edit Smart Collection' });
  await expect(smartEdit).toBeVisible();
  await smartEdit.click();

  await expect(page).toHaveURL(`${sandboxUrl}/collections`);
  const editDialog = page.locator('dialog.smart-collection-edit-dialog');
  await expect(editDialog).toBeVisible();
  const editor = editDialog.locator('form.smart-collection-editor-form');
  await expect(editor).toBeVisible();
  await expect(editor.locator('input[name="name"]')).toHaveValue(name);
  await expect(editor.getByRole('button', { name: 'Save changes' })).toBeVisible();

  await editor.locator('[data-organize-close]').click();
  await expect(editDialog).toBeHidden();
  await expect(page).toHaveURL(`${sandboxUrl}/collections`);
});
