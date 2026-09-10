<p align="center">
  <img src="infomancer-lockup.svg" alt="InfoMancer" width="620">
</p>

<p align="center"><strong>Your media library, understood.</strong></p>
<p align="center">InfoMancer helps you catalog, inspect, review, and safely organize Movie and TV libraries you already own.</p>

<p align="center">
  <img alt="Release 0.8.1 beta.2" src="https://img.shields.io/badge/release-0.8.1--beta.2-B7FF2A?style=flat-square&labelColor=11161d">
  <img alt="Windows, macOS, Linux, Server" src="https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Server-26313d?style=flat-square&labelColor=11161d">
</p>

<p align="center">
  <a href="https://github.com/chandler-sol/InfoMancer/releases"><strong>Download</strong></a> ·
  <a href="docs/INSTALLATION.md"><strong>Install</strong></a> ·
  <a href="docs/reference/FEATURE_CATALOG.md"><strong>Features</strong></a> ·
  <a href="docs/REMOTE_ACCESS.md"><strong>Remote access</strong></a>
</p>

InfoMancer works with the media files you already have. It does not require you to move your library into a special folder, and scanning does not change your files.

> **0.8.1 is beta software.** Keep a backup of any catalog you care about while testing.

## Start here

Pick the setup that matches what you want:

| I want to... | Use |
| --- | --- |
| Use InfoMancer on one computer only | **InfoMancer Desktop** and choose **Run on this computer** |
| Share one catalog between several computers | **InfoMancer Server**, then connect Desktop clients to it |
| Use an existing InfoMancer Server | **InfoMancer Desktop** and choose **Connect to a server** |

**Desktop is not Server.** A Desktop install using **Run on this computer** stays local to that computer. Use InfoMancer Server when several devices need the same catalog.

## Download 0.8.1-beta.2

| Platform / product | Download |
| --- | --- |
| Windows 10/11 x64 Desktop | `InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe` |
| macOS Desktop, Apple Silicon | `InfoMancer-0.8.1-beta.2-macOS-Apple-Silicon.dmg` |
| macOS Desktop, Intel | `InfoMancer-0.8.1-beta.2-macOS-Intel.dmg` |
| Debian / Ubuntu / Linux Mint Desktop x86-64 | `InfoMancer-0.8.1-beta.2-Linux-x86_64.deb` |
| Other Linux Desktop x86-64 | `InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage` |
| **InfoMancer Server** | `InfoMancer-Server-0.8.1-beta.2.zip` |

## Installing InfoMancer Server is meant to be simple

For a normal home Server install:

1. Download and extract `InfoMancer-Server-0.8.1-beta.2.zip`.
2. Run the setup helper for your operating system.
3. Let the helper check Docker. InfoMancer Server requires Docker Engine **24.0+** and Docker Compose **2.20+**.
4. Tell it where your Movies and TV Shows live.
5. Open the address it prints and paste in the one-time setup code.

If Docker is missing or too old, the helper tells you what needs attention and offers the official Docker installation/update instructions. On Windows, if `winget` is available, it can also offer to install or update Docker Desktop.

The Server ZIP includes:

- `Setup-InfoMancer.cmd` for Windows
- `Setup-InfoMancer.command` for macOS
- `setup-infomancer.sh` for Linux
- `START-HERE.txt` for the shortest instructions

You do not need Python, Node, Rust, a separate database server, or Cloudflare for a normal local-network install.

See **[Installation](docs/INSTALLATION.md)** for the full first-time walkthrough and Docker links.

## What InfoMancer does

InfoMancer can:

- catalog Movies and TV Shows across several folders or drives
- add metadata, artwork, credits, episode information, and technical media details
- help match titles that were not identified correctly
- find missing episodes, duplicates, unusual files, and other library problems
- organize the library with Collections, tags, ratings, Favorites, Saved Views, and custom sorting
- preview supported file changes before anything is applied
- keep operation history, recovery tools, backups, and guarded restore options
- support Librarian and Member accounts on shared Server installs

For the detailed feature list, see the **[Feature Catalog](docs/reference/FEATURE_CATALOG.md)**.

## File safety

Scanning is read-only. InfoMancer does not rename, move, or delete media simply because it found something wrong.

When you choose a supported file-changing action, InfoMancer is designed to show the proposed change first, block obvious collisions, and keep recovery information where possible.

## Remote access

A normal InfoMancer Server works on your trusted local network without Cloudflare.

**Do not port-forward port 8787 directly to the public Internet.** If you want access away from home, use a VPN or the documented authenticated remote-access setup.

See **[Remote Access](docs/REMOTE_ACCESS.md)**.

## Documentation

- **[Installation](docs/INSTALLATION.md)**
- **[Feature Catalog](docs/reference/FEATURE_CATALOG.md)**
- **[Remote Access](docs/REMOTE_ACCESS.md)**
- **[Updates](docs/UPDATES.md)**
- **[Server manual setup](docs/SERVER_MANUAL.md)**
- **[CLI](docs/CLI.md)**
- **[Packaging](docs/PACKAGING.md)**

## Development

Developer and packaging details are intentionally kept out of the normal installation path. If you are working on InfoMancer itself, the repository uses Python 3.13, FastAPI, SQLite, JavaScript/CSS, and Tauri.

```bash
python -m unittest discover -s tests -v
```

InfoMancer remains a beta project and a final open-source license has not yet been selected.
