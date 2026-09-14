# Cycle 0D architecture and security audit

This document is the working evidence log for Issue #82. Cycle 0D is intentionally a hardening and behavior-preserving refactor cycle. Findings are separated from hypotheses so a concern is not treated as a defect until the code path and failure mode are established.

## Frozen baseline

- Canonical branch: `testing/0.9-alpha`
- Qualified baseline commit: `f04eb2017112a04bc9944658d8b5c4d20d32b8fa`
- Baseline Tests workflow: #2512, successful
- Qualified Dev: `0.9.0-dev.2512`, tag `desktop-dev-2512-f04eb201`
- Exact-source Beta: `0.9.0-beta.2`, tag `desktop-beta-0.9.0-beta.2-f04eb201`
- Signed downgrade proof: workflow run `34797581818`, successful
- Audit branch: `audit/0.9-cycle-0d`

The canonical baseline stays frozen while read-only review and isolated audit changes proceed on the audit branch.

## Architecture inventory, first pass

### Composition and lifecycle

`app/main.py` is still both the ASGI composition root and a large application module. Importing it currently initializes the database, creates authentication and domain services, seeds engagement data, loads provider secrets, creates a TVDB client, creates collection-art storage, builds the background coordinator, configures middleware, defines first-run/auth/account/admin routes, owns many scan/metadata helpers, and finally assembles domain routers.

The existing W1.5 extraction pattern uses `RouteContext(globals())`. Route modules obtain `LiveRef` proxies back into the mutable `app.main` namespace, and router assembly writes returned handlers back into `globals()` for compatibility. This preserved test/runtime replacement behavior during the 0.8 decomposition, but it now makes dependencies and mutation ownership implicit.

Target direction for 0D, subject to the completed inventory:

- a small application factory / ASGI assembly layer;
- explicit startup and shutdown ownership;
- centralized request/authentication/CSRF/security-header policy;
- explicit service dependencies instead of arbitrary live access to `main.py` globals;
- domain routes that do not depend on route-registration order to override older handlers;
- compatibility facades only where an actual external or regression contract requires them.

### Route ownership

The runtime route inventory found 12 duplicate method/path registrations. Focused hardening or release-polish routers were registered before broader legacy routers, so the intended handler won only because Starlette selects the first matching route. The audit branch now removes those shadowed route registrations centrally while retaining the legacy function aliases for compatibility. A regression test requires every method/path pair to have exactly one live owner and separately pins the 12 canonical owners.

### Background work

`BackgroundCoordinator` owns process-local state for scanning, metadata work, hashing, duplicate verification, media inspection, and managed-Trash cleanup, plus the scheduler and single-runtime lease. Several older entry points are still re-exported from `main.py` as globals for route compatibility.

## Security controls confirmed in first pass

The first pass found substantial existing hardening. These controls should be preserved during refactoring:

- Argon2 password hashing with an explicit cost profile and a dummy hash for missing accounts.
- Random session tokens stored as SHA-256 hashes rather than plaintext.
- Random CSRF tokens and constant-time token comparisons.
- Per-account/IP login throttling plus persistent aggregate lockouts.
- Expiring, one-time, hashed invitation tokens.
- Host-header validation and local-only redirect validation.
- Unsafe authenticated requests are globally CSRF checked. Disabled-auth local mode still requires same-origin validation and CSRF.
- Cloudflare identity validation uses signed JWT claims and does not itself grant an InfoMancer account.
- Jinja templates receive request-local CSP nonces through the hardened loader.
- Diagnostics are Librarian-only and redact paths, identities, network values, credentials, sessions, and secrets.
- Portable recovery rejects unsafe paths, duplicate/cross-platform-colliding names, encrypted members, unsupported compression, excessive expansion ratios, undeclared files, checksum mismatches, invalid database snapshots, and unsupported roles.
- Recovery extracts only after verification and rechecks size/hash while staging.
- Tauri uses an exact signed updater public key and fixed GitHub channel endpoints.
- The Tauri capability file grants the `main` window only `core:default`; there is no remote-origin capability. Current Tauri 2.11.5 is after the upstream 2.11.1 remote-origin ACL security fix.
- GitHub Actions qualification uses read-only default contents permission, immutable SHA-pinned first-party Actions in the main test workflow, dependency audits, all-platform Python tests, browser acceptance, and exact-commit signed packaging.

## Confirmed findings

### D0-001: qualified release branch is not protected

**Severity: High, supply-chain/process**

`testing/0.9-alpha` currently reports no branch protection and no required status checks. A push to this branch can enter the qualification workflow and, after gates pass, produce and advance signed Dev artifacts. The workflow gates reduce accidental bad releases, but they do not replace repository-level protection against an unauthorized or mistaken direct branch update.

**Required remediation:** configure repository rules/branch protection appropriate to the project so direct changes to qualified release branches cannot bypass the intended review/CI policy. Exact settings must be chosen with the repository's GitHub plan and owner workflow in mind. Signed publication secrets should remain unavailable to untrusted pull-request code.

### D0-002: recovery did not establish an exclusive application maintenance barrier

**Severity: High, data-integrity risk**

The baseline recovery route checked that known background jobs were idle, wrote a restore-start event, then checked again before swapping the live database/artwork. `RecoveryPackageService.restore()` subsequently replaced the live database and removed the WAL/SHM sidecars. However, the scheduler remained active and ordinary authenticated mutation requests remained accepted. The local `restore_lock` prevented a second restore only; it did not prevent another request or the scheduler from starting a new scan/hash/trash/metadata job after the final idle check.

This created a check-then-act window in which new work could begin while recovery was staging or swapping the database. A worker could hold a connection to the old database or write during the replacement boundary. Because restore is specifically a data-durability operation, relying on two status snapshots was not sufficient.

**Required remediation:** introduce an application-wide exclusive maintenance state that is established before the final quiescence check, prevents new mutation/background work from starting, drains or rejects active work, pauses the scheduler, performs the restore, and remains active until the process exits/restarts. Add an adversarial regression test that attempts to start work after recovery has entered exclusive mode.

**Audit-branch status: implemented and regression-verified.** `MaintenanceGate` now coordinates ordinary HTTP work, scheduler ticks and background-job start transitions. Recovery acquires exclusive mode before its final quiescence check, failed recovery releases the gate, and a successful database replacement remains exclusive until restart. Regression coverage includes admission blocking, health-probe availability, active-work refusal and successful-restore exclusivity. The implementation passed the Windows, macOS, Ubuntu, browser-acceptance and security/dependency jobs in PR Tests run #2513.

### D0-003: route dependency injection remains coupled to mutable `main.py` globals

**Severity: High, architecture/maintainability**

`RouteContext` and `LiveRef` deliberately proxy arbitrary names in the mutable `app.main` namespace. Router assembly then publishes handlers back into `main.py` with `globals().update()`. This makes dependency ownership implicit, allows construction order to change live dependencies, and makes security-sensitive wrappers harder to prove statically. For example, `security_hardening` replaces `library_export_rows` through the live context during route construction.

This is not presently evidence of an exploit, but it is a major auditability problem and raises the cost/risk of every future security and Cycle 1 change.

**Required remediation:** replace arbitrary namespace access with explicit application/service dependencies in incremental slices. Preserve centralized security policy and compatibility only where a real contract requires it.

### D0-004: duplicate route ownership made security behavior order-dependent

**Severity: High, security/authorization correctness**

Runtime enumeration confirmed 12 duplicate method/path registrations. Starlette resolves them by first-match order, so the intended implementation was not the only live owner. The duplicates were:

- `GET /maintenance/diagnostics`
- `GET /movies/bulk-match`
- `GET /shows/bulk-match`
- `POST /api/titles/{title_id}/favorite`
- `POST /collections/{collection_id}/delete`
- `POST /movies/bulk-match`
- `POST /roots`
- `POST /shows/bulk-match`
- `POST /titles/organize-bulk`
- `POST /titles/{title_id}/imdb-refresh`
- `POST /titles/{title_id}/media-info`
- `POST /titles/{title_id}/movie/{movie_id}`

This was more than a maintainability smell. `GET /maintenance/diagnostics` had both the privacy-sanitized security-hardening implementation and an older Settings implementation that serialized `mie.summary()` directly and retained event fields other than `user_id`. The hardened implementation won only because it was registered first. Similarly, `POST /roots` depended on focused route priority so the source-browser validation contract won over the broader Settings handler. A future router-order refactor could therefore have silently changed a security-sensitive behavior without changing the URL.

**Required remediation:** one live owner per method/path, with canonical owners tested directly. Legacy callable aliases may remain temporarily where compatibility tests or internal callers require them, but they must not register duplicate HTTP routes.

**Audit-branch status: remediation implemented, CI verification in progress.** `app/routes/__init__.py` now explicitly suppresses shadowed legacy registrations while keeping their handler aliases available. `tests/test_route_contract.py` requires global method/path uniqueness and pins all 12 intended owners. `tests/test_route_authorization.py` now also refuses to inspect an ambiguous path instead of silently accepting the first match. No production behavior is intentionally changed because each retained owner is the implementation that already won under the previous route order.

### D0-005: migration compatibility declarations needed semantic re-audit

**Severity: Medium, downgrade-contract correctness**

All baseline migrations 1 through 17 were declared through `additive_migration()` with a compatible downgrade policy. Migration 17 does not only add passive schema. It backfills announcement receipts and installs triggers that alter behavior on future user and announcement inserts. The compatibility framework already has `behavioral` and `breaking` classifications, so calling every migration additive weakened the meaning of the ledger even though the tested Dev/Beta round trip remained safe.

**Required remediation:** review every migration against the documented reader/writer/downgrade semantics and correct classifications without rewriting historical installed snapshots silently. Define the rule Cycle 1 must follow before Migration 18 is authored.

**Audit-branch status: implemented and regression-verified.** Migration 17 now uses an explicit `behavioral_migration()` declaration while retaining schema-1 reader/writer compatibility and the compatible downgrade policy. Fresh installations record the corrected semantic class. Existing installations keep the compatibility snapshot they recorded when Migration 17 originally ran because the ledger remains append-only through `INSERT OR IGNORE` semantics. Tests cover both fresh classification and non-rewrite of historical snapshots. The change passed the full PR Tests run #2513 matrix.

### Migration classification rule for Cycle 1+

Compatibility class and downgrade policy are separate decisions:

- **additive**: passive schema/index additions or compatible data population that older code can safely ignore and that does not alter the semantics of future writes;
- **behavioral**: triggers, workflow-visible defaults/backfills, automatic receipts/state transitions or other changes that alter future database behavior while remaining compatible with explicitly declared older readers/writers;
- **breaking**: a migration whose resulting database cannot safely preserve the declared older reader/writer contract and therefore needs a stricter minimum schema, read-only downgrade or restore-required policy.

A data backfill is not automatically behavioral. The classification is about the resulting database contract. Migration 17 is behavioral because its triggers continue changing future insert behavior after the migration completes. Migration 18 and later must state both semantic class and downgrade contract deliberately rather than inheriting a convenient default.

## Open review items, not yet findings

These remain hypotheses until the relevant paths/tests are fully inspected:

- middleware CSP has a legacy `script-src 'unsafe-inline'` fallback when no request nonce exists; inventory non-template HTML responses before assigning severity;
- `ProviderSecretStore` derives a Fernet key from configured `INFOMANCER_SECRET` using direct SHA-256. The documented expectation is a long random value; determine whether entropy should be enforced or a password KDF should be used;
- filesystem mutations perform path revalidation, but rename/trash operations still have unavoidable pathname TOCTOU windows. Review Windows junction, symlink, UNC and case-insensitive behavior before deciding what additional defenses are practical;
- recovery update-manifest base URL can be operator-configured. It is not ordinary user input, but redirect/private-network behavior should still be documented as a deployment trust boundary;
- session rotation after password change should be reviewed. Other sessions are revoked today, while the current session remains valid;
- E2E CI uses `npm install --ignore-scripts` rather than `npm ci`; verify lockfile/reproducibility behavior before changing it;
- all remaining workflows still need an action-pin, permission, secret-flow, and artifact-retention pass;
- all outbound provider/image/download paths still need explicit timeout, redirect, content-size, and SSRF review.

## Next audit slices

1. Finish route ownership CI and then replace mutable route dependencies in security-sensitive slices.
2. Complete filesystem mutation and portable-recovery concurrency review.
3. Complete outbound HTTP/SSRF/provider review.
4. Complete Tauri IPC/navigation/capability and installer review.
5. Review every GitHub Actions workflow and lockfile.
6. Review logging, diagnostics, exports, and secret-at-rest behavior.
7. Only after security/data-loss findings are dispositioned, begin incremental `main.py` decomposition.
