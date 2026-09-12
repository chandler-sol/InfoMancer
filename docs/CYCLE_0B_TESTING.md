# 0.9 Cycle 0B: Visual Automated Testing

Cycle 0B turns the Beta 2 test foundation into a practical development workflow for 0.9.

## Test tiers

### Smoke
A fast visual confidence pass for startup, local authentication, core navigation, and primary UI chrome.

### Deep
A realistic interaction pass for guided setup, source browsing, scanning, Library state, view switching, workspace Inspector behavior, favorite mutation, and modal behavior.

### Full
The complete local qualification path: Python regression tests, compilation, and every browser acceptance scenario. Full has no artificial time ceiling and is the home for future expensive checks such as large-library fixtures, upgrade migrations, media-analysis matrices, and long-running stability coverage.

## Design rules

1. Local visual testing and CI use the same `run_visual.py` server orchestration.
2. Every scenario that mutates installation-wide state gets an isolated disposable database when needed.
3. Smoke must remain short enough to use repeatedly during development.
4. Deep should favor real workflows over isolated selector checks.
5. Full may become hours-long or overnight as meaningful coverage grows.
6. Do not add delay or repetition merely to make Full longer.
7. Screenshots, traces, video, reports, and server logs remain available when failures need investigation.

## Commands

From `e2e/`:

```bash
npm run test:smoke
npm run test:deep
npm run test:full
```

Headless and Playwright UI variants are documented in `docs/TESTING.md`.
