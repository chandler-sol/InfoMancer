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
4. If the installed build is newer than the selected channel, InfoMancer reports `waiting_for_channel` and stays on the installed build until the channel catches up.
5. Applying an update still creates and validates a database backup first.
6. Server updates still require the restricted host updater and its trusted release-signature check.
7. Desktop updater signatures and server release trust remain independent platform-specific verification layers.

## Current 0C foundation

The canonical channel classifier lives in `app/update_channels.py`. It owns:

- Standard / Beta / Dev labels and stability ordering
- semantic version and prerelease ordering
- release classification
- filtering a GitHub release list for the selected channel
- the no-downgrade `waiting_for_channel` state
- conservative persistence of the installation-wide channel preference

The dedicated settings surface is `/settings/updates`.

The old Beta 2 release controls remain on System Settings temporarily. They are transitional and will be retired or redirected once the channel manifest path owns release discovery for every supported packaging method.

## Qualification and publishing plan

The next 0C stage replaces "published GitHub release" as the source of truth with a versioned channel manifest.

A qualified build record will include at minimum:

- channel
- immutable version/build identity
- source commit SHA
- build timestamp
- qualification workflow/run identity
- qualification result
- release notes reference
- platform artifacts
- cryptographic signature or checksum metadata required by that platform

The publishing path is:

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
