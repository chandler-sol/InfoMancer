# Release review checklist

## Installation and upgrades

- Test a clean installation on Windows, macOS, Ubuntu/Linux Mint, and a
  headless Debian-family server.
- Test paths with spaces, Unicode, apostrophes, long names, and non-English
  characters.
- Test local disks, removable disks, offline disks, NFS mounts, SMB/UNC shares,
  and read-only media.
- Test upgrade from every supported database version and restoration from a
  backup.
- Confirm uninstall never touches media and clearly explains retained data.

## Filesystem safety

- Re-test every rename workflow against collisions, case-only renames,
  permissions failures, disconnected storage, symlinks, and concurrent scans.
- Add a global read-only mode for cautious first-time installations.
- Keep previews and audit logs for every filesystem-changing action.
- Test Windows reserved filenames and path-length boundaries.

## Security and privacy

- Complete an authentication, authorization, CSRF, session, invitation, and
  reverse-proxy review.
- Add login rate-limit and recovery-flow tests.
- Confirm Members cannot reach Librarian endpoints by direct URL or API call.
- Define a supported remote-access model and never publish the origin port by
  default.
- Add dependency vulnerability scanning and secret scanning.
- Review logs and exports for API keys, tokens, email addresses, and sensitive
  media paths.
- Publish a privacy statement explaining that catalogs remain local and naming
  every external metadata/search service contacted.

## Data durability

- Provide an in-app backup and restore workflow, not only data export.
- Test interrupted writes, disk-full conditions, corrupted databases, and WAL
  recovery.
- Define retention for logs, announcements, sessions, cached images, and
  metadata.
- Add migration rollback or documented restore instructions.

## Performance

- Establish test libraries at 1,000, 10,000, 50,000, and 100,000 files.
- Measure initial scan, incremental scan, media inspection, matching, search,
  library rendering, and backup duration.
- Limit background concurrency so scans do not overwhelm NAS storage or API
  providers.
- Test low-memory systems and slow network mounts.

## Accessibility and interface quality

- Complete keyboard-only and screen-reader passes.
- Verify visible focus, contrast, reduced-motion behavior, zoom to 200%, and
  touch targets.
- Test phone, tablet, laptop, ultrawide, and long localized text.
- Standardize confirmation, progress, empty, success, and plain-language error
  states.
- Ensure every destructive or filesystem-changing action states its scope.

## Provider and legal review

- Choose and publish the InfoMancer software license before a public release.
- Confirm TheTVDB, IMDb, artwork, and any search-provider usage and attribution
  comply with their current terms.
- Review FFmpeg/FFprobe build configuration and license obligations before
  bundling binaries in native installers.
- Add third-party notices and dependency licenses.
- Define a policy for user-supplied integrations and copyrighted content.

## Support and operations

- Add an About/version page value that exactly identifies the installed build.
- Produce a one-click diagnostic bundle with automatic secret redaction.
- Write troubleshooting articles for inaccessible storage, permissions,
  metadata authentication, unavailable providers, database recovery, and
  reverse proxies.
- Define supported operating-system versions, architectures, and upgrade
  periods.
- Create an issue template that requests version, platform, deployment type,
  reproduction steps, and sanitized logs.

## Release engineering

- Put the project under version control and protect the release branch.
- Run unit, integration, template, migration, and installer tests in CI.
- Build Windows, macOS, and Linux artifacts on their native runners.
- Sign installers, publish SHA-256 checksums, and retain build provenance.
- Test release candidates on clean machines before promotion.
- Document rollback and emergency security-release procedures.
