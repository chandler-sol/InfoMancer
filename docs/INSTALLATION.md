# Install InfoMancer

InfoMancer 0.8.1-beta.2 has two simple installation choices:

| What you want | Install |
| --- | --- |
| One computer with its own private catalog | **InfoMancer Desktop** |
| One shared catalog for several computers | **InfoMancer Server** |

Your Movie and TV files stay where they already are.

# InfoMancer Server

Use Server when the catalog should live on an always-on computer, home server, or NAS-capable Docker host and be shared with other devices.

For a normal home install, you need only:

- Docker
- the InfoMancer Server ZIP
- the locations of your Movies and TV Shows

You do **not** need Python, Node, Rust, a separate database server, or Cloudflare.

## Quick install

### 1. Install Docker

Install Docker and make sure it is running.

### 2. Download InfoMancer Server

Download and extract:

`InfoMancer-Server-0.8.1-beta.2.zip`

Keep the extracted folder somewhere permanent. It will also hold InfoMancer's local configuration and catalog data.

### 3. Run the setup helper

**Windows**

Double-click:

`Setup-InfoMancer.cmd`

**macOS**

Double-click:

`Setup-InfoMancer.command`

If macOS blocks it, right-click the file and choose **Open**.

**Linux**

Open Terminal in the extracted folder and run:

```bash
./setup-infomancer.sh
```

If that reports a permission problem, run:

```bash
sh setup-infomancer.sh
```

### 4. Tell InfoMancer where your media lives

The helper asks for your Movies folder and TV Shows folder. You can leave one blank if you do not have it.

Examples:

```text
Windows: D:\Movies
macOS:   /Volumes/Media/Movies
Linux:   /media/storage/Movies
```

The helper creates the needed config files for you. It also sets the Linux user/group values automatically when needed.

It does **not** move, copy, rename, or delete media during setup.

### 5. Open InfoMancer

The helper starts the Server, waits for it to become ready, then prints an address and a one-time setup code.

It will look similar to:

```text
InfoMancer Server is ready.

From another computer on this network:
  http://192.168.1.50:8787

One-time setup code:
  example-code-here
```

Create the first **Librarian** account and paste in that one-time code.

From another computer, the general address is:

`http://SERVER-IP:8787`

After that, install InfoMancer Desktop on other computers and choose **Connect to a server**.

> **Do not port-forward port 8787 to the public Internet.** Local-network access is built in. For access away from home, use a VPN or the documented authenticated setup in [Remote Access](REMOTE_ACCESS.md).

## If setup fails

The helper should normally tell you what went wrong. For more detail, run this from the Server folder:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

For manual Docker setup, unusual storage layouts, permissions, and detailed troubleshooting, see **[Manual Server Setup](SERVER_MANUAL.md)**.

If you are doing the manual setup yourself, **Only change `source:`** for the host media path and leave the `/media/...` target alone. The first-run log line is `InfoMancer first-run bootstrap token:`.

# InfoMancer Desktop

Desktop is for either:

- **Run on this computer**: one private catalog on that computer
- **Connect to a server**: use an existing InfoMancer Server

Download the package that matches the computer:

| Platform | File |
| --- | --- |
| Windows 10/11 x64 | `InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe` |
| Mac with Apple Silicon | `InfoMancer-0.8.1-beta.2-macOS-Apple-Silicon.dmg` |
| Mac with Intel processor | `InfoMancer-0.8.1-beta.2-macOS-Intel.dmg` |
| Debian / Ubuntu / Linux Mint x86-64 | `InfoMancer-0.8.1-beta.2-Linux-x86_64.deb` |
| Other Linux x86-64 desktops | `InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage` |

## Windows Desktop

1. Run `InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe`.
2. Launch **InfoMancer**.
3. Choose **Run on this computer** or **Connect to a server**.

Beta 2 is not yet Authenticode-signed, so Windows SmartScreen may show an unknown-publisher warning.

## macOS Desktop

1. Open the DMG that matches your Mac.
2. Move **InfoMancer** into Applications.
3. Launch InfoMancer.
4. Choose **Run on this computer** or **Connect to a server**.

If macOS blocks the first launch, open **System Settings > Privacy & Security** and choose **Open Anyway** for InfoMancer. Beta 2 is not yet Apple-notarized.

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

Then choose **Run on this computer** or **Connect to a server**.

# Updating and backups

Before a beta update, make a backup if the catalog matters to you.

- **Desktop:** use InfoMancer's Recovery tools to create a `.infomancer-backup`.
- **Server:** protect `data/`, `.env`, and `compose.media.yaml` together.

See **[Updating InfoMancer](UPDATES.md)** for the update steps.

# More help

- **[Manual Server Setup](SERVER_MANUAL.md)**
- **[Remote Access](REMOTE_ACCESS.md)**
- **[Updating InfoMancer](UPDATES.md)**
- **[Feature Catalog](reference/FEATURE_CATALOG.md)**

Direct Python execution and detailed Docker configuration are development/advanced paths, not the normal installation method.
