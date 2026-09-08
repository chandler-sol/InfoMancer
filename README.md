# InfoMancer

**Website:** [infomancer.media](https://infomancer.media/)

![InfoMancer library intelligence dashboard](docs/Infomancer1.png)

InfoMancer is a self-hosted movie and TV library manager. It scans the media you already own, builds a searchable catalog, helps match titles and find missing episodes, checks library health, and previews filename changes before anything is renamed.

Your catalog, accounts, settings, and media stay under your control.

## What do I install?

Today there is one installable package: **InfoMancer Server**.

The word **Server** does not mean cloud hosting, a special server computer, or a paid service. It simply means this is the copy of InfoMancer that stores the catalog and does the work.

You install InfoMancer Server **once**.

| Setup | What it means | How you use InfoMancer |
| --- | --- | --- |
| **Local install** | InfoMancer Server runs on the same computer you are using | Open it in that computer's web browser |
| **Dedicated server install** | InfoMancer Server runs on another computer that stays on | Open it from another device through a secure connection |
| **Browser** | The current InfoMancer client | Nothing extra to install |

A local install and a dedicated server install use the **same InfoMancer Server package**. The only difference is where it runs.

**Local-first** describes how InfoMancer handles your data. It does not mean InfoMancer can only be used on one computer.

InfoMancer currently listens only on the computer running it by default. That is intentional. If you install it on a headless or dedicated server, the installation guide explains how to reach it safely.

## What InfoMancer can do

- Scan multiple Movie and TV folders without moving the media
- Catalog local disks, mounted storage, and supported network shares
- Search titles and filenames quickly
- Match Movie and TV metadata through TVDB and IMDb
- Report missing aired TV episodes
- Preview bulk matching and filename changes before applying them
- Rename TV show folders and episode files using Plex-friendly naming
- Restore original filenames after InfoMancer renames them
- Inspect resolution, codecs, bitrate, container, runtime, and HDR/SDR information
- Find duplicate copies and verify identical files with fingerprints
- Report library-health problems and explain why they were flagged
- Support Librarian and Member accounts
- Back up the catalog and portable settings
- Track background work and newly discovered media

Scanning is non-destructive. InfoMancer does not rename, move, or delete media during a normal scan.

## Install InfoMancer Server

Docker is the recommended installation method on Windows, macOS, and Linux. You do not need to understand Docker to use InfoMancer. The installation guide walks through the required commands and explains what each file is for.

Download the current server release, named like:

```text
InfoMancer-Server-VERSION.zip
```

Then follow **[Install InfoMancer Server](docs/INSTALLATION.md)**.

For a first installation, the simplest choice is to run InfoMancer Server on the computer where your media is already available.

The short version is:

1. Install Docker Desktop, or Docker Engine with Compose on Linux.
2. Extract the InfoMancer Server ZIP to a permanent folder.
3. Copy the included example configuration files.
4. Tell InfoMancer which Movie and TV folders it may see.
5. Start InfoMancer Server.
6. Open `http://127.0.0.1:8787` and follow Guided Setup.

`127.0.0.1` means **this computer**. It does not send your InfoMancer session over the Internet.

## Accounts and sign-in

Current InfoMancer releases use a local username or email and password.

That login is handled by your InfoMancer Server. **Internet access is not required to sign in with a local account.** Existing local sessions also remain local to the installation.

Internet access is still needed for features that contact outside services, such as TVDB or IMDb metadata updates.

On the first visit, InfoMancer asks you to create the first **Librarian** account.

- **Librarians** can manage sources, metadata, users, scans, and file changes.
- **Members** can browse and search the library without filesystem or administrative access.

Librarians can open **Profile → Users** to add people. New users receive a one-time setup link and choose their own password.

Use **Profile → Password** to change your password and **Profile → Sessions** to sign out other browsers.

Third-party sign-in such as Apple, Google, Microsoft, and GitHub is planned for a later release. Local username/password access will remain available even when those options are added.

## Media folders and permissions

InfoMancer does not copy your media into its own application folder.

For Docker installs, you map your real folders to simple names that InfoMancer can see. For example:

```text
Windows folder: D:\Movies
InfoMancer sees: /media/movies
```

You then choose `/media/movies` inside Guided Setup.

InfoMancer needs:

- **Read access** to catalog a folder
- **Write access** only if you want InfoMancer to rename files in that folder

The installation guide includes Windows, macOS, Linux, external-drive, and network-share examples.

## Settings

Librarians can open **Settings** from the main menu or account menu.

- **General**: time zone and library display defaults
- **Metadata & Matching**: TVDB setup and IMDb metadata maintenance
- **External Search**: the site used by missing-media search links
- **System**: database health, backups, updates, media inspection, logs, and service controls

Settings exports intentionally leave out passwords, accounts, sessions, API credentials, encryption keys, media sources, and media files.

## Backups

The most important files to protect are:

- `data/` for the catalog, accounts, settings, and application data
- `.env` for protected installation configuration
- `compose.media.yaml` for your media-folder mappings

The media itself is not stored inside the InfoMancer application folder.

See the installation guide for backup, update, and uninstall steps.

## Remote and dedicated-server access

InfoMancer is deliberately conservative about network access. The default Docker package binds the web interface to the machine running InfoMancer Server instead of exposing it to the whole network.

For a headless Linux server, the installation guide shows a simple SSH tunnel for setup. For permanent access away from the server, use a VPN or authenticated reverse proxy. Do not expose port `8787` directly to the Internet.

See **[Remote access with Cloudflare](docs/REMOTE_ACCESS.md)** for the included Cloudflare Tunnel option.

Cloudflare Access can protect a public hostname, but InfoMancer still uses its own local account login.

## Guided Setup

After creating the first Librarian, InfoMancer offers Guided Setup or Manual Setup.

Guided Setup walks through:

1. Basic installation preferences
2. TVDB credentials
3. Movie and TV folders
4. The first scan

You can reopen Setup Assistant later from Help, App Settings, or the Profile menu.

## Updates and packaging

The current public-beta package is **InfoMancer Server** as a release ZIP with Docker Compose.

Future native installers may make InfoMancer Server easier to install as a background service on Windows, macOS, and Linux. A future desktop client, if built, would be a separate product surface rather than another copy of the server.

See the **[cross-platform packaging plan](docs/PACKAGING.md)** and **[release review checklist](docs/RELEASE_REVIEW.md)**.

## Advanced tools

InfoMancer includes a command-line interface for diagnostics, scans, exports, backups, optimization, logs, and Librarian recovery.

```bash
python -m app.cli --help
python -m app.cli status
python -m app.cli doctor
```

Docker users can run the same commands with `docker compose exec infomancer`.

See the **[command-line guide](docs/CLI.md)**.

Running InfoMancer directly from Python is supported for development and troubleshooting, but it is not the recommended installation path for normal users.

## Safe operating model

- Scanning never renames, moves, or deletes media.
- Removing a source removes catalog records, not the media files.
- Filesystem renames have a review step showing the old and new paths.
- InfoMancer refuses to overwrite an existing destination during a rename.
- Search-provider links do not start downloads.
- Back up the catalog and test rename workflows on a small sample before using them broadly.

## Current boundaries

InfoMancer does not currently reorganize TV shows into season folders, download missing media, or scrape download-result pages. Direct Apple, Google, Microsoft, and GitHub sign-in is not yet enabled.
