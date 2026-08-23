# Install InfoMancer

InfoMancer 0.8 alpha can be installed in two main ways:

1. **Native desktop app** for a personal Windows, macOS, or Linux computer. The desktop app can run its own local InfoMancer core or connect to an existing InfoMancer server.
2. **Docker Compose** for a server, NAS-adjacent host, headless machine, or installation that should be available to multiple computers.

The native packages are alpha previews. Docker remains the most mature self-hosted deployment path, but Docker is **not required** to test the dedicated desktop application.

## Before you begin

For any installation, you need read access to the Movie and TV folders you want to catalog. Grant write access only to locations where you want InfoMancer to rename, organize, restore, or move supported duplicates into Managed Trash.

A TheTVDB project API key is recommended for matching and missing-episode information. Guided Setup explains where to enter it.

InfoMancer uses FFprobe for technical media inspection. The 0.8 native preview does not yet bundle FFprobe, so install FFmpeg/FFprobe on the host if you want Media inspection and the technical portions of MIE to work. Docker includes FFprobe inside the application environment.

## Native desktop app

Download the package that matches your platform from the GitHub release assets.

The native launcher offers two modes on first launch:

- **Run on this computer** starts the bundled InfoMancer core locally and stores the catalog in the operating system's application-data location.
- **Connect to a server** opens an existing InfoMancer installation in the native shell. Enter the server's `http://` or `https://` address.

Your movie and TV files remain where they already live. The desktop app does not copy media into InfoMancer.

### Windows 10/11 x64

Download:

`InfoMancer_0.8.0-alpha.1_x64-setup.exe`

1. Run the installer.
2. Launch **InfoMancer** from the Start menu.
3. Choose **Run on this computer** for a standalone installation, or **Connect to a server** for an existing InfoMancer server.
4. For a new standalone installation, follow the first-run setup and create the initial Librarian account.
5. Add Windows folders, drive letters, or accessible UNC shares as Sources.

The 0.8 alpha installer is a preview build and is not yet Authenticode-signed. Windows SmartScreen may therefore warn that the publisher is unknown. Verify that the installer came from the official InfoMancer GitHub release and compare its SHA-256 value with `SHA256SUMS.txt` before overriding a warning.

InfoMancer stores standalone desktop state under the current user's application-data area. The Windows uninstaller can offer a portable `.infomancer-backup` before removing local application state. Media files are never part of uninstall cleanup.

### macOS Apple silicon

Download:

`InfoMancer_0.8.0-alpha.1_aarch64.dmg`

The current 0.8 draft provides an **Apple silicon** preview. An Intel/universal macOS package is not included yet.

1. Open the DMG and move InfoMancer into Applications when prompted.
2. Launch InfoMancer.
3. Choose **Run on this computer** or **Connect to a server**.
4. Complete first-run setup for a new local installation.
5. Grant macOS access to external volumes or network locations when required.

The current alpha DMG is not Apple-notarized. If macOS blocks the first launch, use the normal macOS unsigned-development-app override: try opening the app once, then use **System Settings > Privacy & Security > Open Anyway** for InfoMancer. Only do this for a package you obtained from the official release and verified against `SHA256SUMS.txt`.

### Linux x86-64

Two packages are provided:

- `InfoMancer_0.8.0-alpha.1_amd64.deb` for Debian/Ubuntu-family systems, including Linux Mint where compatible.
- `InfoMancer_0.8.0-alpha.1_amd64.AppImage` as a portable preview for other desktop distributions.

Install the DEB with:

```bash
sudo apt install ./InfoMancer_0.8.0-alpha.1_amd64.deb
```

Or run the AppImage with:

```bash
chmod +x InfoMancer_0.8.0-alpha.1_amd64.AppImage
./InfoMancer_0.8.0-alpha.1_amd64.AppImage
```

Some distributions may require FUSE compatibility for AppImage execution. The DEB is preferred on supported Debian/Ubuntu-family desktops.

After launch, choose **Run on this computer** or **Connect to a server**, then complete first-run setup if this is a new local catalog. Media mounted under `/media`, `/mnt`, your home directory, or another path accessible to your user can be selected as Sources.

## Server / self-hosted installation with Docker

Use Docker when InfoMancer should stay running on a server, be shared by multiple computers, or be administered independently of a desktop login.

### One application process per catalog

InfoMancer intentionally runs one application process against each SQLite catalog. Background scans, scheduled maintenance, task progress, and filesystem operations are coordinated inside that process. Do not add Uvicorn workers or run multiple InfoMancer replicas against the same `data/infomancer.db` file.

The application maintains a database-backed runtime lease and refuses a second live process that attempts to use the same catalog. A stale lease can be reclaimed automatically after an unclean process exit.

### Windows Docker host

1. Install and start Docker Desktop.
2. Download the InfoMancer server/source ZIP and extract it to a permanent folder such as `C:\InfoMancer`.
3. Open PowerShell in that folder.
4. Create the local configuration:

```powershell
Copy-Item .env.example .env
Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
notepad compose.media.yaml
```

5. Replace the example media paths with your real folders. Keep forward slashes in the YAML file.
6. Start InfoMancer:

```powershell
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

7. Open `http://127.0.0.1:8787` and create the first Librarian account.
8. In Guided Setup, choose the `/media/...` paths that correspond to your Windows mappings.

For a network share, use a source such as `//server/share/Movies`. Confirm that the share works in Windows first and that Docker Desktop is allowed to access it.

### macOS Docker host

1. Install and start Docker Desktop.
2. Download and extract the InfoMancer server/source ZIP.
3. In Terminal:

```bash
cp .env.example .env
cp deploy/macos.compose.yaml.example compose.media.yaml
open -e compose.media.yaml
```

4. Replace the `/Volumes/Media/...` examples with your mounted folders.
5. Start InfoMancer:

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

6. Open `http://127.0.0.1:8787`, create the first Librarian, and complete Guided Setup.

Approve Docker Desktop access to external disks or folders when macOS requests it.

### Linux Docker host

Install Docker Engine from Docker's repository and the Docker Compose plugin for your distribution. Then extract the InfoMancer server/source ZIP and create the local configuration:

```bash
cp .env.example .env
cp deploy/linux.compose.yaml.example compose.media.yaml
nano compose.media.yaml
```

Replace the example media paths, then match the container's non-root account to the Linux user that owns the InfoMancer data directory:

```bash
if [ "$(id -u)" -eq 0 ]; then
  echo "Run this step as the non-root account that will own InfoMancer data." >&2
  exit 1
fi
sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
mkdir -p data
```

Start InfoMancer:

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Open `http://127.0.0.1:8787`. For a headless remote host, tunnel the loopback port:

```bash
ssh -L 8787:127.0.0.1:8787 user@server-address
```

Then open `http://127.0.0.1:8787` on your own computer.

## Confirm a Docker installation is running

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

If the service does not become healthy:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

Remove API keys, session information, private filenames, and personal paths before sharing logs publicly.

## Updates

### Native desktop preview

Download the newer installer/package from the official release and install it over the existing application. A normal application update must preserve local catalog data. Signed automatic desktop updates are still being qualified for the alpha channel.

Create a current recovery package before testing an alpha upgrade when the local catalog matters to you.

### Docker

Back up the `data` folder, stop InfoMancer, replace the application files while preserving `.env`, `compose.media.yaml`, and `data/`, then rebuild:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Database migrations run automatically when the updated application starts.

## Backups

For Docker deployments, protect these together:

- `data/` for the catalog, users, settings, encrypted provider credentials, and generated encryption key when applicable.
- `.env` for deployment configuration.
- `compose.media.yaml` for host-to-container storage mappings.

For native local installations, use InfoMancer's Recovery tools to create a portable `.infomancer-backup`. Recovery packages contain catalog/application state but never movie or TV files.

## Uninstall

Native uninstallers remove InfoMancer-owned application state according to the platform's desktop packaging behavior. Windows offers the recovery-backup path before destructive local-data cleanup. User media is never deleted.

For Docker:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Deleting the deployment folder removes its local catalog/settings but not media that was mounted into the container.

## Advanced direct-Python installation

Running directly with Python is intended for development and troubleshooting, not the normal installation path. It requires Python 3.13, FFmpeg/FFprobe, and platform-specific service management:

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
# Windows: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8787
```

Do not add `--workers` when using one InfoMancer SQLite catalog. For direct native use, set `MEDIA_BROWSE_ROOTS` to real paths for that operating system, separated by commas.
