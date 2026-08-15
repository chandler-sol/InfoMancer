# Install InfoMancer

Docker is the recommended installation method for InfoMancer. It provides the
same application environment on Windows, macOS, and Linux, includes FFprobe for
media inspection, and keeps upgrades predictable.

InfoMancer does not copy your media into Docker. You choose which host folders
are visible to the application, and the catalog remains in the local `data`
folder beside the application.

## Before you begin

You need:

1. A computer or server that can remain on while you use InfoMancer.
2. Read access to the Movie and TV folders you want to catalog.
3. Write access to a media folder only if you want InfoMancer to rename files.
4. [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
   or [macOS](https://docs.docker.com/desktop/setup/install/mac-install/), or
   [Docker Engine](https://docs.docker.com/engine/install/) with the Compose
   plugin on Linux.
5. A TheTVDB project API key for matching and missing-episode information.
   Guided Setup explains where to enter it.

## One application process per catalog

InfoMancer intentionally runs one application process against each SQLite
catalog. Background scans, scheduled maintenance, task progress, and filesystem
operations are coordinated inside that process. Do not add Uvicorn workers or
run multiple InfoMancer replicas against the same `data/infomancer.db` file.

The application maintains a database-backed runtime lease and refuses a second
live process that attempts to use the same catalog. A stale lease can be
reclaimed automatically after an unclean process exit.

## Windows

1. Install and start Docker Desktop.
2. Download the InfoMancer release ZIP and extract it to a permanent folder,
   such as `C:\InfoMancer`.
3. Open PowerShell in that folder.
4. Create the local configuration:

   ```powershell
   Copy-Item .env.example .env
   Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
   notepad compose.media.yaml
   ```

5. Replace `D:/Movies` and `D:/TV` with your real folders. Keep forward slashes
   in the YAML file. Remove an example mapping if you do not need it, or
   duplicate a complete `source`/`target` block for another disk.
6. Start InfoMancer:

   ```powershell
   docker compose -f compose.yaml -f compose.media.yaml up -d --build
   ```

7. Open [http://127.0.0.1:8787](http://127.0.0.1:8787) and create the first
   Librarian account.
8. In Guided Setup, choose folders such as `/media/movies` and `/media/tv`.
   These are the container names for the Windows folders you mapped.

For a network share, use a source such as `//server/share/Movies`. Confirm that
the share works in Windows first and that Docker Desktop is allowed to access
it. A service cannot use a network mapping that exists only inside a different
Windows user session.

## macOS

1. Install and start Docker Desktop.
2. Download and extract the InfoMancer release ZIP into a permanent folder.
3. Open Terminal in that folder.
4. Create the local configuration:

   ```bash
   cp .env.example .env
   cp deploy/macos.compose.yaml.example compose.media.yaml
   open -e compose.media.yaml
   ```

5. Replace the `/Volumes/Media/...` examples with your real mounted folders.
6. Start InfoMancer:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml up -d --build
   ```

7. Open [http://127.0.0.1:8787](http://127.0.0.1:8787), create the first
   Librarian, and complete Guided Setup.

If Docker Desktop asks for permission to share an external disk or folder,
approve the location before scanning it.

## Linux

These instructions cover Debian, Ubuntu, Fedora, RHEL-compatible
distributions, and other systems supported by Docker Engine and Compose. Linux
Mint commonly follows the instructions for its Ubuntu or Debian base, but
Docker does not officially test every derivative distribution.

1. Install Docker Engine from Docker's repository and install the Docker
   Compose plugin for your distribution.
2. Download and extract InfoMancer into a permanent location such as
   `/opt/infomancer` or a folder in your home directory.
3. Open a terminal in that folder and create the local configuration:

   ```bash
   cp .env.example .env
   cp deploy/linux.compose.yaml.example compose.media.yaml
   nano compose.media.yaml
   ```

4. Replace `/mnt/media/movies` and `/mnt/media/tv` with your mounted storage.
5. Start InfoMancer:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml up -d --build
   ```

6. Open `http://127.0.0.1:8787`. When the Linux host has no desktop, create an
   SSH tunnel from your own computer:

   ```bash
   ssh -L 8787:127.0.0.1:8787 user@server-address
   ```

   Then open `http://127.0.0.1:8787` on your own computer.

7. Create the Librarian and finish Guided Setup.

## Confirm that it is running

Run:

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

The `infomancer` service should be running and become healthy. If it does not:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

The log should explain the failure. If you request help, remove API keys,
session information, private filenames, and personal paths before sharing it.

## Updates

1. Back up the `data` folder.
2. Stop InfoMancer:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml down
   ```

3. Replace the application files with the new release. Keep your `.env`,
   `compose.media.yaml`, and `data` folder.
4. Rebuild and start:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml up -d --build
   ```

Database migrations run automatically when the updated application starts.

## Backups

Back up all of these together:

- `data/` - catalog, users, settings, encrypted provider credentials, and the
  generated encryption key when `INFOMANCER_SECRET` is blank.
- `.env` - protected deployment configuration.
- `compose.media.yaml` - host-to-container storage mappings.

The catalog can be recreated by scanning, but accounts, personal organization,
settings, and original-filename history cannot be reconstructed from media
files alone.

## Uninstall

Stop and remove the container:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Deleting the InfoMancer application folder removes its catalog and settings.
InfoMancer never stores media inside that folder, and uninstalling it does not
delete your Movie or TV files.

## Advanced native installation

Running directly with Python is intended for development and troubleshooting,
not the normal installation path. It requires Python 3.13, FFmpeg/FFprobe, and
platform-specific service management:

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
# Windows: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8787
```

Do not add `--workers` to that command when using the same InfoMancer catalog.
For native use, set `MEDIA_BROWSE_ROOTS` to real paths for that operating
system. Separate multiple paths with commas.
