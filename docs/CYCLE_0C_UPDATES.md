# 0.9 Cycle 0C: Update Channels

Cycle 0C turns InfoMancer's existing release and updater pieces into one installation-wide update model.

## User-facing channels

### Standard
Production releases intended for normal installations. Standard is the conservative default.

### Beta
Qualified preview releases. A Beta installation may also consume a newer Standard release when one exists.

### Dev
The newest qualified development build. Dev also accepts Beta and Standard releases.

**Dev never means newest commit.** A commit becomes eligible only after the required qualification gates succeed.

## Safety rules

1. Selecting a channel does not install code.
2. Moving toward a less-stable channel requires explicit confirmation.
3. Moving toward a more-stable channel never silently downgrades the installed code or database.
4. If the installed build is newer than the selected channel, InfoMancer reports `waiting_for_channel` and stays on the installed build until a qualified downgrade path is explicitly allowed.
5. Applying an update still creates and validates a database backup first.
6. Server updates still require the restricted host updater and its trusted release-signature check.
7. Desktop updates still require the Tauri updater signing key and signed updater artifact.
8. Channel metadata never weakens platform-specific cryptographic verification.

## Current 0C foundation

The canonical channel classifier lives in `app/update_channels.py`. It owns:

- Standard / Beta / Dev labels and stability ordering
- semantic version and prerelease ordering
- release classification
- filtering a GitHub release list for the selected channel
- the no-blind-downgrade `waiting_for_channel` state
- conservative persistence of the installation-wide channel preference
- qualified manifest and database-schema contract validation

The dedicated settings surface is `/settings/updates`.

Every server update request now snapshots the qualified release identity that was displayed to the Librarian. The request can carry the selected channel, version, immutable build id, source commit, qualification run and gates, schema contract, manifest URL, and release-notes reference. The restricted host updater preserves that identity in status and a bounded `update-history.json` audit trail.

The host updater still verifies the release tag cryptographically. If a qualified manifest commit SHA was supplied, the signed release tag must resolve to that exact commit before checkout is allowed.

The Tauri desktop updater reads the same installation-wide `update-channel.json` setting as the bundled core and selects the corresponding signed rolling updater endpoint:

```text
Standard -> desktop-standard/latest.json
Beta     -> desktop-beta/latest.json
Dev      -> desktop-dev/latest.json
```

A damaged or unknown desktop channel setting fails conservatively to Standard. The updater public key and Tauri artifact signature remain mandatory. Standard release publication now advances `desktop-standard`; Dev publication remains qualification-gated by the canonical Tests workflow. Beta publication is completed by the promotion work rather than by treating arbitrary prerelease tags as Standard.

## Qualification and publishing

A qualified build record includes at minimum:

- channel
- immutable version/build identity
- source commit SHA
- build timestamp
- qualification workflow/run identity
- qualification result and passed gates
- database schema contract
- release notes reference
- platform artifacts
- cryptographic signature or checksum metadata required by that platform

The Dev publishing path is:

```text
push to 0.9
  -> regression/security/browser qualification
  -> package candidate artifacts
  -> verify packaged candidate
  -> publish immutable build
  -> atomically move Dev channel manifest to that build
```

A failed, cancelled, or incomplete qualification run never advances the Dev manifest.

## Promotion

Beta and Standard should promote an already-qualified immutable build wherever packaging/signing permits. The channel is a pointer to a build, not a request to rebuild source. Where a platform requires channel-specific metadata, that metadata may be regenerated, but the application payload should remain tied to the same immutable build identity.

The dedicated Beta promotion path will own `desktop-beta` and the Beta channel manifest. The production release path owns `desktop-standard` and refuses prerelease version strings.

## Version identity

Development builds use a monotonic prerelease identity such as:

```text
0.9.0-dev.184
```

A promoted preview may become:

```text
0.9.0-beta.1
```

and the production release:

```text
0.9.0
```

The immutable build identity and source commit remain separately recorded so promotion history is auditable even when the user-facing release version changes.

## Recovery-aware downgrade guidance

Cycle 0C also connects the schema compatibility ledger to portable recovery packages.

`Settings > Recovery` can explicitly scan InfoMancer's own `recovery-packages` directory plus additional directories listed in `INFOMANCER_RECOVERY_SEARCH_PATHS`. The scan is non-recursive and never performs an unrestricted filesystem crawl.

Each discovered `.infomancer-backup` is fully verified. InfoMancer then reads the packaged SQLite database's `schema_migrations` and `schema_compatibility` tables to derive the recovery point's actual compatibility contract. This works for existing portable backups without requiring a new archive-format version.

The derived contract is compared with currently published qualified Standard, Beta, and Dev manifests. Recommendation order is:

1. an exact creator-version match inside the selected channel when one is currently published
2. otherwise the newest qualified read/write-compatible build allowed by the selected channel
3. if no build in the selected channel is compatible, the most stable compatible cross-channel build may be shown as a fallback, but it is explicitly marked as requiring a channel change

Unknown or incomplete compatibility history fails closed for downgrades. InfoMancer may still recommend an equal or newer schema target because that path migrates the backup forward rather than asking older code to write a newer database.

## Cross-platform storage path reconciliation

Portable `.infomancer-backup` files remain cross-platform. During verified restore preview, InfoMancer reads the backup's original media roots and lets the Librarian map them to existing directories under the receiving installation's trusted storage.

Examples:

```text
D:\Movies       -> /media/Movies
D:\TV           -> /media/TV
\\NAS\TV        -> /Volumes/TV
```

Windows roots are interpreted with Windows path semantics even on Linux or macOS, including case-insensitive path comparisons. Relative media structure below the old root is preserved beneath the new root.

The rewrite happens only in the staged database. It covers roots, title folders, media files, managed Trash, rename proposals, and path-bearing rename Undo history. The rewritten database is then validated again against trusted storage before the normal recovery transaction may replace live state. Unsafe, ambiguous, missing, or untrusted mappings fail closed.

Recovery may suggest a unique same-name directory immediately beneath a trusted browse root, but it never searches the whole filesystem or silently accepts a guessed destination.
