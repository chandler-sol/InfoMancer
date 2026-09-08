# Install InfoMancer Server

InfoMancer currently has one installable component: **InfoMancer Server**.

You install it once, then use InfoMancer in a web browser.

The server can run on the same computer you are using or on another computer that stays on. The software is the same either way.

## First, choose where InfoMancer Server will run

| Choice | Best for | What it means |
| --- | --- | --- |
| **Local install** | Trying InfoMancer, one-person use, or media attached to your main computer | InfoMancer Server and your browser are on the same computer |
| **Dedicated server install** | An always-on media computer, home server, or headless Linux system | InfoMancer Server runs on another computer and you connect to it securely |

A **local install is still InfoMancer Server**. The word Server describes the software's job, not the type of computer it must run on.

For your first install, use the computer where your media is already easiest to access unless you already have an always-on server you want to use.

## What gets installed

The release ZIP contains InfoMancer Server and the files Docker needs to run it.

A release is named like:

```text
InfoMancer-Server-VERSION.zip
```

Docker keeps InfoMancer's runtime consistent across Windows, macOS, and Linux. Your media does **not** get copied into Docker.

You tell Docker which folders InfoMancer may see. For example:

```text
Real Windows folder: D:\Movies
InfoMancer folder:   /media/movies
```

Inside InfoMancer, you select `/media/movies`. The actual files remain on `D:\Movies`.

## Before you begin

You need:

1. A Windows, macOS, or Linux computer that can stay on while you use InfoMancer.
2. Access to the Movie and TV folders you want to catalog.
3. Write access to a folder only if you want InfoMancer to rename files there.
4. Docker Desktop on Windows or macOS, or Docker Engine with the Compose plugin on Linux.
5. A TVDB project API key if you want matching and missing-episode information. Guided Setup explains where to enter it.

You do **not** need a separate InfoMancer application on every computer, phone, or tablet that will browse the library.

## Windows

### 1. Install Docker Desktop

Install and start [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

Wait until Docker Desktop reports that Docker is running.

### 2. Extract InfoMancer Server

Download the InfoMancer Server release ZIP and extract it to a permanent folder, for example:

```text
C:\InfoMancer
```

Do not run it directly from your Downloads folder if you plan to keep using that installation.

### 3. Open PowerShell in the InfoMancer folder

In File Explorer, open the extracted InfoMancer folder, click the address bar, type `powershell`, and press Enter.

### 4. Create your local configuration files

Run:

```powershell
Copy-Item .env.example .env
Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
notepad compose.media.yaml
```

The last command opens the media-folder mapping file in Notepad.

### 5. Tell InfoMancer where your media is

The example file contains sample Movie and TV folders. Replace them with your real folders.

For example:

```yaml
- source: D:/Movies
  target: /media/movies
```

Keep forward slashes in the YAML file.

If your TV shows are on another disk, that is fine:

```yaml
- source: E:/TV
  target: /media/tv
```

The left side is the real Windows folder. The right side is the simple path InfoMancer will see.

Remove mappings you do not need. Duplicate a complete mapping block if you have more disks or folders.

Save and close Notepad.

### 6. Start InfoMancer Server

Run:

```powershell
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

The first start may take a few minutes because Docker has to build the application environment.

### 7. Open InfoMancer

Open:

```text
http://127.0.0.1:8787
```

`127.0.0.1` means **this computer**.

Create the first Librarian account and follow Guided Setup.

When Guided Setup asks for media folders, choose the InfoMancer paths you created, such as:

```text
/media/movies
/media/tv
```

Do not choose `D:\Movies` inside InfoMancer. Docker has already translated that folder to `/media/movies`.

### Windows network shares

A UNC share can be used as a source, for example:

```text
//server/share/Movies
```

Make sure the share already works in Windows and that Docker Desktop is allowed to access it.

A mapped drive that exists only inside one Windows login session may not be visible to Docker. A UNC path is usually clearer for a server-style installation.

## macOS

### 1. Install Docker Desktop

Install and start [Docker Desktop for macOS](https://docs.docker.com/desktop/setup/install/mac-install/).

### 2. Extract InfoMancer Server

Download and extract the server ZIP to a permanent folder.

### 3. Open Terminal in that folder

Create the local configuration files:

```bash
cp .env.example .env
cp deploy/macos.compose.yaml.example compose.media.yaml
open -e compose.media.yaml
```

### 4. Add your media folders

Replace the example `/Volumes/Media/...` paths with the real locations of your Movie and TV folders.

A mapping might look like:

```yaml
- source: /Volumes/Media/Movies
  target: /media/movies
```

The files stay on the external or mounted volume. InfoMancer sees them as `/media/movies`.

### 5. Start InfoMancer Server

Run:

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

### 6. Open InfoMancer

Open:

```text
http://127.0.0.1:8787
```

Create the first Librarian and complete Guided Setup.

If macOS or Docker Desktop asks for permission to access an external disk or folder, approve that location before scanning it.

## Linux

These instructions work for Docker-supported Linux distributions including Ubuntu and Debian. Linux Mint commonly follows the instructions for its Ubuntu or Debian base.

### 1. Install Docker

Install Docker Engine from Docker's repository and install the Docker Compose plugin for your distribution.

### 2. Extract InfoMancer Server

Put the extracted server folder somewhere permanent, for example:

```text
/opt/infomancer
```

or a folder in your home directory.

### 3. Create the local configuration files

From the InfoMancer folder, run:

```bash
cp .env.example .env
cp deploy/linux.compose.yaml.example compose.media.yaml
nano compose.media.yaml
```

### 4. Add your media folders

Replace the sample paths with the real mounted storage paths.

For example:

```yaml
- source: /mnt/media/movies
  target: /media/movies
```

Save the file.

### 5. Start InfoMancer Server

Run:

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

### 6. Open InfoMancer

If the Linux machine has a desktop and you are sitting at it, open:

```text
http://127.0.0.1:8787
```

If the Linux machine is headless or is a dedicated server, use an SSH tunnel from your own computer:

```bash
ssh -L 8787:127.0.0.1:8787 user@server-address
```

Keep that terminal window open, then open this on your own computer:

```text
http://127.0.0.1:8787
```

The SSH tunnel safely carries that browser connection to InfoMancer Server.

Create the first Librarian and complete Guided Setup.

## Local install vs dedicated server, in plain language

### Local install

Everything happens on one computer:

```text
Your browser
    ↓
InfoMancer Server
    ↓
Your media folders
```

You normally open `http://127.0.0.1:8787`.

This is the easiest setup and is a good place to start.

### Dedicated server install

InfoMancer Server runs on another computer:

```text
Your laptop / desktop / phone
          ↓
   secure connection
          ↓
   InfoMancer Server
          ↓
      media storage
```

The dedicated machine must be on for InfoMancer to be available.

The current Docker package does **not** expose port `8787` to the whole network by default. This prevents an accidental insecure installation.

For initial setup on a headless Linux server, use the SSH tunnel shown above. For permanent access, use a VPN or authenticated reverse proxy. See [Remote access with Cloudflare](REMOTE_ACCESS.md) for the included Cloudflare Tunnel option.

Do not forward port `8787` directly from your router to the Internet.

## Sign-in and Internet access

InfoMancer's current username/password login is handled by InfoMancer Server itself.

You can sign in locally even when the server has no Internet connection.

Internet access is still required for features that contact outside metadata services such as TVDB or IMDb.

## Confirm that InfoMancer is running

From the InfoMancer folder, run:

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

The `infomancer` service should show as running and then healthy.

If it does not, run:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

The log usually explains what failed.

Before sharing logs with anyone, remove API keys, session information, private filenames, and personal paths.

## Start and stop InfoMancer later

Start it again with:

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d
```

Stop it with:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Stopping InfoMancer does not delete the catalog or your media.

## Updates

Before updating, back up the `data` folder.

Then:

1. Stop InfoMancer:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml down
   ```

2. Replace the application files with the new InfoMancer Server release.
3. Keep your existing `.env`, `compose.media.yaml`, and `data` folder.
4. Rebuild and start:

   ```bash
   docker compose -f compose.yaml -f compose.media.yaml up -d --build
   ```

Database updates run automatically when the new version starts.

## Backups

Back up these three items together:

- `data/` contains the catalog, users, settings, encrypted provider credentials, and local application data.
- `.env` contains protected installation configuration.
- `compose.media.yaml` records which real folders are mapped into InfoMancer.

Your actual Movie and TV files are not stored in the InfoMancer application folder.

The catalog can be rebuilt by scanning the media again, but user accounts, personal organization, settings, and original-filename history may not be recoverable without a backup.

## Uninstall

Stop the server:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

Then delete the InfoMancer Server application folder if you no longer want the installation.

Deleting that folder removes InfoMancer's catalog and settings. It does **not** delete the Movie or TV files in the folders you mapped.

## Advanced native installation

Running InfoMancer directly with Python is intended for development and troubleshooting. It is not the normal installation path.

It requires Python 3.13, FFmpeg/FFprobe, and manual setup for starting and stopping the application:

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
# Windows: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8787
```

For native use, `MEDIA_BROWSE_ROOTS` must contain real paths for that operating system. Separate multiple paths with commas.

Unless you are developing or troubleshooting InfoMancer, use the Docker instructions above instead.
