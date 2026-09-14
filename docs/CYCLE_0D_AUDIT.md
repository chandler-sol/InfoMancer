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

**Current verification:** the repository-rulesets endpoint returns no configured rulesets. The connected GitHub App does not have administration permission to inspect or change classic branch protection, so this finding remains intentionally open for repository-owner configuration rather than being changed blindly from the audit branch.

### D0-002: recovery did not establish an exclusive application maintenance barrier

**Severity: High, data-integrity risk**

The baseline recovery route checked that known background jobs were idle, wrote a restore-start event, then checked again before swapping the live database/artwork. `RecoveryPackageService.restore()` subsequently replaced the live database and removed the WAL/SHM sidecars. However, the scheduler remained active and ordinary authenticated mutation requests remained accepted. The local `restore_lock` prevented a second restore only; it did not prevent another request or the scheduler from starting a new scan/hash/trash/metadata job after the final idle check.

This created a check-then-act window in which new work could begin while recovery was staging or swapping the database. A worker could hold a connection to the old database or write during the replacement boundary. Because restore is specifically a data-durability operation, relying on two status snapshots was not sufficient.

**Required remediation:** introduce an application-wide exclusive maintenance state that is established before the final quiescence check, prevents new mutation/background work from starting, drains or rejects active work, pauses the scheduler, performs the restore, and remains active until the process exits/restarts. Add an adversarial regression test that attempts to start work after recovery has entered exclusive mode.

**Audit-branch status: implemented and regression-verified.** `MaintenanceGate` now coordinates ordinary HTTP work, scheduler ticks and background-job start transitions. Recovery acquires exclusive mode before its final quiescence check, failed recovery releases the gate, and a successful database replacement remains exclusive until restart. Regression coverage includes admission blocking, health-probe availability, active-work refusal and successful-restore exclusivity. The implementation passed the Windows, macOS, Ubuntu, browser-acceptance and security/dependency jobs in PR Tests run #2513.

### D0-003: route dependency injection remains coupled to mutable `main.py` globals

**Severity: High, architecture/maintainability**

`RouteContext` and `LiveRef` deliberately proxy arbitrary names in the mutable `app.main` namespace. Router assembly then publishes handlers back into `main.py` with `globals().update()`. This makes dependency ownership implicit, allows construction order to change live dependencies, and makes security-sensitive wrappers harder to prove statically. For example, the baseline `security_hardening` router replaced `library_export_rows` through the live context during route construction.

This is not presently evidence of an exploit, but it is a major auditability problem and raises the cost/risk of every future security and Cycle 1 change.

**Required remediation:** replace arbitrary namespace access with explicit application/service dependencies in incremental slices. Preserve centralized security policy and compatibility only where a real contract requires it.

**Audit-branch status: application write channel removed; read-through cleanup remains.** `security_hardening` now returns the secured `library_export_rows` compatibility handler instead of mutating the route context. The five compatibility replacements in `final_polish` are also returned explicitly and installed by the composition root. `RouteContext.set()` has been removed entirely, and a regression contract rejects any future `ctx.set()` call under `app/routes`. TVDB credential rotation no longer replaces `tvdb`, `stored_provider_secrets`, or `provider_secret_error` globals from a request handler; the verified credentials are persisted first and then applied in place to the existing live `TVDBClient`, with its cached authentication token invalidated. PR Tests run #2537 passed Windows, macOS, Ubuntu, browser acceptance, and security/dependency audit on the combined change. `LiveRef` read-through dependencies and compatibility publication through `globals().update()` still remain for later incremental cleanup.

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

**Audit-branch status: remediation implemented and route-regression verified.** `app/routes/__init__.py` explicitly suppresses shadowed legacy registrations while keeping their handler aliases available. `tests/test_route_contract.py` requires global method/path uniqueness and pins all 12 intended owners. `tests/test_route_authorization.py` refuses to inspect an ambiguous path instead of silently accepting the first match. Older tests that encoded router source-order requirements were converted to behavior/registration contracts. The route changes passed the Windows, macOS, Ubuntu and browser jobs in PR Tests run #2523. That workflow's security/dependency job later failed only because RustSec published the new `rustls` advisory recorded as D0-006.

### D0-005: migration compatibility declarations needed semantic re-audit

**Severity: Medium, downgrade-contract correctness**

All baseline migrations 1 through 17 were declared through `additive_migration()` with a compatible downgrade policy. Migration 17 does not only add passive schema. It backfills announcement receipts and installs triggers that alter behavior on future user and announcement inserts. The compatibility framework already has `behavioral` and `breaking` classifications, so calling every migration additive weakened the meaning of the ledger even though the tested Dev/Beta round trip remained safe.

**Required remediation:** review every migration against the documented reader/writer/downgrade semantics and correct classifications without rewriting historical installed snapshots silently. Define the rule Cycle 1 must follow before Migration 18 is authored.

**Audit-branch status: implemented and regression-verified.** Migration 17 now uses an explicit `behavioral_migration()` declaration while retaining schema-1 reader/writer compatibility and the compatible downgrade policy. Fresh installations record the corrected semantic class. Existing installations keep the compatibility snapshot they recorded when Migration 17 originally ran because the ledger remains append-only through `INSERT OR IGNORE` semantics. Tests cover both fresh classification and non-rewrite of historical snapshots. The change passed the full PR Tests run #2513 matrix.

### D0-006: desktop TLS dependency acquired a newly published RustSec advisory

**Severity: Medium, desktop TLS/supply-chain**

On September 14, 2026, RustSec published `RUSTSEC-2026-0285`, "TLS 1.3 handshake messages incorrectly accepted across encryption level boundaries." The frozen desktop lockfile resolved `rustls` 0.23.43, while the advisory identifies 0.23.45 as the first fixed release. This advisory did not exist during the frozen baseline qualification, but it correctly caused the audit branch's security/dependency gate to fail as soon as the advisory database learned about it.

**Required remediation:** update the locked desktop dependency to a fixed `rustls` release without suppressing the advisory or weakening `cargo audit`, then rerun the normal qualification matrix.

**Audit-branch status: implemented and regression-verified.** Cargo generated the lockfile update to `rustls` 0.23.45. The same resolution corrected stale root-package lock metadata from `infomancer-desktop` 0.8.1-beta.1 to 0.8.1-beta.2 and added the already-declared direct `serde_json` dependency; no unrelated transitive package versions moved. A permanent CI gate now checks `Cargo.toml`/`Cargo.lock` synchronization with Cargo's own locked metadata resolution before `cargo audit`. PR Tests runs #2537, #2539, #2541, and #2543 all passed the security/dependency audit with the fixed lockfile.

### D0-007: configured provider-secret encryption used a fast password derivation

**Severity: Medium, secret-at-rest hardening**

When `INFOMANCER_SECRET` was configured, the baseline `ProviderSecretStore` hashed the supplied string once with SHA-256 and used that digest directly as Fernet key material. This is sound when the configured value is truly random high-entropy key material, but configuration accepted any non-empty string. If an operator chose a human-memorable secret and an attacker later obtained `provider-secrets.enc`, the fast derivation unnecessarily reduced the cost of offline guessing.

The existing raw Fernet file also carried no format/KDF version, so simply changing derivation would have made already-saved TVDB/provider credentials unreadable. A second compatibility case existed for installations that initially used the generated local key and later added `INFOMANCER_SECRET`.

**Required remediation:** introduce an explicitly versioned encrypted format, use a salted password-hardening KDF for configured application secrets, preserve the generated-local-key mode, and retain safe readers for both legacy ciphertext formats so upgrades do not require credential resets.

**Audit-branch status: implemented and regression-verified.** New configured-secret writes use a version-2 provider-secret envelope with a random 16-byte salt and Scrypt-derived Fernet key (`N=2^15`, `r=8`, `p=1`). Local-key installations use the same versioned envelope with an explicit `local_key` source. Legacy direct-SHA-256 application-secret ciphertext and legacy raw local-key ciphertext remain readable. A successful subsequent write migrates legacy data to the current envelope, including the case where `INFOMANCER_SECRET` is added after a local-key installation. Wrong secrets and unknown future envelope versions fail closed. Tests cover every compatibility path, and PR Tests run #2539 passed Windows, macOS, Ubuntu, browser acceptance, and security/dependency audit.

### D0-008: directory creation could invalidate filesystem containment checks before a media move

**Severity: Medium, filesystem-integrity**

Most media-mutation paths already resolve and validate cataloged paths against configured media roots. Two paths still had a deterministic post-validation gap. `SeasonFolderService.apply()` validated the proposed season destination, then created the missing `Season NN` directory and moved the episode without resolving containment again. `DuplicateTrashService.restore()` validated the original destination, then created a missing destination parent and restored the trashed file without re-resolving that parent. On storage where an attacker or competing process can replace the just-created directory entry with a symlink or Windows junction, the subsequent move could be redirected outside the configured library boundary.

**Required remediation:** after any directory creation that occurs between preview/validation and mutation, resolve the live source and destination again against their authoritative roots, then recheck source presence and destination collision immediately before the move. Add adversarial tests that replace the newly created directory with an outside symlink and prove the file remains at its safe source.

**Audit-branch status: confirmed manifestations implemented and regression-verified; broader mutation review remains open.** Season-folder apply now revalidates the show folder, source file, and live season destination after folder creation and rechecks collision immediately before `rename()`. Managed-Trash restore recomputes the live managed-Trash boundary, revalidates source and destination after destination-parent creation, then rechecks collision and source presence before `shutil.move()`. The regression tests perform the actual directory-to-symlink substitution on runners that support directory symlinks and require fail-closed behavior with the outside directory untouched. The season-folder fix passed the full PR Tests run #2541 matrix; the combined season-folder and managed-Trash restore fixes passed the full #2543 matrix.

A residual pathname race can still exist in the very small interval between the final containment/collision check and the OS rename operation. Eliminating that class completely and portably would require descriptor-based or platform-specific no-replace primitives rather than additional `pathlib` checks. The remaining rename/undo paths therefore still need review, but the code no longer creates a new unchecked directory boundary itself immediately before these two mutations.

### Migration classification rule for Cycle 1+

Compatibility class and downgrade policy are separate decisions:

- **additive**: passive schema/index additions or compatible data population that older code can safely ignore and that does not alter the semantics of future writes;
- **behavioral**: triggers, workflow-visible defaults/backfills, automatic receipts/state transitions or other changes that alter future database behavior while remaining compatible with explicitly declared older readers/writers;
- **breaking**: a migration whose resulting database cannot safely preserve the declared older reader/writer contract and therefore needs a stricter minimum schema, read-only downgrade or restore-required policy.

A data backfill is not automatically behavioral. The classification is about the resulting database contract. Migration 17 is behavioral because its triggers continue changing future insert behavior after the migration completes. Migration 18 and later must state both semantic class and downgrade contract deliberately rather than inheriting a convenient default.

## Open review items, not yet findings

These remain hypotheses until the relevant paths/tests are fully inspected:

- middleware CSP has a legacy `script-src 'unsafe-inline'` fallback when no request nonce exists; inventory non-template HTML responses before assigning severity;
- remaining filesystem mutations still use pathname-based revalidation. Review rename/undo paths, Windows junction and UNC behavior, case-insensitive collisions, and whether any platform-specific no-replace primitive is justified before assigning another finding;
- recovery update-manifest base URL can be operator-configured. It is not ordinary user input, but redirect/private-network behavior should still be documented as a deployment trust boundary;
- session rotation after password change should be reviewed. Other sessions are revoked today, while the current session remains valid;
- E2E CI uses `npm install --ignore-scripts` rather than `npm ci`; verify lockfile/reproducibility behavior before changing it;
- all remaining workflows still need an action-pin, permission, secret-flow, and artifact-retention pass;
- all outbound provider/image/download paths still need explicit timeout, redirect, content-size, and SSRF review;
- provider-secret temporary-file creation and other secret-at-rest filesystem writes still need the same local symlink/collision review applied to media mutation paths.

## Next audit slices

1. Finish the remaining filesystem mutation/undo review, including Windows junction/UNC and case-insensitive behavior.
2. Complete outbound HTTP/SSRF/provider and update-manifest trust-boundary review.
3. Complete Tauri IPC/navigation/capability and installer review.
4. Review every remaining GitHub Actions workflow, lockfile, permission, secret flow, and artifact-retention policy.
5. Continue replacing `LiveRef` read-through dependencies and review auth/CSRF/effect ownership now that the route-context write channel is gone.
6. Review logging, diagnostics, exports, and remaining secret-at-rest behavior.
7. Only after security/data-loss findings are dispositioned, begin incremental `main.py` decomposition.
