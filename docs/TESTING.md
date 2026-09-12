# InfoMancer Testing

InfoMancer uses two complementary test layers during development:

- Python regression tests for application logic and platform behavior.
- Playwright browser acceptance tests for real user workflows in disposable InfoMancer installations.

The 0.9 development line adds a local visual runner so browser tests can be watched without manually preparing fixture databases or starting acceptance servers.

## Python regression suite

From the repository root:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions runs this suite on Windows, macOS, and Linux for the canonical 0.9 alpha branch.

## One-time Playwright setup

From the `e2e` directory:

```bash
npm install
npx playwright install chromium
```

The browser runtime only needs to be installed again when Playwright requires a different browser revision.

## Watch InfoMancer test itself

From the `e2e` directory:

```bash
npm run test:watch
```

This command:

1. Rebuilds deterministic disposable media fixtures.
2. Starts three isolated local InfoMancer acceptance installations.
3. Waits until every installation reports healthy.
4. Runs the Playwright acceptance suite in a visible Chromium window.
5. Stops the temporary InfoMancer servers when the test run ends.

`test:headed` is kept as an alias for the same workflow.

## Interactive Playwright UI

For the most useful visual development experience:

```bash
npm run test:ui
```

Playwright UI Mode lets you select tests, run or re-run them, inspect each step, and review browser state while the disposable InfoMancer installations stay available behind the runner.

## Step-through debugging

```bash
npm run test:debug
```

This opens Playwright Inspector and pauses execution so individual browser actions can be stepped through.

## Local headless acceptance run

To reproduce the browser suite locally without opening a browser window:

```bash
npm run test:local
```

CI continues to use `npm test` because GitHub Actions prepares its own isolated acceptance servers before invoking Playwright.

## Additional Playwright arguments

Arguments after `--` are forwarded to Playwright. For example:

```bash
npm run test:watch -- acceptance.spec.js
npm run test:ui -- --grep "guided setup"
```

## Test evidence

Playwright is configured to retain the following evidence when a test fails:

- screenshots
- trace files
- video
- HTML report

Local output is written beneath:

```text
e2e/test-results/
e2e/playwright-report/
```

The visual runner also leaves its disposable server logs in:

```text
e2e/.e2e-runtime/logs/
```

The `.e2e-runtime` fixture area is rebuilt at the start of the next visual run, so it must never be treated as user data.

To reopen the most recent HTML report:

```bash
npm run test:report
```

## CI behavior

The canonical `testing/0.9-alpha` branch runs the full GitHub Actions test workflow on every push. The workflow includes:

- Python tests on Windows, macOS, and Linux
- Python dependency audit
- Bandit security scan
- Rust dependency audit
- Playwright browser acceptance
- compilation check
- retained test output and browser evidence

The workflow can also be started manually through GitHub Actions when a clean qualification run is useful without creating another code commit.
