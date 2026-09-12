const { test, expect } = require('@playwright/test');

const baseUrl = process.env.INFOMANCER_E2E_SMOKE_URL || 'http://127.0.0.1:8790';
const password = 'acceptance-password-123';

test.describe.configure({ retries: 0 });

async function createLibrarian(page) {
  await page.goto(`${baseUrl}/setup`);
  await expect(page.getByRole('heading', { name: 'Create your Librarian' })).toBeVisible();
  await page.locator('input[name="username"]').fill('smoke-librarian');
  await page.locator('input[name="display_name"]').fill('Smoke Librarian');
  const email = page.locator('input[name="email"]');
  if (await email.count()) await email.fill('smoke@example.invalid');
  await page.locator('input[name="password"]').fill(password);
  await page.locator('input[name="password_confirm"]').fill(password);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/setup'),
    page.getByRole('button', { name: 'Create Librarian account' }).click(),
  ]);
}

async function dismissTour(page) {
  const tour = page.locator('#onboarding-tour');
  await expect(tour).toBeVisible({ timeout: 12000 });
  await Promise.all([
    page.waitForURL((url) => url.pathname === '/' && url.searchParams.get('setup_prompt') === '1'),
    tour.getByRole('button', { name: 'Skip for now' }).click(),
  ]);
}

async function signIn(page) {
  await page.goto(`${baseUrl}/login`);
  await page.getByRole('textbox', { name: 'Username or email' }).fill('smoke-librarian');
  await page.locator('input[type="password"]').fill(password);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/login'),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ]);
}

async function expectHealthyPage(page, path) {
  const response = await page.goto(`${baseUrl}${path}`);
  expect(response).not.toBeNull();
  expect(response.ok()).toBeTruthy();
  await expect(page.locator('body')).toBeVisible();
  await expect(page).not.toHaveURL(/\/login/);
}

test('smoke: startup, login, navigation, and primary UI chrome stay usable', async ({ page }, testInfo) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await createLibrarian(page);
  await dismissTour(page);

  await page.context().clearCookies();
  await signIn(page);

  await expectHealthyPage(page, '/');
  await expect(page.locator('a[href="/library"]').first()).toBeVisible();
  await expect(page.locator('a[href="/review"]').first()).toBeVisible();
  await expect(page.locator('a[href="/sources"]').first()).toBeVisible();

  await expectHealthyPage(page, '/library');
  await expect(page.locator('.catalog-tabs')).toBeVisible();
  const searchToggle = page.locator('#library-filter-search-toggle');
  await expect(searchToggle).toBeVisible();
  await searchToggle.click();
  await expect(page.locator('#live-library-search')).toBeVisible();
  await expect(page.locator('#library-list-view')).toBeVisible();
  await expect(page.locator('#library-cover-view')).toBeVisible();

  await expectHealthyPage(page, '/review');
  await expectHealthyPage(page, '/sources');
  await expectHealthyPage(page, '/settings/system');
  await expect(page.locator('.settings-nav, [aria-label="Settings"]').first()).toBeVisible();

  await expectHealthyPage(page, '/settings/updates');
  await expect(page.getByRole('heading', { name: 'Updates' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Standard', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Beta', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dev', exact: true })).toBeVisible();
  await expect(page.getByText('Dev only advances after qualification succeeds.')).toBeVisible();
  await expect(page.getByText(/never silently downgrades/)).toBeVisible();

  await testInfo.attach('smoke-update-channels', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });

  expect(pageErrors).toEqual([]);
});
