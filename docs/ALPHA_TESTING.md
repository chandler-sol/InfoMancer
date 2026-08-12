# InfoMancer alpha testing checklist

Use a disposable sandbox or make a verified database backup before testing. InfoMancer must never rename, move, restore, or delete media without a preview and explicit confirmation.

Record platform results in `docs/CROSS_PLATFORM_ALPHA_MATRIX.md`.

## Installation and access

- Complete first-run setup on Windows, macOS, or Linux.
- Confirm login, logout, password recovery, and mobile layout.
- Add both a Movie source and a TV source, then scan each.

## Catalog and intelligence

- Match one movie and one TV series; verify the identity explanation.
- Open Library Health and confirm every finding explains what happened and what to do next.
- Open Storage Intelligence and compare source totals with the source folders.
- Review duplicates, search and sort the list, then use a bulk classification action.
- Verify an exact duplicate by content. Confirm no file changes occur during verification.
- Move a test duplicate to managed Trash, restore it, and confirm the original path returns.

## Recovery and diagnostics

- Create and verify a database backup. Download it before testing restore.
- Export portable settings and library data.
- Download a diagnostic bundle and confirm it contains no password, session, API key, PIN, or media database.

## Feedback to send

Include the operating system, installation method, browser, action attempted, expected result, actual result, plain-language error shown, and a diagnostic bundle when appropriate. Never send API keys or passwords.
