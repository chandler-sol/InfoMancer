# Install InfoMancer

InfoMancer 0.8 can run as a native desktop app or as a shared server.

## Start here

Choose the setup that matches what you want:

| You want to... | Choose |
| --- | --- |
| Use InfoMancer only on this computer | **Native app → Run on this computer** |
| Use the native app with a server you already have | **Native app → Connect to a server** |
| Share one InfoMancer library between computers | **Docker / server** |
| Keep InfoMancer running on a headless or always-on machine | **Docker / server** |

> **Important for 0.8:** A native app using **Run on this computer** is local-only. It does not make that Windows, Mac, or Linux computer available as an InfoMancer server to other devices. The bundled local core listens only on that machine.

Your media files stay where they already are. InfoMancer catalogs them in place.

## Download the right file

From the GitHub Release, choose exactly one native package or the server ZIP:

| Platform | File |
| --- | --- |
| Windows 10/11 x64 | `InfoMancer-0.8.1-alpha.1-Windows-x64-Setup.exe` |
| Mac with Apple Silicon | `InfoMancer-0.8.1-alpha.1-macOS-Apple-Silicon.dmg` |
| Mac with Intel processor | `InfoMancer-0.8.1-alpha.1-macOS-Intel.dmg` |
| Debian / Ubuntu / Linux Mint x86-64 | `InfoMancer-0.8.1-alpha.1-Linux-x86_64.deb` |
| Other Linux x86-64 desktops | `InfoMancer-0.8.1-alpha.1-Linux-x86_64.AppImage` |
| Docker / server | `InfoMancer-0.8.1-alpha.1.zip` |

If you are unsure which Mac you have, open **Apple menu → About This Mac**. A Mac showing an Apple M-series chip uses **Apple Silicon**. A Mac showing an Intel processor uses **Intel**.

Native 0.8 packages include the FFprobe component used for technical media inspection. You do not need to install FFprobe separately for the packaged desktop app.

## Windows

1. Download `InfoMancer-0.8.1-alpha.1-Windows-x64-Setup.exe`.
2. Run the installer.
3. Launch **InfoMancer** from the Start menu.
4. Choose **Run on this computer** for a new local installation, or **Connect to a server** for an existing InfoMancer server.
5. If this is a new local installation, create the first Librarian account and follow Guided Setup.

The 0.8 alpha installer is not yet Authenticode-signed. Windows SmartScreen may show an unknown-publisher warning. Only continue with a package downloaded from the official InfoMancer GitHub Release, and compare it with `SHA256SUMS.txt` if you want to verify the download.

A standalone Windows installation can use local folders, drive letters, and network shares that are accessible to your Windows account.

## macOS

1. Download the DMG that matches your Mac:
   - Apple Silicon: `InfoMancer-0.8.1-alpha.1-macOS-Apple-Silicon.dmg`
   - Intel: `InfoMancer-0.8.1-alpha.1-macOS-Intel.dmg`
2. Open the DMG and move **InfoMancer** into Applications.
3. Launch InfoMancer.
4. If macOS blocks the app, open **System Settings → Privacy & Security**, scroll to the Security section, and choose **Open Anyway** for InfoMancer. You may need to attempt to open InfoMancer once before this option appears.
5. Choose **Run on this computer** or **Connect to a server**.
6. Complete Guided Setup for a new local installation.

> **First launch on macOS:** The 0.8 alpha DMG is not yet Apple-notarized, so the Privacy & Security approval step above will commonly be required. Only approve a package downloaded from the official InfoMancer GitHub Release.

macOS may also ask for permission before InfoMancer can access external drives, mounted shares, or folders outside your normal user area.

## Linux

### Debian, Ubuntu, Linux Mint

The DEB is the preferred package on compatible systems:

```bash
sudo apt install ./InfoMancer-0.8.1-alpha.1-Linux-x86_64.deb
```

Then launch InfoMancer from your desktop application menu.

### AppImage

For other x86-64 desktop distributions:

```bash
chmod +x InfoMancer-0.8.1-alpha.1-Linux-x86_64.AppImage
./InfoMancer-0.8.1-alpha.1-Linux-x86_64.AppImage
```

Some distributions require FUSE compatibility for AppImage execution.

After launch, choose **Run on this computer** or **Connect to a server**. Local media under your home folder, `/media`, `/mnt`, or other paths accessible to your user can be added as Sources.

## Native app modes

### Run on this computer

Use this when one computer should own its own InfoMancer catalog.

- InfoMancer starts a bundled local core automatically.
- The catalog is stored in that operating system's application-data area.
- The core is reachable only from that computer in 0.8.
- Closing/quitting the desktop app stops the bundled local core.

### Connect to a server

Use this when an InfoMancer server already exists elsewhere.

Enter its `http://` or `https://` address in the native launcher. The server owns the catalog, accounts, settings, and media access. The client computer does not create a second copy of the catalog.

The media paths configured in InfoMancer must be accessible to the **server**, not merely to the client computer.

## Docker / shared server

Use the server installation when InfoMancer should be shared, always available, or run without a desktop login.

### 1. Install Docker

Install Docker Desktop on Windows/macOS or Docker Engine plus the Docker Compose plugin on Linux.

### 2. Extract the server ZIP

Download and extract:

`InfoMancer-0.8.1-alpha.1.zip`

Keep the extracted folder somewhere permanent.

### 3. Create your local configuration

Copy `.env.example` to `.env`.

Then copy the example for your host OS to `compose.media.yaml`:

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
notepad compose.media.yaml
```

**macOS**

```bash
cp .env.example .env
cp deploy/macos.compose.yaml.example compose.media.yaml
open -e compose.media.yaml
```

**Linux**

```bash
cp .env.example .env
cp deploy/linux.compose.yaml.example compose.media.yaml
nano compose.media.yaml
```

Edit `compose.media.yaml` so its host paths point to your real Movie and TV folders.

On Linux, also make the container user match the account that will own the InfoMancer data folder:

```bash
sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
mkdir -p data
```

Run those Linux commands as the normal non-root account that should own InfoMancer data.

### 4. Start InfoMancer

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

### 5. Open InfoMancer

On the server itself, open:

`http://127.0.0.1:8787`

Create the first Librarian account and complete Guided Setup.

If this is a remote/headless machine, use an SSH tunnel, VPN, authenticated reverse proxy, or the documented Cloudflare path instead of exposing port 8787 directly to the public internet.

See **[Remote Access](REMOTE_ACCESS.md)**.

## Check server status

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

If InfoMancer does not become healthy:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

Before sharing logs publicly, remove private filenames, paths, addresses, API keys, and session information.

## Updates

### Native app

Install the newer package over the existing application. Your local catalog should remain in the operating system's application-data folder.

For alpha upgrades, create a fresh `.infomancer-backup` first when the local catalog matters to you.

### Docker / server

Back up your deployment, replace the application files while keeping `.env`, `compose.media.yaml`, and `data/`, then rebuild:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Database migrations run automatically at startup.

## Backups

**Native standalone:** use InfoMancer's Recovery tools to create a portable `.infomancer-backup`.

**Docker/server:** protect these together:

- `data/`
- `.env`
- `compose.media.yaml`

Recovery packages and deployment backups contain InfoMancer state, not your Movie or TV files.

## Uninstall

Removing InfoMancer does not delete your media files.

On Windows, the uninstaller can offer a recovery-backup path before removing local InfoMancer application state.

For Docker:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Delete the deployment folder only if you also intend to remove its local InfoMancer catalog and configuration.

## Need more detail?

- **[Remote Access](REMOTE_ACCESS.md)**
- **[Updating InfoMancer](UPDATES.md)**
- **[Packaging](PACKAGING.md)**
- **[CLI](CLI.md)**

Direct Python execution is intended for development and troubleshooting rather than normal 0.8 installation.
