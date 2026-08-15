# Updating InfoMancer

InfoMancer can check GitHub releases without additional setup. Installing a
release from the web interface is optional and uses a separate, restricted
host helper. The web application never receives Docker or Git control.

## Release signature requirement

The host updater refuses to install an unsigned or invalidly signed Git tag.
After fetching release tags, it runs `git verify-tag --raw` before resolving or
checking out the requested commit. The host account therefore needs the public
GPG key used to sign InfoMancer release tags in its Git/GPG keyring.

For an additional trust boundary, start the helper with the full expected
signing-key fingerprint:

```text
--trusted-signing-key FULL_GPG_FINGERPRINT
```

The option may be supplied more than once during a signing-key rotation. When
one or more fingerprints are configured, a cryptographically valid tag is
accepted only when its `VALIDSIG` fingerprint matches that allowlist.

Release maintainers should create annotated signed tags, for example:

```sh
git tag -s v0.7.0-alpha.1 -m "InfoMancer 0.7.0 alpha 1"
git push origin v0.7.0-alpha.1
```

Older unsigned tags remain usable for manual rollback or inspection, but the
restricted host updater intentionally will not install them.

## Manual updates

From the InfoMancer checkout:

```sh
git fetch --tags
git verify-tag v0.7.0-alpha.1
git checkout --detach v0.7.0-alpha.1
docker compose -p infomancer -f compose.yaml -f compose.media.yaml up -d --build --remove-orphans
```

Replace the tag and Compose files with the release and deployment files you
use. Create or download a database backup from **Settings > System** first.

## Enable updates from the interface on Linux

1. Copy `deploy/infomancer-updater.service.example` to
   `/etc/systemd/system/infomancer-updater.service`.
2. Edit `User`, `WorkingDirectory`, `ExecStart`, and the repeated
   `--compose-file` values to match the installation. Add
   `--trusted-signing-key FULL_GPG_FINGERPRINT` when you want to restrict
   updates to an explicit release key.
3. Import the release-signing public key into the GPG keyring of the service
   account and verify its fingerprint out of band.
4. The service user must be able to run Docker and read/write the InfoMancer
   checkout.
5. Start the helper:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now infomancer-updater
```

The helper refuses to update a checkout with local source edits. It fetches
only a release tag selected by InfoMancer, verifies the tag signature before
checkout, rebuilds the existing Compose project, checks `/health`, and returns
to the previous commit if the new release does not start successfully.

## Windows and macOS

The same Python helper is cross-platform and can be run by Task Scheduler,
launchd, or manually:

```text
python scripts/host_updater.py --watch --compose-file compose.yaml --compose-file compose.media.yaml --trusted-signing-key FULL_GPG_FINGERPRINT
```

Keep it running under a dedicated operating-system account with access only to
the InfoMancer checkout, Docker, and the release-signing public key needed for
verification.
