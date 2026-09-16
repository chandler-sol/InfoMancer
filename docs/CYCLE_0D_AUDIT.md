# Cycle 0D architecture and security audit

This document is the working evidence log for Issue #82. Cycle 0D is a hardening and behavior-preserving refactor cycle. Findings are separated from follow-up cleanup so a concern is not treated as a release blocker unless the code path and failure mode are established.

## Frozen baseline

- Canonical branch: `testing/0.9-alpha`
- Qualified baseline commit: `f04eb2017112a04bc9944658d8b5c4d20d32b8fa`
- Baseline Tests workflow: #2512, successful
- Qualified Dev: `0.9.0-dev.2512`, tag `desktop-dev-2512-f04eb201`
- Exact-source Beta: `0.9.0-beta.2`, tag `desktop-beta-0.9.0-beta.2-f04eb201`
- Signed downgrade proof: workflow run `34797581818`, successful
- Audit branch: `audit/0.9-cycle-0d`
- Draft audit PR: #83

The canonical baseline remains frozen while Cycle 0D changes are isolated on the audit branch.

## Current checkpoint

The audit branch has now completed the major security, data-integrity, update-channel, desktop-navigation, and qualification work discovered during the 0D review. The latest clean code checkpoint before this document refresh was commit `69a2d5d966db1bd615f776d06496ffe303b25691`.

The most recent fully qualified combined checkpoint before the final rename error-contract cleanup was PR Tests run #2588. That run covered Windows, macOS, Ubuntu, browser acceptance, Rust desktop tests, and the security/dependency audit. A final exact-head run is required after the last tiny rename error-contract change and this document-only refresh.

One confirmed release-process blocker remains outside the audit branch: repository-level protection for `testing/0.9-alpha`. The connected GitHub App cannot configure that owner-controlled setting.

## Architecture inventory

### Composition and lifecycle

`app/main.py` remains both the ASGI composition root and a large application module. Importing it still initializes the database, authentication and domain services, provider secrets, TVDB, background coordination, middleware, first-run/auth/account/admin routes, scan/metadata helpers, and router assembly.

The older W1.5 extraction pattern uses `RouteContext(globals())`. Route modules can obtain `LiveRef` proxies into the mutable `app.main` namespace, and the composition root still publishes returned compatibility handlers through `globals().update()`.

Cycle 0D removed the route-context write channel and several hidden replacement patterns, but it intentionally did not turn this audit into a wholesale application-factory rewrite. Remaining `LiveRef` read-through dependencies are maintainability debt for incremental cleanup, not a newly discovered release blocker.

### Route ownership

The baseline runtime had 12 duplicate method/path registrations whose behavior depended on Starlette first-match order. Cycle 0D reduced every method/path pair to one live owner while retaining compatibility aliases where required. Regression tests now reject duplicate live route ownership.

### Background work

`BackgroundCoordinator` owns process-local state for scans, metadata work, hashing, duplicate verification, media inspection, managed-Trash cleanup, the scheduler, and the single-runtime lease. Recovery now coordinates with an application-wide maintenance gate so restore cannot race ordinary requests or new background starts.

## Security controls confirmed and preserved

Cycle 0D preserved the existing controls that were already sound:

- Argon2 password hashing with explicit cost settings and a dummy hash for missing accounts.
- Random session tokens stored only as SHA-256 hashes.
- Random CSRF tokens and constant-time comparisons.
- Per-account/IP throttling plus persistent aggregate login lockouts.
- Expiring, one-time, hashed invitation tokens.
- Host-header validation and local-only redirect validation.
- Global CSRF checks for unsafe authenticated requests.
- Same-origin and CSRF enforcement in disabled-auth local mode.
- Signed Cloudflare identity validation without automatic account creation.
- Request-local CSP nonces for Jinja-rendered pages.
- Librarian-only diagnostics with path, identity, network, credential, session, and secret redaction.
- Recovery package validation for unsafe paths, collisions, unsupported compression, expansion abuse, undeclared files, checksum failures, invalid database snapshots, and unsupported roles.
- Tauri updater signature verification with a fixed public key.
- No remote-origin Tauri capability grant to the main window.
- SHA-pinned first-party Actions in the main qualification workflow.
- Read-only default workflow contents permission in the main test workflow.
- Python, Rust, npm, browser, and multi-platform qualification gates.

## Confirmed findings and disposition

### D0-001: qualified release branch is not protected

**Severity: High, supply-chain/process**

`testing/0.9-alpha` does not currently expose an active repository ruleset through the available integration, and earlier branch inspection showed no required status checks. A direct update to the qualified branch can therefore bypass the intended review boundary and later participate in signed Dev publication if qualification succeeds.

**Status: OPEN, repository-owner action required.**

The connected GitHub App does not have repository administration permission, so Cycle 0D intentionally does not guess or force branch-protection settings. Before treating the qualified release branch as fully protected, the repository owner should configure branch/ruleset policy so direct updates cannot bypass the intended PR and CI flow. Signed publication secrets should remain inaccessible to untrusted pull-request code.

### D0-002: recovery did not establish an exclusive application maintenance barrier

**Severity: High, data integrity**

The baseline restore flow checked background status but did not prevent a new ordinary request, scheduler tick, or background job from starting between the final check and database replacement.

**Status: FIXED.**

`MaintenanceGate` now coordinates ordinary HTTP admission, scheduler ticks, and background-job start transitions. Recovery acquires exclusive mode before final quiescence, failed recovery releases the gate, and successful database replacement remains exclusive until restart. Regression coverage includes busy admission, health probes, active-work refusal, and successful-restore exclusivity. The implementation first passed the full matrix in PR Tests run #2513 and remained green in later combined runs.

### D0-003: route dependencies could mutate the `main.py` namespace during router construction

**Severity: High, architecture/auditability**

The baseline route context allowed hidden writes into the mutable composition-root namespace, including security-sensitive wrapper replacement.

**Status: FIXED for mutation ownership; incremental read-through cleanup remains.**

`RouteContext.set()` has been removed. `security_hardening` and `final_polish` return compatibility handlers explicitly for installation by the composition root. A regression contract rejects future `ctx.set()` calls under `app/routes`. TVDB credential rotation now mutates the existing verified client in place after persistence instead of replacing the global client object and invalidating `LiveRef` consumers. The combined change passed PR Tests run #2537.

`LiveRef` read-through dependencies and `globals().update()` compatibility publication remain architecture debt for later incremental decomposition, but the hidden route-construction write channel is closed.

### D0-004: duplicate route ownership made security behavior registration-order dependent

**Severity: High, authorization/security correctness**

The baseline had 12 duplicate method/path registrations, including diagnostics and source-management routes where the hardened behavior won only because its router happened to be registered first.

**Status: FIXED.**

`app/routes/__init__.py` suppresses shadowed legacy registrations while retaining callable aliases. Tests require method/path uniqueness and pin the intended canonical owners. The route changes passed Windows, macOS, Ubuntu, and browser qualification in #2523 and remained green in later full runs.

### D0-005: migration compatibility declarations overstated additive behavior

**Severity: Medium, downgrade-contract correctness**

Migration 17 installs behavioral triggers and therefore was not purely additive even though its downgrade contract remains compatible.

**Status: FIXED.**

Migration 17 now uses the behavioral classification while preserving historical append-only compatibility snapshots. Tests cover fresh classification and non-rewrite of existing snapshots. Full qualification passed in #2513.

### D0-006: desktop TLS dependency acquired a RustSec advisory

**Severity: Medium, desktop TLS/supply chain**

`rustls` 0.23.43 became affected by `RUSTSEC-2026-0285` after the frozen baseline had originally qualified.

**Status: FIXED.**

Cargo-generated lock resolution updated `rustls` to 0.23.45 without suppressing the advisory. CI now verifies Cargo manifest/lock synchronization with `cargo metadata --locked` before `cargo audit`. The fixed lockfile has remained green in subsequent security/dependency runs.

### D0-007: configured provider-secret encryption used a fast password derivation

**Severity: Medium, secret-at-rest hardening**

The baseline converted `INFOMANCER_SECRET` directly through one SHA-256 operation before using it as Fernet key material. That is acceptable for high-entropy key material but unnecessarily weak for a human-chosen secret.

**Status: FIXED.**

New configured-secret writes use a version-2 envelope with a random 16-byte salt and Scrypt (`N=2^15`, `r=8`, `p=1`) before Fernet. Local-key installs use the same versioned envelope with an explicit key source. Legacy application-secret and local-key ciphertext remain readable, and the next successful write migrates them. Wrong secrets and unknown future versions fail closed. Local-key creation uses exclusive creation and restrictive permissions where supported.

A later filesystem pass also made failed provider-secret writes clean up unique temporary files rather than leaving stale restricted temp files behind. The provider-secret work passed #2539 and the later cleanup remained green through #2578 and subsequent combined runs.

### D0-008: media rename/move paths had containment, collision, and rollback race gaps

**Severity: High, filesystem/data integrity**

The first manifestations were season-folder creation and managed-Trash restore, where a directory could change after validation but before the media move. Broader review then found the same class in operation-history undo, persisted rename proposals, and legacy live title rename routes.

**Status: FIXED for the reviewed application mutation paths.**

Cycle 0D now applies the following protections across the reviewed rename/move paths:

- re-resolve source and destination against their authoritative media root immediately before mutation;
- recheck source type/presence and destination collision immediately before the rename/move;
- refuse to overwrite a new destination that appears during rollback;
- fail closed when a parent is replaced by a symlink or junction-like path boundary;
- preserve both conflicting files when a safe rollback cannot be completed;
- return a controlled domain error when a catalog failure is rolled back successfully instead of exposing a raw database 500;
- centralize the live title-route file/folder mutations through `SafeFileRenameService` rather than five independent raw `Path.rename()` implementations.

Adversarial tests cover late symlink substitution, source/destination collision appearance, catalog failure, safe rollback, rollback collision refusal, undo safety, managed-Trash restore, season-folder mutation, persisted rename proposals, live file rename, and live folder rename.

The broad filesystem hardening was progressively qualified through #2541, #2543, #2578, #2580, and #2588. A final exact-head run is required after the last controlled-error cleanup.

A very small residual pathname race still exists between the final userspace check and the operating-system rename primitive. Eliminating that class completely on every supported platform would require descriptor-based or platform-specific no-replace primitives. Cycle 0D treats that as future platform hardening rather than a reason to duplicate more userspace checks.

### D0-009: update metadata and artifact transport allowed weaker URL contracts

**Severity: High, update integrity**

The update-channel runtime and publication tooling did not consistently require credential-free HTTPS for metadata, redirects, and published artifact URLs.

**Status: FIXED.**

Runtime update metadata fetches require credential-free HTTPS, reject HTTPS-to-HTTP downgrade redirects and credentialed redirects, and cap metadata responses at 2 MiB. Published artifact, qualification, and release-note URLs are required to use HTTPS by both the manifest builder and schema. The promotion helper uses the same credential-free HTTPS artifact contract.

The operator-configured `INFOMANCER_UPDATE_MANIFEST_BASE_URL` remains a deliberate deployment trust boundary and may point to private/LAN HTTPS infrastructure. That value is not ordinary web-user input.

Runtime transport hardening passed #2548. Publication/schema hardening passed #2551 and remained green in later combined runs.

### D0-010: signed Dev publication workflow had incorrect updater assumptions

**Severity: High, supply chain/release correctness**

The signed Dev publisher had drifted from the actual Tauri updater artifact contract, did not reliably advance the rolling feeds, and blurred Authenticode packaging with Tauri updater signing.

**Status: FIXED.**

The Dev publisher now uses a SHA-pinned Tauri action, pins publication to the exact triggering commit, emits updater JSON, prefers the NSIS updater artifact, verifies immutable release assets, uses the expected updater signature artifact, and advances both the rolling `desktop-dev/latest.json` and `update-channels/dev.json` feeds. Source-contract tests protect the publisher assumptions.

Later full qualification runs, including #2588, exercise the retained supply-chain contracts.

### D0-011: Rust desktop tests existed but were not part of required PR qualification

**Severity: High, desktop correctness/security process**

Desktop Rust unit tests could exist and regress without failing the normal PR qualification matrix.

**Status: FIXED.**

The main Tests workflow now includes required Windows `cargo test --locked` desktop qualification. CI generates the Tauri native test icons and stages the expected sidecar path before running Rust tests. Both qualified Dev candidacy and Windows Dev packaging depend on that job. A source-contract test prevents silent removal of the gate.

The first attempt usefully exposed missing generated icons. The corrected gate passed #2560 and #2562 and continues to run in later matrices.

### D0-012: desktop webview could retain unrelated top-level web navigation

**Severity: High, desktop trust-boundary integrity**

The Tauri shell had no remote capability grant, but unrelated HTTP/HTTPS top-level navigation could remain inside the address-bar-less primary webview after startup.

**Status: FIXED.**

The desktop now pins a trusted InfoMancer origin after the selected server finishes loading. Same-origin navigation remains in the webview. Unrelated HTTP/HTTPS navigation after trust is established is opened in the system browser instead. Unsafe schemes and credentialed URLs fail closed. Local bootstrap rejects cross-origin redirects, while remote bootstrap may traverse HTTPS identity-provider redirects before the selected InfoMancer origin becomes trusted. A safe same-host HTTP-to-HTTPS upgrade can re-pin the origin.

Rust tests cover launcher behavior, trust establishment, local/remote bootstrap, cross-origin externalization, upgrade behavior, unsafe schemes, TVDB externalization, and the dark bootstrap bridge. The combined change passed the full matrix in #2562.

### D0-013: browser acceptance dependencies were not strictly lockfile-reproducible

**Severity: Medium, test/supply-chain reproducibility**

The E2E project declared an exact direct Playwright version but had no npm lockfile and the workflow used `npm install --ignore-scripts`, allowing transitive resolution to float.

**Status: FIXED.**

An npm-generated lockfile is now committed. Browser acceptance uses `npm ci --ignore-scripts`, and the supply-chain tests require both the lockfile and the strict CI command. A later synchronization improvement made the deep acceptance test poll the actual Library page for bounded visibility rather than assuming one render would immediately expose the seeded catalog row.

The locked E2E path is included in later full green matrices, including #2588.

## Additional review dispositions

### Outbound HTTP and SSRF review

The reviewed server-side provider paths do not expose ordinary user-controlled arbitrary fetches:

- TVDB uses its fixed HTTPS API host and explicit timeouts.
- IMDb dataset sync uses the fixed IMDb dataset host with a timeout.
- update-channel fetching is hardened as described in D0-009.
- host-updater health checks are operator CLI configuration and default to loopback.
- external search provider URL templates create browser links rather than server-side fetches.
- no separate arbitrary server-side poster/artwork downloader was found in the reviewed path.

The fixed GitHub releases check in `app/main.py` is an availability/robustness follow-up because its response-size behavior can be bounded further, but it is not an SSRF finding.

### Authentication session rotation

Password changes already revoke other sessions. The current browser session remains valid rather than being rotated after the password update. A copied current-session token would therefore remain usable until its normal expiration/revocation boundary.

This is a useful hardening improvement, but Cycle 0D currently classifies it as follow-up work rather than a blocker because password-change revocation is already present for other sessions and account-reset/recovery flows have their own stronger invalidation behavior. A future change should revoke all old session material, mint a fresh current-session token, and set a replacement cookie atomically.

### CSP fallback

Template-rendered pages use request-local CSP nonces. Middleware still has a legacy `script-src 'unsafe-inline'` fallback for responses that did not render through the nonce-aware template path. The audit did not establish that this creates a reachable executable-inline-script path on sensitive non-template responses. It remains defense-in-depth cleanup, not a confirmed exploit.

### Workflow permissions outside the main Tests workflow

The main qualification workflow uses constrained default permission and pinned actions. Some older auxiliary workflows still carry write permission because they publish, clean, or capture historical release assets. Those should be reviewed or retired as the 0.9 release workflow is consolidated, but no evidence was found that they are currently part of untrusted PR execution with signing secrets.

### Promotion-token scope

Further splitting publication into a read-only qualification stage and a minimal write-only promotion stage would reduce token exposure. That is worthwhile process hardening, but it is a workflow architecture improvement rather than an established release blocker after the publisher contract fixes.

## Migration classification rule for Cycle 1+

Compatibility class and downgrade policy are separate decisions:

- **additive**: passive schema/index additions or compatible data population that older code can safely ignore and that does not alter future-write semantics;
- **behavioral**: triggers, workflow-visible defaults/backfills, automatic receipts/state transitions, or other changes that alter future database behavior while preserving explicitly declared compatibility;
- **breaking**: a migration whose resulting database cannot safely preserve the declared older reader/writer contract and therefore requires a stricter minimum schema, read-only downgrade, or restore-required policy.

A data backfill is not automatically behavioral. Classification describes the resulting database contract. Migration 18 and later must state both semantic class and downgrade policy deliberately.

## Remaining work before Cycle 0D can be considered complete

1. Obtain a full green Tests run on the exact final audit head after the last rename error-contract repair and documentation refresh.
2. Refresh PR #83 with the exact final commit and qualification run.
3. Perform one final read-only PR diff review for accidental helper files, unrelated changes, or stale claims.
4. Leave PR #83 in draft until the independent review is complete.
5. Configure branch/ruleset protection for `testing/0.9-alpha` as a repository-owner action.

## Non-blocking follow-up for later cycles

- Continue incremental replacement of `LiveRef` read-through dependencies and shrink `app/main.py`.
- Rotate the current session token after password change.
- Remove or tighten the CSP `unsafe-inline` fallback where practical.
- Bound the fixed GitHub releases response in `app/main.py`.
- Review and retire stale write-capable auxiliary workflows.
- Consider platform-specific no-replace filesystem primitives if Windows junction/case-insensitive edge behavior requires stronger guarantees.
- Separate signed publication into narrower qualification and promotion permission stages if release automation grows more complex.

Cycle 0D should not expand into broad architecture redesign unless the final independent review finds a concrete security, correctness, concurrency, data-loss, compatibility, or supply-chain blocker.