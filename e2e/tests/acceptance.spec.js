const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const tokenUrl = process.env.INFOMANCER_E2E_TOKEN_URL || 'http://127.0.0.1:8787';
const sandboxUrl = process.env.INFOMANCER_E2E_SANDBOX_URL || 'http://127.0.0.1:8788';
const sandboxDatabase = process.env.INFOMANCER_E2E_DATABASE || '';
const bootstrapToken = process.env.INFOMANCER_E2E_BOOTSTRAP_TOKEN || 'e2e-library-card-123456';

async function createLibrarian(page, baseUrl, username, token = '') {
  await page.goto(`${baseUrl}/setup`);
  await expect(page.getByRole('heading', { name: 'Create your Librarian' })).toBeVisible();

  const bootstrap = page.locator('input[name="bootstrap_token"]');
  if (await bootstrap.count()) await bootstrap.fill(token);
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="display_name"]').fill('Acceptance Librarian');
  const email = page.locator('input[name="email"]');
  if (await email.count()) await email.fill(`${username}@example.invalid`);
  await page.locator('input[name="password"]').fill('acceptance-password-123');
  await page.locator('input[name="password_confirm"]').fill('acceptance-password-123');

  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/setup'),
    page.getByRole('button', { name: 'Create Librarian account' }).click(),
  ]);
}

async function dismissFreshInstallTour(page, testInfo, attachmentName) {
  const tour = page.locator('#onboarding-tour');
  await expect(tour).toBeVisible({ timeout: 12000 });
  await expect(tour.getByRole('heading', { name: 'Meet the 0.8 workspace' })).toBeVisible();
  await expect(tour.locator('#tour-step-label')).toContainText(/^1 of \d+$/);
  await expect(tour.getByRole('button', { name: 'Skip for now' })).toBeVisible();
  await attach(tour, testInfo, attachmentName);

  await Promise.all([
    page.waitForURL((url) => url.pathname === '/' && url.searchParams.get('setup_prompt') === '1'),
    tour.getByRole('button', { name: 'Skip for now' }).click(),
  ]);

  const setupChoice = page.locator('.setup-choice-layer');
  await expect(setupChoice).toBeVisible();
  await expect(setupChoice.getByRole('heading', { name: 'How would you like to begin?' })).toBeVisible();
  return setupChoice;
}

async function enterGuidedSetupFromFreshInstall(page, testInfo) {
  const setupChoice = await dismissFreshInstallTour(page, testInfo, 'fresh-install-tour');
  await Promise.all([
    page.waitForURL(/\/getting-started\/general/),
    setupChoice.getByRole('button', { name: /Guided setup/ }).click(),
  ]);
  await expect(page.getByRole('heading', { name: 'Set your time zone' })).toBeVisible();
}

function seedState(...args) {
  if (!sandboxDatabase) throw new Error('INFOMANCER_E2E_DATABASE is required');
  execFileSync(
    'python',
    [path.join(process.cwd(), 'seed_state.py'), '--database', sandboxDatabase, ...args],
    { stdio: 'inherit' },
  );
}

async function attach(pageOrLocator, testInfo, name, fullPage = false) {
  const body = await pageOrLocator.screenshot(fullPage ? { fullPage: true } : {});
  await testInfo.attach(name, { body, contentType: 'image/png' });
}

test.describe('InfoMancer browser acceptance', () => {
  // These tests intentionally mutate two persistent disposable servers. Retrying a
  // completed bootstrap or setup step against the same database produces false
  // failures, so keep this stateful acceptance group single-attempt.
  test.describe.configure({ retries: 0 });

  test('bootstrap-token setup creates the first Librarian and starts the fresh-install tour', async ({ page }, testInfo) => {
    await page.goto(`${tokenUrl}/setup`);
    const bootstrap = page.locator('input[name="bootstrap_token"]');
    await expect(bootstrap).toBeVisible();
    await expect(page.locator('label:has(input[name="bootstrap_token"])')).toContainText('Bootstrap token');
    await attach(page, testInfo, 'bootstrap-setup', true);

    await createLibrarian(page, tokenUrl, 'token-librarian', bootstrapToken);
    const setupChoice = await dismissFreshInstallTour(page, testInfo, 'bootstrap-fresh-install-tour');
    await expect(setupChoice.getByRole('button', { name: /Guided setup/ })).toBeVisible();
    await expect(setupChoice.getByRole('button', { name: /Set up manually/ })).toBeVisible();
  });

  test('guided setup can browse, back out, preview, add, and scan a deterministic source', async ({ page }, testInfo) => {
    await createLibrarian(page, sandboxUrl, 'acceptance-librarian');
    await enterGuidedSetupFromFreshInstall(page, testInfo);

    await page.getByRole('button', { name: 'Save and continue' }).click();
    await expect(page).toHaveURL(/\/getting-started\/metadata/);
    await page.getByRole('button', { name: 'Skip in testing mode' }).click();
    await expect(page).toHaveURL(/\/getting-started\/sources/);

    await page.getByRole('button', { name: 'Browse folders' }).click();
    const dialog = page.locator('dialog.source-browser');
    await expect(dialog).toBeVisible();
    const close = dialog.getByRole('button', { name: 'Close folder browser' });
    const closeGeometry = await close.evaluate((element) => {
      const before = getComputedStyle(element, '::before');
      const after = getComputedStyle(element, '::after');
      return {
        beforeWidth: Number.parseFloat(before.width),
        afterWidth: Number.parseFloat(after.width),
        beforeTransform: before.transform,
        afterTransform: after.transform,
      };
    });
    expect(closeGeometry.beforeWidth).toBeGreaterThan(10);
    expect(closeGeometry.afterWidth).toBeGreaterThan(10);
    expect(closeGeometry.beforeTransform).not.toBe('none');
    expect(closeGeometry.afterTransform).not.toBe('none');
    expect(closeGeometry.beforeTransform).not.toBe(closeGeometry.afterTransform);
    await attach(dialog, testInfo, 'source-browser-location-chooser');

    await close.click();
    await expect(dialog).toBeHidden();
    await page.getByRole('button', { name: 'Browse folders' }).click();
    await expect(dialog).toBeVisible();

    const moviesLocation = dialog.locator('.source-folder').filter({ hasText: 'Movies' }).first();
    await expect(moviesLocation).toBeVisible();
    await moviesLocation.click();
    await expect(dialog.locator('#source-current-name')).toHaveText('Movies');

    const bucketA = dialog.locator('.source-folder').filter({ hasText: /^.*A.*Open/ }).first();
    await expect(bucketA).toBeVisible();
    await bucketA.click();
    await expect(dialog.locator('#source-current-name')).toHaveText('A');
    await dialog.getByRole('button', { name: '← Back' }).click();
    await expect(dialog.locator('#source-current-name')).toHaveText('Movies');

    await dialog.getByRole('button', { name: 'Preview this folder' }).click();
    await expect(dialog.locator('#source-preview')).toBeVisible();
    await expect(dialog.locator('#source-preview-stats')).toContainText('12');
    await expect(dialog.locator('#source-recommendation')).toContainText('Movies detected');
    await attach(dialog, testInfo, 'source-browser-preview');

    await Promise.all([
      page.waitForURL(/\/getting-started\/sources/),
      dialog.getByRole('button', { name: 'Add & Scan' }).click(),
    ]);

    await page.goto(`${sandboxUrl}/sources`);
    const sourceRow = page.locator('.root-row').filter({ hasText: 'Movies' }).first();
    await expect(sourceRow).toBeVisible();
    await expect(sourceRow).toContainText('12 titles', { timeout: 20000 });
    await expect(sourceRow).toContainText('12 video files');
    await expect(sourceRow).toContainText(/Healthy/i);
    await attach(page, testInfo, 'sources-after-scan', true);
  });

  test('Mark all read clears the entire Activity inbox, not only the visible 250', async ({ page }, testInfo) => {
    seedState('--activity', '300');
    await page.goto(`${sandboxUrl}/activity`);

    await expect(page.getByRole('button', { name: 'Mark all read' })).toBeVisible();
    await expect(page.locator('.activity-item.unread').first()).toBeVisible();
    await attach(page, testInfo, 'activity-unread', true);

    await Promise.all([
      page.waitForURL((url) => url.pathname === '/activity'),
      page.getByRole('button', { name: 'Mark all read' }).click(),
    ]);

    await expect(page.getByRole('button', { name: 'Mark all read' })).toHaveCount(0);
    await expect(page.locator('.activity-item.unread')).toHaveCount(0);
    const activityNav = page.locator('a[href="/activity"]').first();
    await expect(activityNav).not.toContainText('250');
    await attach(page, testInfo, 'activity-read', true);
  });

  test('bulk match review gives visible apply feedback and preserves unresolved rows', async ({ page }, testInfo) => {
    seedState('--movie-suggestions', '6');
    await page.goto(`${sandboxUrl}/movies/bulk-match?review=true`);

    await expect(page.getByRole('heading', { name: 'Bulk movie matching' })).toBeVisible();
    await expect(page.locator('.match-check')).toHaveCount(6);
    await expect(page.locator('.match-check:checked')).toHaveCount(5);
    await expect(page.getByText('Deliberately Wrong Candidate')).toBeVisible();
    await attach(page, testInfo, 'bulk-match-review', true);

    await page.route('**/movies/bulk-match', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise((resolve) => setTimeout(resolve, 1200));
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<!doctype html><title>Acceptance submit complete</title><p>Accepted by E2E harness</p>',
      });
    });

    const apply = page.locator('[data-bulk-apply-button]').first();
    await apply.click({ noWaitAfter: true });
    const status = page.locator('[data-bulk-apply-status]');
    await expect(status).toBeVisible();
    await expect(status).toContainText('Applying 5 selected movies');
    await expect(status.locator('.task-track')).toBeVisible();
    await expect(apply).toBeDisabled();
    await expect(page.getByText('Deliberately Wrong Candidate')).toBeVisible();
  });
});
