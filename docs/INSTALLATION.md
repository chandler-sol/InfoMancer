# Install InfoMancer

InfoMancer 0.8.1-beta.2 has two installation types:

| What you want | Install |
| --- | --- |
| One computer with its own private InfoMancer catalog | **InfoMancer Desktop** |
| A shared catalog for several computers, a NAS, or an always-on machine | **InfoMancer Server** |

> **Desktop is not Server.** Choosing **Run on this computer** in InfoMancer Desktop creates a local installation that only that computer can use. If other computers need to connect to the same catalog, install InfoMancer Server.

Your Movie and TV files stay where they already are. InfoMancer catalogs them in place.

# Install InfoMancer Server

Use Server for a home server, NAS-capable Docker host, headless computer, or any always-on machine that should serve one InfoMancer library to other devices.

For a normal home-network install, you need:

- a Windows, macOS, or Linux computer that can run Docker
- Docker Desktop on Windows/macOS, or Docker Engine plus Docker Compose on Linux
- the folders containing your Movies and TV Shows
- about 10 minutes

You do **not** need Python, Node, Rust, a database server, Cloudflare, or a programming environment.

## The easy Server install

### Step 1: Install Docker

Install Docker and make sure it is running before continuing.

### Step 2: Download and extract InfoMancer Server

Download:

`InfoMancer-Server-0.8.1-beta.2.zip`

Extract it somewhere permanent. The extracted folder will also hold your InfoMancer configuration and `data/` directory, so do not use a temporary folder if you plan to keep the server.

Open `START-HERE.txt` inside the extracted folder if you want the shortest possible instructions.

### Step 3: Run the setup helper

#### Windows

Double-click:

`Setup-InfoMancer.cmd`

A PowerShell window will open and keep itself visible when setup finishes.

#### macOS

Double-click:

`Setup-InfoMancer.command`

If macOS blocks it, right-click it and choose **Open**. You can also open Terminal in the extracted Server folder and run:

```bash
sh setup-infomancer.sh
```

#### Linux

Open a terminal in the extracted Server folder and run:

```bash
./setup-infomancer.sh
```

If the executable bit was lost while copying the files, run:

```bash
sh setup-infomancer.sh
```

### Step 4: Answer two simple media questions

The helper asks where your Movies and TV Shows live. You can leave one blank if you only have the other.

For example:

```text
Movies folder: /media/storage/Movies
TV Shows folder: /media/storage/TV
```

On Windows, a normal path such as `D:\Movies` is fine. The helper converts it to Docker-friendly form automatically.

The helper then:

1. creates `.env` if it does not already exist
2. creates `compose.media.yaml` from your answers
3. creates `data/`
4. sets the correct Linux UID/GID automatically when needed
5. checks that Docker and Docker Compose are available and running
6. builds and starts InfoMancer Server
7. waits for the Server to become healthy
8. creates and reads the one-time first-run setup code
9. prints the address to open from this computer and, when it can detect one, the LAN address for other computers

It does not move, copy, rename, or delete your media during setup.

### Step 5: Open InfoMancer

When setup finishes, it prints something similar to:

```text
InfoMancer Server is ready.

On this computer:
  http://127.0.0.1:8787

From another computer on this network:
  http://192.168.1.50:8787

One-time setup code:
  example-code-here
```

From another computer, the general address format is:

`http://SERVER-IP:8787`

Create the first **Librarian** account and paste in the one-time setup code when asked.

> **Do not port-forward port 8787 on your router and do not expose it directly to the public Internet.** Local-network access is built in. For access away from home, use a VPN or the documented authenticated reverse-proxy/Cloudflare setup in [Remote Access](REMOTE_ACCESS.md).

## What happens if I run the helper again?

The helper is designed not to casually destroy an existing setup:

- existing `.env` is kept
- existing `data/` is kept
- if `compose.media.yaml` already exists, the helper asks whether to keep it
- you can choose not to start Docker after creating/updating the config

That makes the helper useful for the first install without turning it into an upgrade or reset tool.

## Connect InfoMancer Desktop to the Server

Install InfoMancer Desktop on a Windows, Mac, or Linux computer, launch it, and choose **Connect to a server**.

Enter the same address you used in the browser, for example:

`http://192.168.1.50:8787`

The Server owns the catalog, accounts, settings, and media access. Desktop is only the client in this mode. The media folders therefore need to be accessible to the **Server**, not to every client computer.

## Manual / advanced Server setup

Most users should use the setup helper above. The manual steps remain available for unusual Docker setups or troubleshooting.

### Create the config files yourself

Choose the matching media template:

#### Windows PowerShell

```powershell
Copy-Item .env.example .env
Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
notepad compose.media.yaml
```

#### macOS

```bash
cp .env.example .env
cp deploy/macos.compose.yaml.example compose.media.yaml
open -e compose.media.yaml
```

#### Linux

```bash
cp .env.example .env
cp deploy/linux.compose.yaml.example compose.media.yaml
nano compose.media.yaml
```

Open `compose.media.yaml` and change the `source:` paths to the real folders on the **server**.

**Only change `source:` for a basic manual install. Leave the `/media/...` `target:` paths alone.**

Linux example:

```yaml
- type: bind
  source: /mnt/media/movies
  target: /media/movies
- type: bind
  source: /mnt/media/tv
  target: /media/tv
```

Windows example:

```yaml
source: D:/Movies
```

macOS example:

```yaml
source: /Volumes/Media/Movies
```

On Linux, set the container user to the current account and create `data/`:

```bash
sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
mkdir -p data
```

The server user needs read access to media for scanning. Rename, organize, and Managed Trash features also require write access to the affected media folders.

### Start manually

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Check status:

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

Look for the `infomancer` service to become healthy.

### Get the first-run setup code manually

Open `http://127.0.0.1:8787/setup` once, then run:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=100 infomancer
```

Find:

```text
InfoMancer first-run bootstrap token: ...
```

That token is the one-time setup code used to claim the first Librarian account. It stops being valid after that first Librarian account is created.

## Server troubleshooting

### Is the container running?

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

### What went wrong during startup?

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

### Restart the server

```bash
docker compose -f compose.yaml -f compose.media.yaml restart infomancer
```

### Stop the server

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

If another computer cannot connect but the server itself can, check the server operating system's firewall and allow TCP port `8787` on your trusted/private network.

Before posting logs publicly, remove private filenames, paths, addresses, API keys, bootstrap tokens, and session information.

# Install InfoMancer Desktop

Download the package that matches the computer:

| Platform | File |
| --- | --- |
| Windows 10/11 x64 | `InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe` |
| Mac with Apple Silicon | `InfoMancer-0.8.1-beta.2-macOS-Apple-Silicon.dmg` |
| Mac with Intel processor | `InfoMancer-0.8.1-beta.2-macOS-Intel.dmg` |
| Debian / Ubuntu / Linux Mint x86-64 | `InfoMancer-0.8.1-beta.2-Linux-x86_64.deb` |
| Other Linux x86-64 desktops | `InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage` |

If you are unsure which Mac you have, open **Apple menu > About This Mac**. An Apple M-series chip uses the Apple Silicon package. An Intel processor uses the Intel package.

Native packages include the FFprobe component used for technical media inspection. You do not need to install FFprobe separately.

## Windows Desktop

1. Download `InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe`.
2. Run the installer.
3. Launch **InfoMancer** from the Start menu.
4. Choose **Run on this computer** for a standalone local catalog or **Connect to a server** for an existing InfoMancer Server.
5. Follow Guided Setup if this is a new local installation.

The Beta 2 installer is not yet Authenticode-signed, so Windows SmartScreen may show an unknown-publisher warning. Only continue with a package downloaded from the official InfoMancer GitHub Release.

## macOS Desktop

1. Download the DMG that matches the Mac.
2. Open the DMG and move **InfoMancer** into Applications.
3. Try to launch InfoMancer.
4. If macOS blocks it, open **System Settings > Privacy & Security**, scroll to Security, and choose **Open Anyway** for InfoMancer. You may need to attempt the launch once before that option appears.
5. Choose **Run on this computer** or **Connect to a server**.

Beta 2 is not yet Apple-notarized, so the Privacy & Security approval step is expected on many Macs.

## Linux Desktop

For Debian, Ubuntu, or Linux Mint:

```bash
sudo apt install ./InfoMancer-0.8.1-beta.2-Linux-x86_64.deb
```

For the AppImage:

```bash
chmod +x InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage
./InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage
```

Then launch InfoMancer and choose **Run on this computer** or **Connect to a server**.

# Updates

## InfoMancer Desktop

Install a newer package over the existing application. The local catalog remains in the operating system's InfoMancer application-data folder.

For beta testing, create a fresh `.infomancer-backup` before an upgrade when the catalog matters to you.

## InfoMancer Server

Keep these three things when updating:

- `.env`
- `compose.media.yaml`
- `data/`

Back them up, replace the application files with the newer InfoMancer Server package, then run:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Database migrations run automatically at startup.

# Backups

**Desktop standalone:** use InfoMancer's Recovery tools to create a portable `.infomancer-backup`.

**Server:** protect these together:

- `data/`
- `.env`
- `compose.media.yaml`

Recovery packages and deployment backups contain InfoMancer state, not the Movie or TV files themselves.

# Uninstall

Removing InfoMancer does not delete your media files.

For Server, stop the containers with:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Delete the extracted Server folder only if you also intend to delete that server's InfoMancer catalog and configuration.

# More help

- **[Remote Access](REMOTE_ACCESS.md)**
- **[Updating InfoMancer](UPDATES.md)**
- **[Packaging](PACKAGING.md)**
- **[CLI](CLI.md)**

Direct Python execution is intended for development and troubleshooting, not normal installation.
