<p align="center">
  <img src="infomancer-lockup.svg" alt="InfoMancer" width="620">
</p>

<p align="center"><strong>Your media library, understood.</strong></p>
<p align="center">A local-first media catalog, intelligence, review, and organization workspace for Movie and TV libraries.</p>

<p align="center">
  <img alt="Release 0.8.1 alpha.1" src="https://img.shields.io/badge/release-0.8.1--alpha.1-B7FF2A?style=flat-square&labelColor=11161d">
  <img alt="Windows, macOS, Linux, Docker" src="https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-26313d?style=flat-square&labelColor=11161d">
  <img alt="Local first" src="https://img.shields.io/badge/design-local--first-26313d?style=flat-square&labelColor=11161d">
</p>

<p align="center">
  <a href="https://github.com/chandler-sol/InfoMancer/releases"><strong>Download</strong></a> ·
  <a href="docs/INSTALLATION.md"><strong>Install</strong></a> ·
  <a href="docs/reference/FEATURE_CATALOG.md"><strong>Features</strong></a> ·
  <a href="docs/REMOTE_ACCESS.md"><strong>Remote access</strong></a>
</p>

InfoMancer is built for media libraries that have outgrown a folder browser. It catalogs media across local disks, mounted storage, and network shares, enriches titles with metadata, inspects technical characteristics, surfaces problems through the Media Intelligence Engine, and keeps potentially destructive work behind explicit review and safety checks.

> **0.8 is alpha software.** Back up a catalog you care about before upgrades, and expect packaging and compatibility to keep improving on the way to 1.0.

<!-- README SCREENSHOT SLOT 1: docs/assets/readme/dashboard-hero.png
     Wide 16:9 capture of the populated Dashboard. No open menus or personal paths.
-->

## Four jobs, one workspace

| | |
| --- | --- |
| **Catalog** | Scan multiple Movie and TV sources into one searchable SQLite-backed library without moving media. |
| **Understand** | Use metadata, FFprobe inspection, episode data, and MIE findings to see what is healthy, missing, unusual, or unresolved. |
| **Review** | Work through matching, missing episodes, duplicates, quality issues, and proposed changes with context instead of disconnected scripts. |
| **Act safely** | Preview supported filesystem changes, block collisions, constrain operations to configured sources, and retain history for guarded recovery. |

## Built for real libraries

InfoMancer 0.8 includes:

- Movie and TV cataloging across multiple sources
- Covers and dense List views with search, filters, Saved Views, and sorting
- a persistent Library Inspector plus deeper title-detail pages
- TVDB matching, metadata, artwork, credits, and expected episode data
- episode-aware TV handling, including multi-episode files and missing aired episodes
- bundled FFprobe inspection for runtime, resolution, codecs, audio, bitrate, container, and dynamic range
- Media Intelligence Engine findings with severity, evidence, explanation, and recommendations
- Review workflows for unresolved media and proposed work
- Collections, Smart Collections, Custom Libraries, Favorites, ratings, and tags
- duplicate review, Managed Trash, guarded restore, Operation History, and Safe Undo where supported
- recovery packages, database backups, and portable exports
- Librarian and Member accounts with role-aware permissions

The complete inventory lives in the **[Feature Catalog](docs/reference/FEATURE_CATALOG.md)**.

<!-- README SCREENSHOT SLOT 2: docs/assets/readme/library-covers.png
     Wide Library Covers view with real posters, toolbar/filter controls visible, inspector closed.
-->

## Understand the library without losing your place

Library is the main workspace. Switch between Movies and TV, Covers and List layouts, then combine filters, Saved Views, Collections, tags, ratings, favorites, and custom sorting.

Select a title to open the Inspector beside the library. When something needs deeper work, open the full title page for metadata, files, media inspection, matching, episode coverage, organization, editions, and other title-specific tools.

<!-- README SCREENSHOT SLOT 3: docs/assets/readme/library-inspector.png
     Library with one title selected and the Inspector open. Avoid exposing private filesystem paths.
-->

## Media Intelligence that explains itself

MIE analyzes facts already stored in the catalog. It can surface identity problems, unreadable inspection results, stale or incomplete metadata, missing aired episodes, unusual episode coverage, technical consistency concerns, and storage opportunities.

A finding includes the evidence and recommendation behind it. InfoMancer should tell you **why** something deserves attention before asking you to change anything.

## Filesystem safety is part of the product

Scanning is read-only. Supported mutation workflows add additional layers:

- preview before apply
- collision blocking instead of overwrite
- source-boundary validation
- revalidation immediately before a change
- Read-Only, Standard, and Lockdown protection modes
- durable Operation History
- guarded undo and restore when the current state can still be verified

InfoMancer is designed to understand a library first, then let you decide what should change.

<!-- README SCREENSHOT SLOT 4: docs/assets/readme/review-workspace.png
     Review Workspace with several realistic categories/items visible and no private paths.
-->

## Choose how you want to run InfoMancer

| Mode | Best for | What 0.8 does |
| --- | --- | --- |
| **Native standalone** | One Windows, Mac, or Linux computer | Runs a bundled InfoMancer core and local catalog on that computer. |
| **Native client** | A desktop connecting to an existing server | Uses the native shell while the server owns the catalog and media access. |
| **Docker / server** | Shared, always-on, headless, or remotely accessed installations | Runs InfoMancer independently of a desktop login and can serve multiple clients. |

> **Important:** In 0.8, **Run on this computer is local-only**. The bundled desktop core listens only on the local machine and is not an InfoMancer server for other devices. Use **Connect to a server** with a separate server deployment when you need shared or remote access.

## Download 0.8.1 alpha.1

Choose the package whose name matches your platform:

| Platform | Download |
| --- | --- |
| Windows 10/11 x64 | `InfoMancer-0.8.1-alpha.1-Windows-x64-Setup.exe` |
| macOS, Apple Silicon | `InfoMancer-0.8.1-alpha.1-macOS-Apple-Silicon.dmg` |
| macOS, Intel | `InfoMancer-0.8.1-alpha.1-macOS-Intel.dmg` |
| Debian / Ubuntu / Linux Mint x86-64 | `InfoMancer-0.8.1-alpha.1-Linux-x86_64.deb` |
| Other Linux x86-64 desktops | `InfoMancer-0.8.1-alpha.1-Linux-x86_64.AppImage` |
| Server / Docker | `InfoMancer-0.8.1-alpha.1.zip` |

Then follow **[Install InfoMancer](docs/INSTALLATION.md)**.

Native alpha packages are not yet fully platform-signed/notarized, so Windows SmartScreen or macOS Gatekeeper may show a warning. Download only from the official GitHub release and verify `SHA256SUMS.txt` when testing an alpha package.

## Remote access

For a shared or always-on installation, use the server deployment. Do not expose an InfoMancer HTTP port directly to the public internet. Use an authenticated reverse proxy, VPN, or the documented Cloudflare Access/Tunnel path.

See **[Remote access](docs/REMOTE_ACCESS.md)**.

## Recovery and ownership

InfoMancer is local-first. The catalog is SQLite, and your Movie and TV files stay where they already live.

Portable `.infomancer-backup` packages can include the catalog and InfoMancer-managed artwork with integrity verification. They do **not** include your media files, deployment secrets, provider-secret keys, application binaries, or caches.

## Documentation

- **[Installation](docs/INSTALLATION.md)**
- **[Feature Catalog](docs/reference/FEATURE_CATALOG.md)**
- **[Remote Access](docs/REMOTE_ACCESS.md)**
- **[Updates](docs/UPDATES.md)**
- **[CLI](docs/CLI.md)**
- **[Packaging](docs/PACKAGING.md)**

## Development

InfoMancer uses Python 3.13, FastAPI, SQLite, Jinja, JavaScript/CSS, and Tauri. The repository runs cross-platform regression tests plus browser acceptance and dependency/security audits.

```bash
python -m unittest discover -s tests -v
```

InfoMancer remains an alpha project and a final open-source license has not yet been selected. Current release work focuses on platform qualification, signing/notarization, performance, accessibility, data durability, and the remaining path to a stable 1.0.
