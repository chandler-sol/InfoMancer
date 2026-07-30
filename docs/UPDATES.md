# Updating InfoMancer

InfoMancer can check GitHub releases without additional setup. Installing a
release from the web interface is optional and uses a separate, restricted
host helper. The web application never receives Docker or Git control.

## Manual updates

From the InfoMancer checkout:

```sh
git fetch --tags
git checkout --detach v0.4.0-alpha.1
docker compose -p infomancer -f compose.yaml -f compose.media.yaml up -d --build --remove-orphans
```

Replace the tag and Compose files with the release and deployment files you
use. Create or download a database backup from **Settings > System** first.

## Enable updates from the interface on Linux

1. Copy `deploy/infomancer-updater.service.example` to
   `/etc/systemd/system/infomancer-updater.service`.
2. Edit `User`, `WorkingDirectory`, `ExecStart`, and the repeated
   `--compose-file` values to match the installation.
3. The service user must be able to run Docker and read/write the InfoMancer
   checkout.
4. Start the helper:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now infomancer-updater
```

The helper refuses to update a checkout with local source edits. It fetches
only a release tag selected by InfoMancer, rebuilds the existing Compose
project, checks `/health`, and returns to the previous commit if the new
release does not start successfully.

## Windows and macOS

The same Python helper is cross-platform and can be run by Task Scheduler,
launchd, or manually:

```text
python scripts/host_updater.py --watch --compose-file compose.yaml --compose-file compose.media.yaml
```

Keep it running under a dedicated operating-system account with access only to
the InfoMancer checkout and Docker.
