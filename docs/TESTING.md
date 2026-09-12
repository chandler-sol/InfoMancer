# InfoMancer Testing

InfoMancer uses two complementary test layers during development:

- Python regression tests for application logic and platform behavior.
- Playwright browser acceptance tests for real user workflows in disposable InfoMancer installations.

The 0.9 development line adds local visual runners so the browser tests can be watched without manually preparing fixture databases or starting acceptance servers.

## One-time Playwright setup

From the `e2e` directory:

```bash
npm install
npx playwright install chromium
```

The browser runtime only needs to be installed again when Playwright requires a different browser revision.

## Three local test tiers

### Smoke

Use this constantly while developing:

```bash
npm run test:smoke
```

Smoke verifies the basics that should almost never be broken:

- InfoMancer starts and reports healthy before Playwright begins.
- A fresh Librarian account can be created.
- Local sign-in works after clearing the browser session.
- Dashboard, Library, Review, Sources, and System Settings load successfully.
- Primary Library UI controls render.
- No uncaught browser page errors occur during the pass.

The goal is a short confidence check rather than exhaustive coverage.

### Deep

Use this after meaningful UI, library, source, scanner, modal, or Inspector work:

```bash
npm run test:deep
```

Deep runs the Smoke pass and then exercises a more realistic workflow:

- guided setup
- source browser modal
- deterministic source preview
- source add and scan
- Library population
- list and cover view switching
- workspace Inspector opening from a Library title
- Inspector health, media, metadata, and organization sections
- favorite state mutation
- organization modal opening and closing

This tier is intended to catch interaction regressions that unit tests and simple page-load checks cannot see.

### Full

Use this before promoting a Dev build, before a Beta candidate, after broad architectural changes, or whenever the machine can be left working unattended:

```bash
npm run test:full
```

Full runs:

1. the complete Python regression suite
2. application bytecode compilation
3. the complete Playwright browser acceptance suite, including Smoke, Deep, guided onboarding, source workflows, activity behavior, bulk matching, collections, modal behavior, and every additional acceptance spec added later

There is intentionally no short runtime target for Full. As the 0.9 suite grows, this is the tier where expensive checks belong. It is acceptable for a local Full qualification to take hours or eventually run overnight. Do not make Full artificially slow by repeating identical tests without a reason. Add expensive coverage when it validates a real failure mode, migration path, large-library condition, media-analysis case, or platform behavior.

## Visible and headless variants

The default Smoke, Deep, and Full commands use visible Chromium so the run can be watched.

Headless equivalents are available for unattended work:

```bash
npm run test:smoke:headless
npm run test:deep:headless
npm run test:full:headless
```

Interactive Playwright UI variants are also available:

```bash
npm run test:smoke:ui
npm run test:deep:ui
npm run test:full:ui
```

Playwright UI Mode lets you select tests, run or re-run them, inspect individual actions, and review browser state.

## General browser test tools

To run the complete browser suite without first running Python tests:

```bash
npm run test:watch
```

For Playwright UI Mode:

```bash
npm run test:ui
```

For step-through debugging with Playwright Inspector:

```bash
npm run test:debug
```

For a local headless browser-only run:

```bash
npm run test:local
```

## Disposable installations

The local runner builds deterministic media fixtures and starts isolated InfoMancer installations on loopback ports for different scenarios. Smoke and Deep each have their own database so their state cannot interfere with the longer acceptance scenarios.

The runner waits for every instance to report healthy before starting Playwright and shuts every temporary server down when the run ends.

The disposable runtime lives at:

```text
e2e/.e2e-runtime/
```

It is rebuilt at the beginning of the next visual run and must never be treated as user data.

## Test evidence

Playwright retains failure evidence including:

- screenshots
- trace files
- video
- HTML reports

Local output is written beneath:

```text
e2e/test-results/
e2e/playwright-report/
e2e/.e2e-runtime/logs/
```

To reopen the most recent HTML report:

```bash
npm run test:report
```

## CI behavior

The canonical `testing/0.9-alpha` branch runs the full GitHub Actions qualification workflow on every push. CI remains optimized for qualification rather than visual watching. It includes:

- Python tests on Windows, macOS, and Linux
- Python dependency audit
- Bandit security scan
- Rust dependency audit
- the complete isolated Playwright browser suite
- compilation checks
- retained Python output and browser evidence

The CI browser job uses the same `run_visual.py` orchestration as local testing so the local and hosted acceptance environments do not drift apart.

The workflow can also be started manually through GitHub Actions when a clean qualification run is useful without creating another code commit.
