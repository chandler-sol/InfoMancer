# Showcase screenshots

InfoMancer includes an optional Playwright capture tool for making repeatable product screenshots from a running installation. It does not modify media, favorites, tags, matching, or filesystem state.

The default set captures five useful product states at three sizes:

- Dashboard
- Library in Covers view
- Library with the Inspector open on the first visible title
- Full detail page for the first visible title
- Review Workspace

The default sizes are:

- `desktop`: 1440 x 900
- `social`: 1200 x 675, a 16:9 posting-friendly frame
- `mobile`: 390 x 844

Files are written to `showcase/screenshots/` and are ignored by Git.

## Windows

With InfoMancer already running locally:

```powershell
.\scripts\capture-showcase.ps1 -Username YOUR_USERNAME
```

PowerShell prompts for the password without echoing it. The password is passed only to the capture process and is removed from the temporary environment afterward.

For a server or a different local port:

```powershell
.\scripts\capture-showcase.ps1 `
  -Url "https://infomancer.example.com" `
  -Username YOUR_USERNAME
```

On the first run the helper installs the small Playwright tooling package plus its Chromium browser. Later runs reuse them.

## macOS and Linux

```bash
bash scripts/capture-showcase.sh --username YOUR_USERNAME
```

For another InfoMancer URL:

```bash
bash scripts/capture-showcase.sh \
  --url "https://infomancer.example.com" \
  --username YOUR_USERNAME
```

The password prompt is silent. The helper installs Playwright and Chromium on the first run.

## Capture only specific frames

Use state slugs separated by commas:

```powershell
.\scripts\capture-showcase.ps1 -Username YOUR_USERNAME -Only "library,library-inspector,title-detail"
```

Available state slugs are `dashboard`, `library`, `library-inspector`, `title-detail`, and `review`.

Limit the output sizes with `-Variants` on Windows or `--variants` on macOS/Linux:

```powershell
.\scripts\capture-showcase.ps1 -Username YOUR_USERNAME -Variants "desktop,social"
```

```bash
bash scripts/capture-showcase.sh --username YOUR_USERNAME --variants "desktop,social"
```

## Privacy masking

The capture tool never writes the supplied login password to disk. Screenshots can still contain whatever is visible in InfoMancer, including media titles or installation-specific text.

For a public posting set, review every image before publishing. If a page contains a particular private DOM element, set `INFOMANCER_SHOWCASE_REDACT_SELECTORS` to a CSS selector before running the capture. Matching elements are replaced by a dark mask in the screenshots.

Example:

```powershell
$env:INFOMANCER_SHOWCASE_REDACT_SELECTORS = ".source-path,.account-email"
.\scripts\capture-showcase.ps1 -Username YOUR_USERNAME
```

The default showcase set intentionally avoids Sources, user administration, and Settings because those pages are more likely to expose machine paths, account details, or infrastructure information.

## Stable capture behavior

Each capture runs in a fresh browser context and temporarily sets the Library view cookie to Covers inside that isolated context. It does not change the view preference in your normal browser.

Animations and transitions are disabled for the screenshot frame, reduced-motion is requested, fonts and visible images are given a short opportunity to finish loading, and the page is returned to the top before capture. A `manifest.json` is produced beside the screenshots with the generated filenames, dimensions, and page states.

If the Library is empty, Inspector and title-detail screenshots are skipped rather than failing the entire run.
