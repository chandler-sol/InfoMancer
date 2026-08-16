# InfoMancer 0.8 release gate

This document is the promotion gate for the 0.8 line. It is no longer a feature wish list.
A checked item means the capability is implemented or covered by repeatable automation. An
unchecked item remains a real release task, usually manual qualification, platform testing,
legal review, signing, or measured performance work.

`testing/0.8-alpha` may continue to move while these gates are open. Do not promote it to a
public-release-quality `main` build until every **Required before promotion** item is closed or
explicitly deferred in release notes with an owner and reason.

## Recovery and data durability

- [x] SQLite backup creation and validation are available in-app.
- [x] Raw database restore validates the candidate and creates a safety backup first.
- [x] Portable `.infomancer-backup` packages contain a consistent database snapshot, collection
  artwork, a versioned manifest, sizes, and SHA-256 checksums.
- [x] Portable packages explicitly exclude media files, provider credentials, provider-secret
  encryption keys, deployment environment files, binaries, and caches.
- [x] Portable recovery supports upload, verify/preview, pre-restore safety package creation,
  staged database + artwork restore, rollback on commit failure, and restart.
- [x] The Windows uninstall recovery package uses the same restorable portable format.
- [ ] Run the data-durability torture matrix in `docs/QA_0_8.md`: process termination mid-write,
  disk-full/fault injection, WAL recovery, corrupted copies, interrupted backup, interrupted
  portable restore, and older supported schema restoration.
- [ ] Record recovery results on a clean reinstall, including provider re-authentication and
  reattachment of existing media paths/shares.
- [ ] Document and test migration rollback for every database version declared supported by 0.8.

## Filesystem safety

- [x] Read-Only, Standard, and Lockdown file-protection modes exist.
- [x] Rename/organization workflows are preview-first and revalidate destinations before apply.
- [x] Supported filesystem changes record operation history and guarded Undo refuses drifted state.
- [x] Existing destinations block supported rename/move operations instead of being overwritten.
- [ ] Run the filesystem torture matrix in `docs/QA_0_8.md` on Linux and Windows: collisions,
  disconnected NAS/SMB/NFS storage, permission loss during commit, symlinks, case-only renames,
  Windows reserved names, long paths, concurrent scans, and rollback/Undo drift.
- [ ] Repeat the matrix against at least one real NAS share, not only temporary local filesystems.
- [ ] Confirm uninstall and updater cleanup never traverse into user media roots.

## Security and privacy

- [x] Local authentication uses Argon2id password hashes and revocable opaque sessions.
- [x] State-changing requests are CSRF protected and public login errors are generic.
- [x] Login throttling/lockout, password/session revocation, invitation/recovery expiry, and role
  authorization have automated coverage.
- [x] The supported remote-access model keeps the origin loopback/private and documents
  authenticated reverse proxy/VPN use.
- [x] Proxy/host/request handling, secure response headers, non-root container execution, and
  least-privilege defaults are implemented for the supported deployment path.
- [x] CI runs dependency auditing and immutable-reference supply-chain checks.
- [ ] Run a final 0.8 authorization matrix proving Members cannot reach Librarian UI/API routes by
  direct URL, crafted form request, or API request.
- [ ] Run secret/log/export review with realistic TVDB credentials, session cookies, recovery
  tokens, email addresses, and media paths. No raw secrets may appear in diagnostics or logs.
- [ ] Publish the privacy statement describing local catalog storage and every external service
  InfoMancer can contact.
- [ ] Enable/verify repository secret scanning and document the response procedure for a leaked key.

## Performance and large libraries

- [x] Background scan, metadata, fingerprint, and review work has bounded/constrained execution
  rather than unbounded fan-out.
- [x] A repeatable synthetic benchmark harness is tracked in `scripts/benchmark_library.py`.
- [ ] Record benchmark results for approximately 1k, 10k, 50k, and 100k files.
- [ ] Measure initial/incremental scan, inspector/detail queries, search, Review, rendering,
  fingerprint selection/work queues, and backup/recovery-package creation.
- [ ] Set release budgets from those results and investigate regressions beyond the agreed budget.
- [ ] Qualify one low-memory machine and one slow network-mounted library.

## Accessibility and interface quality

- [x] Reduced-motion handling exists for Workspace motion and newly added interactive detail UI.
- [x] Core dialogs, inspectors, menus, and major controls expose keyboard/focus semantics rather
  than mouse-only click targets.
- [ ] Complete the manual accessibility/responsive matrix in `docs/QA_0_8.md` using keyboard only,
  200% zoom, visible focus, screen reader, reduced motion, and touch input.
- [ ] Test phone, tablet, common laptop, 1440p/4K desktop, and ultrawide layouts with long metadata,
  long paths, and localized-length text.
- [ ] Verify every destructive/filesystem-changing action states scope, consequence, and recovery
  path in plain language.
- [ ] Resolve every horizontal page-level overflow found by the matrix. Component-internal
  scrolling is allowed only where deliberately designed and keyboard accessible.

## Installation, upgrades, and supported platforms

- [ ] Clean-install Docker deployment on supported Windows, macOS, Ubuntu/Linux Mint, and headless
  Debian-family hosts.
- [ ] Test paths containing spaces, Unicode, apostrophes, long names, and non-English characters.
- [ ] Test local, removable, offline, read-only, NFS, SMB, and Windows UNC sources.
- [ ] Test upgrade from every database version we claim 0.8 can upgrade from.
- [ ] Define exact supported OS versions, architectures, Docker versions, and native-Windows
  requirements in installation docs.
- [ ] Verify the native Windows install, update, recovery-package creation, uninstall, clean
  reinstall, and restore cycle on a clean VM.

## Provider, licensing, and privacy review

- [ ] Choose and publish the InfoMancer software license.
- [ ] Publish third-party notices and dependency-license information.
- [ ] Review the exact FFmpeg/FFprobe build/distribution configuration and document its licensing
  obligations before bundling native binaries.
- [ ] Confirm current TheTVDB/IMDb/artwork/search-provider usage and attribution against their
  applicable terms.
- [ ] Publish the privacy statement and data-flow summary.
- [ ] Document the user-supplied integration/content responsibility boundary.

## Release engineering

- [x] Source is version controlled and CI runs unit/integration/template validation on Ubuntu,
  Windows, and macOS with Python 3.13.
- [x] CI runs dependency audit and supply-chain pinning checks.
- [x] Native Windows installer/uninstaller smoke testing exists in CI.
- [x] GitHub Releases is the designed distribution/update source, avoiding a separate update server.
- [ ] Protect the release branch/tag policy used for public promotion.
- [ ] Configure production Tauri updater signing keys outside the repository and exercise signed
  update verification end to end.
- [ ] Configure Windows publisher/AuthentiCode signing for public native builds.
- [ ] Publish SHA-256 checksums and retained build provenance for release artifacts.
- [ ] Test the release candidate on clean machines after all other gates are green.
- [ ] Document and rehearse release rollback and emergency security-release procedures.

## Support and operations

- [x] Installed version/build information is exposed in the application.
- [x] One-click diagnostics exist and deliberately exclude provider secrets and the media database.
- [x] Database, settings, and portable recovery tooling are available without hand-editing SQLite.
- [ ] Finish troubleshooting coverage for inaccessible storage, permissions, provider auth,
  provider outages, database recovery, reverse proxies, and failed updates.
- [ ] Add/verify issue templates requesting version, platform, deployment type, reproduction steps,
  and sanitized logs.
- [ ] Define support/upgrade periods for 0.8 and the policy for unsupported old database versions.

## Required before promotion

The 0.8 public-release-quality promotion is blocked until all of the following are complete:

1. Filesystem and data-durability torture matrices pass on the supported platform set.
2. 1k/10k/50k/100k benchmark results are recorded and release budgets are accepted.
3. Accessibility/responsive manual QA is complete with no critical keyboard, focus, screen-reader,
   zoom, touch, or viewport defects.
4. Privacy statement, software license, third-party notices, provider review, and FFmpeg/FFprobe
   review are published.
5. Supported OS/architecture versions are explicit and clean install/upgrade/recovery cycles pass.
6. Production release/update signing, checksums, provenance, and rollback procedure are exercised.
7. Final security/authorization and secret-redaction review passes.
