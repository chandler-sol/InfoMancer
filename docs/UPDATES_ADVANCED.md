# Advanced Update Administration

This page is for operators and maintainers who intentionally run InfoMancer from a repository checkout or enable the restricted host updater.

Normal Desktop and packaged Server users should use **[Updating InfoMancer](UPDATES.md)** instead.

# Security model

The InfoMancer web application does not receive general Docker or Git control. Automated host updates use a separate, restricted helper running under an operating-system account with only the access it needs.

The host updater accepts only release tags whose signatures can be verified against an explicitly trusted signing-key fingerprint.

# Signed release tags

After fetching release tags, the host updater runs `git verify-tag --raw` before resolving or checking out the requested commit.

The service account therefore needs the InfoMancer release-signing public key in its GPG keyring and the full expected fingerprint in the updater configuration:

```text
--trusted-signing-key FULL_GPG_FINGERPRINT
```

The option may be supplied more than once during a signing-key rotation. A valid signature from another key in the account's GPG keyring is not enough. The `VALIDSIG` fingerprint must match the configured allowlist.

Release maintainers should create annotated signed tags and verify them before publishing.

Generic example:

```bash
git tag -s vX.Y.Z -m "InfoMancer X.Y.Z"
git push origin vX.Y.Z
git verify-tag vX.Y.Z
```

# Manual repository update

For an installation intentionally deployed from a Git checkout:

```bash
git fetch --tags
git verify-tag vX.Y.Z
git checkout --detach vX.Y.Z
docker compose -p infomancer -f compose.yaml -f compose.media.yaml up -d --build --remove-orphans
```

Replace `vX.Y.Z` with the release being installed. Create a backup first and preserve the deployment's `.env`, `compose.media.yaml`, and `data/`.

The restricted updater refuses to update a checkout with local source edits.

# Linux host updater service

An example systemd unit is provided at:

`deploy/infomancer-updater.service.example`

To use it:

1. Copy it to `/etc/systemd/system/infomancer-updater.service`.
2. Edit `User`, `WorkingDirectory`, `ExecStart`, and the repeated `--compose-file` values for the installation.
3. Replace `FULL_GPG_FINGERPRINT` with the verified full fingerprint of the InfoMancer release key.
4. Import the release-signing public key into the GPG keyring of the service account and verify its fingerprint independently.
5. Make sure the service account can run Docker and read/write the InfoMancer checkout.
6. Start the helper:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now infomancer-updater
```

The helper fetches only the selected release tag, verifies its signature, rebuilds the existing Compose project, checks `/health`, and returns to the previous commit if the replacement does not become healthy.

# Windows and macOS host helper

The same restricted Python helper can be run manually or by an operating-system scheduler:

```text
python scripts/host_updater.py --watch --compose-file compose.yaml --compose-file compose.media.yaml --trusted-signing-key FULL_GPG_FINGERPRINT
```

Run it under a dedicated account with access only to the InfoMancer checkout, Docker, and the release-signing public key needed for verification.

# Native Desktop updater

Packaged Desktop builds use Tauri's signed updater rather than the Server host-update mechanism.

Updater signatures are mandatory when the update channel is configured:

- the public verification key is compiled into release builds through `TAURI_UPDATER_PUBLIC_KEY`
- the private signing key is supplied only to the release workflow through `TAURI_SIGNING_PRIVATE_KEY` and its optional password
- builds without a configured public verification key report the updater as unavailable instead of accepting unsigned updates

Before a replacement installer runs, the Desktop shell stops its bundled local core. The normal update path is designed to replace application binaries while preserving the user's InfoMancer application data.

# Operational cautions

- Back up a catalog before testing a beta update.
- Do not disable signature verification to make an update work.
- Do not give the web application direct Docker or Git credentials.
- Do not run the host updater with broader operating-system permissions than it needs.
- Do not treat an older binary as a safe rollback target for a database that has already been migrated by a newer version. Use a pre-update database or Server-folder backup instead.
