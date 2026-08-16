# InfoMancer Feature Catalog

This document is the long-form inventory of user-facing and operator-facing capabilities in the current `testing/0.8-alpha` line. It is intentionally more exhaustive than the README.

Status key:

- **Implemented**: present in the current alpha and covered by the application or tests.
- **Alpha / evolving**: present and usable, but still being refined before the next stable release.
- **Planned**: part of the current roadmap, but not complete enough to call implemented.

## Product model

- **Implemented** Local-first media catalog backed by SQLite.
- **Implemented** Self-hosted web application with no mandatory InfoMancer cloud account.
- **Implemented** Movie and TV cataloging across multiple storage roots.
- **Implemented** Docker deployment on Windows, macOS, and Linux.
- **Alpha / evolving** Native Windows desktop application built around the same local InfoMancer core.
- **Implemented** Loopback-first deployment model for safer local installations.
- **Implemented** Optional authenticated remote access through Cloudflare Access and Cloudflare Tunnel.
- **Implemented** Server-rendered FastAPI + Jinja application with progressive enhancement rather than a client-only SPA.

## Workspace and navigation

- **Implemented** Persistent Workspace shell with primary Dashboard, Library, Review, Sources, and Activity domains.
- **Implemented** Collapsible secondary navigation groups.
- **Implemented** Compact/collapsed sidebar mode.
- **Implemented** Resizable desktop sidebar.
- **Implemented** Global library search in the application header.
- **Implemented** Recent-search access from the global search control.
- **Implemented** Background-task status widget.
- **Implemented** Announcement indicator and unread state.
- **Implemented** Right-side Library Inspector that preserves the current working view.
- **Implemented** Single-click inspect behavior in supported Library surfaces.
- **Implemented** Full title details on double click or Enter where supported.
- **Implemented** URL-backed inspector state so browser history can restore inspected titles.
- **Implemented** Contextual bulk-selection toolbar.
- **Implemented** Ctrl/Cmd-click multi-selection.
- **Implemented** Shift-click range selection.
- **Implemented** Keyboard selection and details navigation.
- **Implemented** Reusable drawers for medium-depth workflows.
- **Implemented** Reusable confirmation dialogs.
- **Implemented** Toast notifications.
- **Implemented** Context menus and action popovers.
- **Implemented** Ctrl/Cmd+K command palette.
- **Implemented** Same-origin partial/AJAX interactions with server-rendered fallbacks.

## Dashboard

- **Implemented** Library summary and status overview.
- **Implemented** Background activity visibility.
- **Implemented** Pinned Saved Views.
- **Implemented** Setup and empty-library guidance.
- **Implemented** User-specific home layout behavior.
- **Implemented** Official and local announcement surfaces.

## Sources and storage roots

- **Implemented** Multiple Movie roots.
- **Implemented** Multiple TV roots.
- **Implemented** Native Windows drive paths.
- **Implemented** UNC/network-share paths when running natively with access to them.
- **Implemented** Docker media-path mapping.
- **Implemented** Source labels.
- **Implemented** Source type assignment: Movie or TV Shows.
- **Implemented** Source enable/disable state.
- **Implemented** Source connection checks.
- **Implemented** Source last-seen and last-scan health state.
- **Implemented** Source rename/edit-name workflow.
- **Implemented** Source removal from the catalog without deleting media.
- **Implemented** Folder browser for adding sources.
- **Implemented** Configurable allowed browse roots.
- **Implemented** Source-boundary enforcement for filesystem mutations.
- **Implemented** Scan one source.
- **Implemented** Scan all sources.
- **Implemented** Targeted title/series rescans.
- **Implemented** Recursive discovery of supported video formats.
- **Implemented** Source-level quality expectations used by Media Intelligence Engine analysis.

## Scanning and file discovery

- **Implemented** Non-destructive catalog scans.
- **Implemented** Reconciliation of files that have appeared or disappeared since the previous scan.
- **Implemented** Movie title/year parsing from common folder and filename layouts.
- **Implemented** `SxxExx` TV episode parsing.
- **Implemented** Continuous multi-episode forms such as `S02E04-E05`.
- **Implemented** Preservation of original filenames for later restoration.
- **Implemented** Cataloged file size and path information.
- **Implemented** New-media intake/background processing.
- **Implemented** Scan progress reporting.
- **Implemented** Failure-safe scan behavior that does not purge a source after a failed scan.

## Library browsing and search

- **Implemented** Unified Library view.
- **Implemented** Dedicated Movies view.
- **Implemented** Dedicated TV Shows view.
- **Implemented** SQLite-backed title and filename search.
- **Implemented** Filtering by media kind.
- **Implemented** Filtering by source.
- **Implemented** Filtering by metadata fields such as genre/title type where available.
- **Implemented** Filtering and sorting states that can be saved as Saved Views.
- **Implemented** Cover/poster grid presentation.
- **Implemented** Table/list presentation.
- **Implemented** Cover-size preference.
- **Implemented** Per-title detail pages.
- **Implemented** Rich movie hero with poster, identity, synopsis, cast/crew, provider IDs, and technical summary.
- **Implemented** TV detail pages with season grouping.
- **Implemented** Collapsible seasons on full TV title pages.
- **Implemented** Installation-wide default season display setting.
- **Implemented** Collapsed-by-default season display option.
- **Implemented** Expand all / Collapse all season controls.
- **Implemented** Season filtering within a TV title.
- **Implemented** Copyable On Disk directory information.
- **Implemented** Containerized per-file On Disk cards for physical media files.

## Saved Views

- **Implemented** Save the current Library filter/sort state under a name.
- **Implemented** Saved Views for Library, Movies, and TV Shows contexts.
- **Implemented** Personal Saved Views scoped to the signed-in account.
- **Implemented** Rename Saved Views.
- **Implemented** Delete Saved Views.
- **Implemented** Pin/unpin Saved Views.
- **Implemented** Pinned views on the Library surface.
- **Implemented** Pinned views on Dashboard.
- **Implemented** Query normalization so arbitrary unsupported parameters are not persisted.
- **Implemented** Saved-view caps to keep navigation manageable.

## Title organization

- **Implemented** Favorites.
- **Implemented** Personal ratings.
- **Implemented** User-created tags.
- **Implemented** Bulk tagging/organization.
- **Implemented** Collections.
- **Implemented** Collection artwork.
- **Implemented** Custom Libraries.
- **Implemented** Add/remove titles from Custom Libraries.
- **Implemented** Smart Collection rules and previews.
- **Implemented** Per-user custom sort titles.
- **Implemented** Bulk sequence/sort-title generation.
- **Implemented** Custom ordering state.

## Metadata and matching

- **Implemented** TheTVDB v4 integration.
- **Implemented** TV series matching.
- **Implemented** Movie matching through TVDB records.
- **Implemented** Saved TVDB series IDs.
- **Implemented** Saved TVDB movie IDs.
- **Implemented** Saved TMDB IDs when available.
- **Implemented** Saved IMDb IDs when available.
- **Implemented** Plex-compatible provider identifiers in naming workflows.
- **Implemented** English title preference when an English translation is available.
- **Implemented** English overview/synopsis preference when an English translation is available.
- **Implemented** Movie synopsis/overview storage.
- **Implemented** TV synopsis/overview storage.
- **Implemented** Explicit metadata refresh rather than hidden provider work on title-detail GET requests.
- **Implemented** Metadata-refresh recovery for legacy movie records using stored TMDB/IMDb identity as a safety anchor.
- **Implemented** Poster/cover storage and display.
- **Implemented** Cover-change workflow for matched titles.
- **Implemented** Match, Change Match, Fix Match, and Unmatch workflows.
- **Implemented** Bulk matching/review flows.
- **Implemented** Match confidence evidence used by MIE.
- **Implemented** Configurable external search provider label and URL template.
- **Implemented** External title/episode search links.
- **Implemented** Direct provider links where IDs exist.

## IMDb metadata

- **Implemented** IMDb title type.
- **Implemented** IMDb genres.
- **Implemented** IMDb rating.
- **Implemented** IMDb vote count.
- **Implemented** Title credits.
- **Implemented** Director credits.
- **Implemented** Writer credits.
- **Implemented** Top-billed cast.
- **Implemented** Episode-level director/writer credits when available.
- **Implemented** Incremental IMDb metadata refresh workflows.
- **Implemented** Search/filter use of stored IMDb metadata.
- **Implemented** Local-library people searches from credit links.
- **Implemented** Local-library person preview/popover behavior in Workspace surfaces.

## TV completeness and episode intelligence

- **Implemented** Expected-episode import from TVDB for matched series.
- **Implemented** Missing aired regular-episode detection.
- **Implemented** Future episodes excluded from the default missing report.
- **Implemented** Season-zero specials excluded from the default gap report.
- **Implemented** Missing-episode counts in review surfaces.
- **Implemented** Missing-episode external search links.
- **Implemented** Multi-episode-file awareness in completeness checks.
- **Implemented** Per-season episode grouping.
- **Implemented** Episode names on title details.
- **Implemented** Episode TVDB links when available.

## Rename and organization workflows

- **Implemented** Preview-first filesystem changes.
- **Implemented** Episode rename proposals.
- **Implemented** Movie rename proposals.
- **Implemented** Series/show-folder rename proposals.
- **Implemented** Plex-compatible TV folder naming such as `Show (Start - End) {tvdb-12345}`.
- **Implemented** Episode naming such as `Show - S01E01 - Episode Name.ext`.
- **Implemented** Restore Original Filenames workflow.
- **Implemented** Destination-collision refusal.
- **Implemented** Configured-source-boundary validation before mutation.
- **Implemented** Revalidation immediately before a persisted rename proposal is applied.
- **Implemented** Persisted global rename proposals instead of rescanning the filesystem on every Review page load.
- **Implemented** Rename proposal states including ready/blocked/dismissed/resolved behavior.
- **Implemented** Global Renames Review bucket.
- **Implemented** Explicit refresh of rename proposal snapshots.

## Season-folder organization

- **Implemented** Preview-first organization into season directories.
- **Implemented** `Season 01`, `Season 02`, and equivalent folder naming.
- **Implemented** Season zero mapped to `Specials`.
- **Implemented** Unparsed files left untouched.
- **Implemented** Destination collision blocking.
- **Implemented** Source/destination revalidation before moves.
- **Implemented** Catalog path updates after successful moves.
- **Implemented** Operation History entries for season-folder moves.
- **Implemented** Safe Undo support for eligible season-folder moves.
- **Implemented** Old empty source folders are not automatically deleted.

## Editions and versions

- **Implemented** Per-file edition labels.
- **Implemented** Per-file version labels.
- **Implemented** Preferred-version flag.
- **Implemented** Preview before saving edition/version identity.
- **Implemented** Typed confirmation before catalog changes in the edition/version workflow.
- **Implemented** Sibling edition/version awareness in duplicate decisions.
- **Implemented** Inspector and title-detail display of edition/version state.

## Technical media inspection

- **Implemented** FFprobe integration.
- **Implemented** Runtime collection.
- **Implemented** Width/height collection.
- **Implemented** Resolution presentation.
- **Implemented** Video codec collection.
- **Implemented** Audio codec collection.
- **Implemented** Audio-channel collection.
- **Implemented** Bitrate collection.
- **Implemented** Container-format collection.
- **Implemented** HDR/dynamic-range collection.
- **Implemented** Media inspection timestamps.
- **Implemented** User-facing inspection errors.
- **Implemented** Reinspection workflows.
- **Implemented** Title-level technical fact rail.
- **Implemented** Library/Inspector technical summaries.

## Media Intelligence Engine (MIE)

- **Implemented** Read-only analytical engine over cataloged facts.
- **Implemented** Explainable findings rather than opaque scores alone.
- **Implemented** Evidence attached to findings.
- **Implemented** Recommendations attached to findings.
- **Implemented** Critical, warning, and information severities.
- **Implemented** Health category.
- **Implemented** Identity category.
- **Implemented** Completeness category.
- **Implemented** Quality category.
- **Implemented** Freshness category.
- **Implemented** Storage category.
- **Implemented** Identity confidence scoring using provider, title, year, and catalog-placement evidence.
- **Implemented** Title/year conflict explanations.
- **Implemented** Missing provider-ID findings.
- **Implemented** Missing artwork findings.
- **Implemented** Missing credits findings.
- **Implemented** Missing TV episode metadata findings.
- **Implemented** Stale metadata findings.
- **Implemented** Unreadable-media findings based on FFprobe failures.
- **Implemented** Multi-episode-file informational findings.
- **Implemented** Missing-episode findings.
- **Implemented** Source-staleness analysis.
- **Implemented** Source quality-profile analysis.
- **Implemented** Title-level technical consistency analysis.
- **Implemented** Duplicate/storage findings integrated with duplicate analysis.
- **Implemented** Calibration values for selected thresholds/weights.
- **Implemented** Feedback reasons such as expected, incorrect, resolved elsewhere, and other.
- **Implemented** Feedback scopes for finding, title, and source.
- **Implemented** Review integration for MIE findings.
- **Implemented** MIE analysis remains non-destructive; it does not mutate media files.

## Unified Review Workspace

- **Implemented** Single Review domain for multiple decision queues.
- **Implemented** MIE findings bucket.
- **Implemented** Duplicate candidates bucket.
- **Implemented** Unmatched-media bucket.
- **Implemented** Missing-episode bucket.
- **Implemented** Metadata-issue bucket.
- **Implemented** Quality-decision bucket.
- **Implemented** Persisted rename-proposal bucket.
- **Implemented** Review filters.
- **Implemented** Review drawers/details.
- **Implemented** Server-side Librarian authorization for mutations.
- **Implemented** Read-only Review GET requests.
- **Implemented** Specialist deep pages remain available for complex workflows.

## Duplicate intelligence

- **Implemented** Duplicate candidate detection.
- **Implemented** File-hash verification before treating candidates as verified copies.
- **Implemented** Duplicate review states.
- **Implemented** Not-duplicate decisions.
- **Implemented** Edition/version-aware duplicate logic.
- **Implemented** Quality/storage recommendations for duplicate copies.
- **Implemented** Storage-recovery estimates/recommendations.
- **Implemented** Managed Trash rather than immediate permanent deletion for supported duplicate cleanup.
- **Implemented** Managed Trash preview.
- **Implemented** Managed Trash restore.
- **Implemented** Managed Trash operation-history linkage.
- **Implemented** Automatic permanent managed-trash cleanup can be paused by Lockdown Mode.

## File protection and safety modes

- **Implemented** Global Read-Only Mode.
- **Implemented** Standard Mode.
- **Implemented** Lockdown Mode.
- **Implemented** Server-side enforcement through a shared file-protection service.
- **Implemented** Read-Only Mode blocks renames, moves, restores, and permanent media deletion.
- **Implemented** Read-Only Mode still permits scanning, matching, inspection, metadata, MIE, tags, collections, and database/application maintenance.
- **Implemented** Standard Mode allows reviewed filesystem changes.
- **Implemented** Lockdown Mode preserves reviewed reversible changes but adds stronger protection around irreversible deletion.
- **Implemented** Lockdown Mode pauses automatic permanent managed-trash purging.
- **Implemented** Persistent Librarian indication when Read-Only Mode is active.
- **Implemented** Preview/review remains available even when apply is blocked by Read-Only Mode.

## Operation History and Safe Undo

- **Implemented** Durable operation ledger for supported filesystem mutations.
- **Implemented** Episode rename history.
- **Implemented** Movie rename history.
- **Implemented** Show-folder rename history.
- **Implemented** Season-folder move history.
- **Implemented** Managed Trash move history.
- **Implemented** Operation type/status filters.
- **Implemented** Librarian-only Operation History view.
- **Implemented** Safe Undo where semantics permit it.
- **Implemented** Path/state revalidation before undo.
- **Implemented** Destination collision checks before undo.
- **Implemented** Source-boundary validation before undo.
- **Implemented** Managed Trash undo delegated through guarded restore logic.
- **Implemented** Undo fails closed if filesystem/catalog state has drifted.
- **Implemented** No arbitrary shell commands are stored or replayed for undo.

## Recovery, backup, export, and maintenance

- **Implemented** Consistent SQLite database backups.
- **Implemented** Backup listing/download from Settings.
- **Implemented** Backup verification.
- **Implemented** Validated database restore workflow.
- **Implemented** Uploaded database restore size limits.
- **Implemented** SQLite integrity checks before staged restore.
- **Implemented** Portable `.infomancer-backup` recovery-package creation.
- **Implemented** Recovery package includes a consistent SQLite snapshot.
- **Implemented** Recovery package can include collection artwork.
- **Implemented** Versioned recovery manifest.
- **Implemented** SHA-256 file checksums in recovery packages.
- **Implemented** Recovery package verification after creation.
- **Implemented** Uploaded recovery-package verification without changing the live installation.
- **Implemented** Rejection of archive traversal and undeclared archive members.
- **Implemented** Rejection of duplicate archive paths.
- **Implemented** Rejection of checksum mismatches and unsupported format versions.
- **Implemented** Recovery package intentionally excludes media files.
- **Implemented** Recovery package intentionally excludes provider credentials and encryption keys.
- **Planned** Full in-app restore from a verified portable recovery package, including rollback if restore fails.
- **Implemented** CSV library export.
- **Implemented** JSON library export.
- **Implemented** XML library export.
- **Implemented** Optional per-user state in CLI exports.
- **Implemented** Settings/preferences export and import with secrets deliberately excluded.
- **Implemented** Database optimization.
- **Implemented** Database health/integrity reporting.
- **Implemented** Diagnostic bundle/log export surfaces.

## Background processing

- **Implemented** Persistent background-task coordinator.
- **Implemented** Visible task progress/status.
- **Implemented** New-media intake queue.
- **Implemented** Scheduled/automatic fingerprinting controls.
- **Implemented** Fingerprinting behavior modes.
- **Implemented** Immediate file limit per run.
- **Implemented** Daily/weekly/monthly scheduling options where configured.
- **Implemented** Configurable schedule day and start time.
- **Implemented** Disk-activity level setting.
- **Implemented** Pause fingerprinting while scans/matching/media inspection are active.
- **Implemented** Manual Run now / Pause / Resume / Cancel controls.
- **Implemented** Persisted rename-proposal generation in background rather than on every Review GET.

## Authentication and accounts

- **Implemented** Local authentication mode.
- **Implemented** Cloudflare Access authentication mode.
- **Implemented** Trusted-loopback/private auth-disabled mode for deliberate deployments.
- **Implemented** Initial Librarian setup.
- **Implemented** Librarian and Member roles.
- **Implemented** Server-side authorization for Librarian-only operations.
- **Implemented** Librarian-managed users.
- **Implemented** One-time account setup/invitation links.
- **Implemented** Invitation expiry.
- **Implemented** Invitation revocation/replacement when a new link is generated.
- **Implemented** Password recovery links from the CLI.
- **Implemented** Recovery-link expiry and one-time use.
- **Implemented** Session revocation after sensitive account recovery/password changes.
- **Implemented** Profile settings.
- **Implemented** Session management page.
- **Implemented** Password-change flow.
- **Implemented** Argon2id password hashing.
- **Implemented** Opaque random session tokens with only SHA-256 hashes stored in SQLite.
- **Implemented** HttpOnly session cookies.
- **Implemented** SameSite cookie policy.
- **Implemented** Secure-cookie support for HTTPS deployments.
- **Implemented** Per-session CSRF tokens.
- **Implemented** Pre-authentication CSRF protection for login/setup forms.
- **Implemented** Account-enumeration-resistant public login errors.
- **Implemented** Pair, identity-wide, and IP-wide login lockouts/throttling.
- **Implemented** Security event recording for important authentication events.
- **Implemented** Librarian notification when a new lockout is created.
- **Implemented** Cloudflare Access JWT validation.
- **Implemented** External identity mapping/account linking groundwork.
- **Planned** Native passkeys.
- **Planned** Application-native MFA.
- **Planned** Direct Google/Microsoft/Apple/GitHub sign-in adapters.

## Request and web security

- **Implemented** CSRF enforcement on state-changing requests.
- **Implemented** Origin checks.
- **Implemented** `Sec-Fetch-Site` cross-site rejection where applicable.
- **Implemented** Host validation support.
- **Implemented** Safe redirect/`next` validation.
- **Implemented** Bounded normal-form parsing.
- **Implemented** Request-size limits for small forms/imports.
- **Implemented** Route-specific larger limits for artwork and database restore.
- **Implemented** File-signature validation for collection artwork.
- **Implemented** JPEG/PNG/WebP artwork allowlist.
- **Implemented** SVG upload rejection for collection artwork.
- **Implemented** Security response headers including nosniff/frame/referrer/permissions/CSP protections.
- **Implemented** FastAPI interactive docs/OpenAPI routes disabled in the normal application surface.
- **Implemented** Proxy/IP handling that does not blindly trust arbitrary forwarded headers.
- **Implemented** Non-root Docker runtime user.
- **Implemented** Restricted local provider-secret key/file permissions.
- **Implemented** Recursive/event-log redaction protections for sensitive values where applicable.

## Event log, activity, and announcements

- **Implemented** Structured event log.
- **Implemented** Event severity.
- **Implemented** Event categories.
- **Implemented** Search/filter of application logs.
- **Implemented** User attribution where available.
- **Implemented** Security/authentication audit events.
- **Implemented** Activity view.
- **Implemented** Per-user unread activity/announcement state.
- **Implemented** Bundled official release announcements.
- **Implemented** Librarian-created installation announcements.
- **Implemented** Member/Librarian/everyone announcement audiences.
- **Implemented** One-time announcements.
- **Implemented** Daily recurring announcements.
- **Implemented** Weekly recurring announcements.
- **Implemented** Start/end scheduling for local announcements.
- **Implemented** Per-user delivery receipts.

## Guided setup and onboarding

- **Implemented** First-run Librarian creation.
- **Implemented** Guided Setup option.
- **Implemented** Manual Setup option.
- **Implemented** Installation-name/time-zone setup.
- **Implemented** TVDB credential entry and connection verification inside setup.
- **Implemented** Encrypted local storage of provider credentials.
- **Implemented** Movie/TV source selection from setup.
- **Implemented** Setup progress persistence.
- **Implemented** First scan handoff.
- **Implemented** Replayable guided user tour.
- **Implemented** Per-user tour completion state.
- **Implemented** Sandbox-only metadata-provider bypass for isolated testing.

## Application settings

- **Implemented** Installation name.
- **Implemented** Display time zone.
- **Implemented** Default Library view.
- **Implemented** Default cover size.
- **Implemented** Default TV season display.
- **Implemented** TVDB credential status/testing.
- **Implemented** External search provider configuration.
- **Implemented** File protection mode.
- **Implemented** Fingerprint behavior/schedule controls.
- **Implemented** Logging level/detail controls.
- **Implemented** Portable preference export/import.
- **Implemented** Database backup/restore controls.
- **Implemented** Recovery package create/verify controls.
- **Implemented** Database optimize/restart/system controls.
- **Implemented** Recent settings-change history with Librarian attribution.

## CLI

- **Implemented** `status`: catalog/source counts and state.
- **Implemented** `doctor`: database integrity, source accessibility, FFprobe, and TVDB setup checks.
- **Implemented** `scan`: one source or all sources.
- **Implemented** `inspect`: FFprobe technical media inspection.
- **Implemented** `export`: CSV, JSON, or XML library export.
- **Implemented** `logs`: read/filter/follow/export application logs.
- **Implemented** `backup`: consistent live SQLite backup.
- **Implemented** `optimize`: SQLite ANALYZE/optimize/checkpoint maintenance.
- **Implemented** `reset-librarian`: emergency Librarian password reset plus session revocation.
- **Implemented** `recovery-link`: short-lived single-use Librarian recovery link.

## Docker and deployment

- **Implemented** Dockerfile with FFmpeg/FFprobe dependencies.
- **Implemented** Docker Compose base deployment.
- **Implemented** Platform-specific media-mapping examples.
- **Implemented** Cloudflare Tunnel Compose overlay.
- **Implemented** Isolated sandbox Compose deployment.
- **Implemented** Loopback host binding by default in the recommended deployment.
- **Implemented** Non-root container user.
- **Implemented** Configurable host/user IDs for container filesystem compatibility.
- **Implemented** Secret-free example environment files.

## Isolated sandbox

- **Implemented** Separate sandbox database.
- **Implemented** Separate sandbox media directory.
- **Implemented** Separate Compose project/container/port.
- **Implemented** Blank first-run sandbox mode.
- **Implemented** Sample-data sandbox mode.
- **Implemented** Generated disposable media fixtures.
- **Implemented** Reset scripts for Windows and Linux.
- **Implemented** No production media/data mounts in the sandbox configuration.

## Native Windows application

- **Alpha / evolving** Tauri-based Windows desktop launcher.
- **Alpha / evolving** Bundled/managed InfoMancer core sidecar.
- **Alpha / evolving** Windows installer build in GitHub Actions.
- **Alpha / evolving** Clean install/uninstall CI smoke testing.
- **Alpha / evolving** Zero-residue uninstall policy for InfoMancer-owned state.
- **Alpha / evolving** Final uninstall prompt offering a verified `.infomancer-backup` before local application data is purged.
- **Alpha / evolving** User media is explicitly outside uninstall cleanup scope.
- **Alpha / evolving** Update installs bypass destructive uninstall-data cleanup.
- **Alpha / evolving** GitHub Releases-based updater architecture, so InfoMancer does not require a separately hosted update server.
- **Alpha / evolving** Tauri updater signature verification.
- **Planned** Production updater signing key configuration before promotion to `main`.
- **Planned** Windows Authenticode publisher signing for a trusted Windows publisher experience.

## Updates and release delivery

- **Implemented** Release-check support in the web application.
- **Implemented** Restricted host updater model for Docker/host-managed updates.
- **Implemented** Windows GitHub Actions build workflow.
- **Implemented** Windows GitHub Releases update-channel workflow.
- **Implemented** Rolling updater manifest design.
- **Implemented** Signed update-package verification design in the Windows client.
- **Planned** Final release-signing configuration and public-release gate.

## Testing and quality controls

- **Implemented** Cross-platform Python test matrix on Ubuntu, macOS, and Windows.
- **Implemented** Python compile validation in CI.
- **Implemented** Dependency audit job.
- **Implemented** Supply-chain tests that require pinned GitHub Actions SHAs.
- **Implemented** Authorization regression tests.
- **Implemented** Security-hardening regression tests.
- **Implemented** Workspace UI contract tests.
- **Implemented** Saved View tests.
- **Implemented** Operation History/Undo tests.
- **Implemented** Read-Only/Lockdown protection tests.
- **Implemented** Recovery package tests.
- **Implemented** Windows desktop build/install/uninstall tests.
- **Implemented** Sandbox/testing documentation.
- **Planned** Large-library benchmark qualification at progressively larger catalog sizes.
- **Planned** Filesystem failure/torture testing across NAS disconnects, permissions, collisions, case rules, long paths, and interruptions.
- **Planned** Data-durability torture testing for disk-full, interrupted writes, WAL recovery, corruption, migrations, and restore rollback.
- **Planned** Formal accessibility and responsive QA pass.

## Intentional boundaries

InfoMancer is a media catalog, intelligence, organization, and safe filesystem-management application. The current project deliberately does **not** scrape torrent-result pages, automatically acquire copyrighted media, or submit downloads to a downloader. Search-provider links are external convenience links only.

Current notable unfinished work includes full restore of a portable `.infomancer-backup`, application-native MFA/passkeys, final production updater/code signing, large-library performance qualification, failure/durability testing, accessibility QA, and final release/legal/privacy/licensing review.
