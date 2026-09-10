# Updating InfoMancer

Updating InfoMancer should not require you to understand Git, signing keys, or Docker internals.

Because 0.8.1 is still beta software, make a backup before updating a catalog you care about.

# InfoMancer Desktop

## Normal update

If InfoMancer tells you a Desktop update is available, you can use the Desktop update screen.

If the in-app installer is not available for your build:

1. Download the newer InfoMancer Desktop package for your operating system.
2. Close InfoMancer.
3. Install the newer package over the existing application.
4. Start InfoMancer normally.

Do not uninstall the old Desktop application first unless you intentionally want a clean reinstall.

A standalone Desktop catalog is stored in InfoMancer's application-data folder, not inside the installed program files.

## Before a beta update

From **Settings > System**, create a fresh `.infomancer-backup` when the catalog matters to you.

See **[Backup and Restore](RECOVERY.md)** for details.

# InfoMancer Server

For beta releases, the safest update method is to keep the old Server folder until the new version is working.

## Before the update

Back up these three items from the current Server folder:

- `data/`
- `.env`
- `compose.media.yaml`

They contain the Server's InfoMancer state and local deployment settings. Your Movie and TV files are not stored in the Server package.

## Update the Server

1. Stop the current Server:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

2. Download the newer `InfoMancer-Server-<version>.zip`.
3. Extract it into a new folder.
4. Copy your existing `data/`, `.env`, and `compose.media.yaml` into the new folder.
5. Run the normal InfoMancer Server setup helper in the new folder.
6. If it asks whether to keep the existing media configuration, choose **Yes**.
7. Start InfoMancer and confirm the library, accounts, Sources, and Settings look correct.

Keep the old Server folder until you are satisfied that the updated Server is working.

Database migrations run automatically when the newer Server starts.

## Roll back a Server beta

If the new Server does not work:

1. Stop it.
2. Keep its folder for troubleshooting.
3. Return to the previous Server folder and the pre-update copy of `data/`, `.env`, and `compose.media.yaml`.
4. Start the previous Server again.

Do not point an older Server at a database that was already migrated by a newer build. Keeping a separate pre-update copy is what makes the rollback safe.

# Advanced update administration

Most users do not need Git tag verification, systemd updater services, signing-key fingerprints, or `host_updater.py`.

Those operator and maintainer details live in **[Advanced Update Administration](UPDATES_ADVANCED.md)**.
