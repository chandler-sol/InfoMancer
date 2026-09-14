const { test, expect } = require('@playwright/test');

const baseUrl = process.env.INFOMANCER_E2E_DEEP_URL || 'http://127.0.0.1:8791';
const password = 'acceptance-password-123';

test.describe.configure({ retries: 0 });

async function createLibrarian(page) {
  await page.goto(`${baseUrl}/setup`);
  await expect(page.getByRole('heading', { name: 'Create your Librarian' })).toBeVisible();
  await page.locator('input[name="username"]').fill('deep-librarian');
  await page.locator('input[name="display_name"]').fill('Deep Test Librarian');
  const email = page.locator('input[name="email"]');
  if (await email.count()) await email.fill('deep@example.invalid');
  await page.locator('input[name="password"]').fill(password);
  await page.locator('input[name="password_confirm"]').fill(password);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/setup'),
    page.getByRole('button', { name: 'Create Librarian account' }).click(),
  ]);
}

async function enterGuidedSetup(page) {
  const tour = page.locator('#onboarding-tour');
  await expect(tour).toBeVisible({ timeout: 12000 });
  await Promise.all([
    page.waitForURL((url) => url.pathname === '/' && url.searchParams.get('setup_prompt') === '1'),
    tour.getByRole('button', { name: 'Skip for now' }).click(),
  ]);
  const choice = page.locator('.setup-choice-layer');
  await expect(choice).toBeVisible();
  await Promise.all([
    page.waitForURL(/\/getting-started\/general/),
    choice.getByRole('button', { name: /Guided setup/ }).click(),
  ]);
}

test('deep: source workflow, library interaction, inspector, and modal UI stay coherent', async ({ page }, testInfo) => {
  await createLibrarian(page);
  await enterGuidedSetup(page);

  await page.getByRole('button', { name: 'Save and continue' }).click();
  await expect(page).toHaveURL(/\/getting-started\/metadata/);
  await page.getByRole('button', { name: 'Skip in testing mode' }).click();
  await expect(page).toHaveURL(/\/getting-started\/sources/);

  await page.getByRole('button', { name: 'Browse folders' }).click();
  const sourceDialog = page.locator('dialog.source-browser');
  await expect(sourceDialog).toBeVisible();

  const moviesLocation = sourceDialog.locator('.source-folder').filter({ hasText: 'Movies' }).first();
  await expect(moviesLocation).toBeVisible();
  await moviesLocation.click();
  await expect(sourceDialog.locator('#source-current-name')).toHaveText('Movies');
  await sourceDialog.getByRole('button', { name: 'Preview this folder' }).click();
  await expect(sourceDialog.locator('#source-preview')).toBeVisible();
  await expect(sourceDialog.locator('#source-preview-stats')).toContainText('12');

  await Promise.all([
    page.waitForURL(/\/getting-started\/sources/),
    sourceDialog.getByRole('button', { name: 'Add & Scan' }).click(),
  ]);

  await page.goto(`${baseUrl}/sources`);
  const sourceRow = page.locator('.root-row').filter({ hasText: 'Movies' }).first();
  await expect(sourceRow).toBeVisible();
  await expect(sourceRow).toContainText('12 titles', { timeout: 20000 });
  await expect(sourceRow).toContainText('12 video files');

  await page.goto(`${baseUrl}/movies`);
  const list = page.locator('.library-table');
  await expect(list).toBeVisible();
  await expect.poll(async () => {
    const count = await page.locator('.library-table [data-workspace-title-id]').count();
    if (!count) await page.reload();
    return count;
  }, {
    message: 'scanned movie titles should become visible in the Library',
    timeout: 20000,
    intervals: [250, 500, 1000, 2000],
  }).toBeGreaterThan(0);
  await expect(page.locator('.library-table [data-workspace-title-id]').first()).toBeVisible();

  await page.locator('#library-cover-view').click();
  await expect(page.locator('#cover-library')).toBeVisible();
  await expect(page.locator('#cover-library [data-workspace-title-id]').first()).toBeVisible();
  await page.locator('#library-list-view').click();
  await expect(page.locator('.library-table')).toBeVisible();
  await page.locator('#library-cover-view').click();

  const firstCard = page.locator('#cover-library [data-workspace-title-id]').first();
  await firstCard.dispatchEvent('click');
  const inspector = page.locator('[data-workspace-inspector-panel]');
  await expect(inspector).toBeVisible({ timeout: 10000 });
  await expect(inspector.getByText('Health & attention')).toBeVisible();
  await expect(inspector.getByText('Media', { exact: true })).toBeVisible();
  await expect(inspector.getByText('Metadata', { exact: true })).toBeVisible();
  await expect(inspector.getByText('Organization', { exact: true })).toBeVisible();

  const favorite = inspector.locator('[data-workspace-favorite]');
  await expect(favorite).toBeVisible();
  const beforeFavorite = await favorite.getAttribute('aria-pressed');
  await favorite.click();
  await expect(favorite).toHaveAttribute('aria-pressed', beforeFavorite === 'true' ? 'false' : 'true');

  await inspector.locator('[data-organize-dialog]').first().click();
  const organizeDialog = page.locator('dialog[open]').last();
  await expect(organizeDialog).toBeVisible();
  await testInfo.attach('deep-inspector-organize-modal', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });

  const close = organizeDialog.locator('[data-organize-close]').first();
  if (await close.count()) {
    await close.click();
  } else {
    await page.keyboard.press('Escape');
  }
  await expect(organizeDialog).toBeHidden();
});
