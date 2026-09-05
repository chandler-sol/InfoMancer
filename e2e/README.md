# InfoMancer browser acceptance suite

This suite exercises complete user workflows against isolated InfoMancer servers with a deterministic synthetic media library. It is intentionally separate from native installer testing: the browser suite catches application workflow and UI regressions quickly, while the release workflow continues to smoke-test the packaged desktop application.

## Current acceptance coverage

- First-run Librarian creation with a configured bootstrap token.
- Guided setup navigation through General, Metadata, and Sources.
- Source-browser open/close behavior and canonical close-mark geometry.
- Folder navigation and Back behavior.
- Source preview, Add & Scan, final title/file totals, and source health.
- Activity unread state and Mark all read across more than the 250-row display window.
- Bulk movie-match review state, Apply selected feedback, and preservation of unresolved rows.

The media fixtures are tiny files with recognized media extensions. They are sufficient for catalog and workflow testing without storing real media or user data.

## Evidence on failure

The CI job retains the Playwright HTML report, screenshots, video, trace data, and isolated InfoMancer server logs. These artifacts are intended to make UI failures diagnosable without requiring a new desktop installer or a manual reproduction first.

## Running locally

Install the normal InfoMancer Python dependencies, then from `e2e/` run `npm install` and `npx playwright install chromium`. Create fixtures with `python create_fixtures.py`, start isolated InfoMancer servers using temporary databases and `MEDIA_BROWSE_ROOTS` pointing at the generated `media/Movies` directory, then run `npm test`.

CI is the canonical configuration because it creates and tears down both isolated servers automatically.

## Deliberately manual

Real Windows NFS/mapped-share behavior remains a manual release acceptance check. The automated suite covers the application behavior around source browsing, scanning, status updates, and review workflows but does not pretend a hosted Linux runner reproduces Windows network-provider behavior.
