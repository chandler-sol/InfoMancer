const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const sandboxUrl = process.env.INFOMANCER_E2E_SANDBOX_URL || 'http://127.0.0.1:8788';
const sandboxDatabase = process.env.INFOMANCER_E2E_DATABASE || '';
const acceptancePassword = 'acceptance-password-123';

function seedState(...args) {
  if (!sandboxDatabase) throw new Error('INFOMANCER_E2E_DATABASE is required');
  execFileSync(
    'python',
    [path.join(process.cwd(), 'seed_state.py'), '--database', sandboxDatabase, ...args],
    { stdio: 'inherit' },
  );
}

async function signIn(page) {
  await page.goto(`${sandboxUrl}/login`);
  await page.getByRole('textbox', { name: 'Username or email' }).fill('acceptance-librarian');
  await page.locator('input[type="password"]').fill(acceptancePassword);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/login'),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ]);
}

test('bulk match review uses the unified determinate apply card and preserves unresolved rows', async ({ page }) => {
  seedState('--movie-suggestions', '6');
  await signIn(page);
  await page.goto(`${sandboxUrl}/movies/bulk-match?review=true`);

  await expect(page.locator('.match-check')).toHaveCount(6);
  await expect(page.locator('.match-check:checked')).toHaveCount(5);
  await expect(page.getByText('Deliberately Wrong Candidate')).toBeVisible();

  const progressPromise = page.evaluate(() => new Promise((resolve) => {
    const form = document.querySelector('[data-bulk-match-review-form]');
    form.addEventListener('submit', () => {
      const progress = document.querySelector('[data-bulk-match-progress]');
      const buttons = [...form.querySelectorAll('[data-bulk-apply-button]')];
      resolve({
        applying: form.dataset.bulkApplying || '',
        busy: form.getAttribute('aria-busy') || '',
        hidden: Boolean(progress?.hidden),
        phase: progress?.dataset.bulkMatchProgressPhase || '',
        heading: progress?.querySelector('[data-bulk-match-progress-heading]')?.textContent || '',
        copy: progress?.querySelector('[data-bulk-match-progress-copy]')?.textContent || '',
        hasFill: Boolean(progress?.querySelector('[data-bulk-match-progress-fill]')),
        allButtonsDisabled: buttons.length > 0 && buttons.every((button) => button.disabled),
        unresolvedVisible: document.body.textContent.includes('Deliberately Wrong Candidate'),
      });
    }, { once: true });
  }));

  await page.locator('[data-bulk-apply-button]').first().click({ noWaitAfter: true });
  const progress = await progressPromise;

  expect(progress.applying).toBe('1');
  expect(progress.busy).toBe('true');
  expect(progress.hidden).toBe(false);
  expect(progress.phase).toBe('apply');
  expect(progress.heading).toBe('Applying metadata for 5 movies');
  expect(progress.copy).toContain('0 of 5 applied');
  expect(progress.hasFill).toBe(true);
  expect(progress.allButtonsDisabled).toBe(true);
  expect(progress.unresolvedVisible).toBe(true);
});
