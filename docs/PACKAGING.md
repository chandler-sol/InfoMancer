# Cross-platform packaging plan

## Recommendation

Ship InfoMancer in two stages:

1. **First public beta:** a release ZIP plus Docker Compose and, later, a
   prebuilt multi-architecture container image.
2. **Later desktop releases:** signed native installers that run InfoMancer as
   a local background service and open its web interface in the default
   browser.

Docker should remain a supported installation even after native installers
exist. It is the best fit for NAS devices, headless servers, and users who want
InfoMancer available to several computers.

## Why a native installer is more than wrapping the current Python process

A polished native package must decide and test:

- where the database, encryption key, configuration, and logs live;
- how InfoMancer starts, stops, restarts, and updates;
- how it selects a free local port and opens the browser;
- how media folders and network shares are granted;
- how FFprobe is bundled and licensed;
- how an uninstall preserves or removes application data;
- how crashes are reported when no terminal window is visible;
- how the application and installer are signed.

The application logic is portable, but those lifecycle pieces must be added
before native packages are safe for non-technical users.

## Windows

Preferred deliverable: a signed **MSI** installer. An `.exe` can be the
installed launcher, but an MSI provides familiar install, repair, upgrade, and
uninstall behavior.

Candidate build route:

- Package Python and InfoMancer with Briefcase or PyInstaller.
- Use Briefcase/WiX or a dedicated WiX project to produce the MSI.
- Install per-user initially to avoid unnecessary administrator requirements.
- Run a tray/background launcher rather than an always-visible console.
- Sign both the launcher and installer with a trusted code-signing certificate.

### Windows uninstall contract

The Windows uninstaller must leave no InfoMancer-owned state behind unless the
user explicitly chooses to save a recovery package. Before destructive removal,
offer **Create recovery backup & uninstall**, **Uninstall everything**, and
**Cancel**. A requested recovery package must be written to a user-selected
location outside InfoMancer-managed directories and verified before uninstall
continues; if creation or verification fails, keep InfoMancer installed unless
the user explicitly chooses to proceed without the backup.

The recovery choice uses InfoMancer's portable `.infomancer-backup` format. The
package contains a verified SQLite snapshot plus collection artwork and a signed-by-
content manifest of SHA-256 checksums. The database carries accounts, catalog data,
source definitions, settings, collections, favorites, tags, and operation history.
Media files, provider credentials, local encryption keys, deployment environment
files, binaries, and caches are intentionally excluded. Provider credentials must be
entered again after recovery. The same package creator/verifier is exposed in App
Settings so users can test the format before a native installer exists.

A complete uninstall removes application binaries, databases, configuration,
provider-secret/encryption-key files, artwork, caches, logs, updater data,
crash data created by InfoMancer, shortcuts, services, scheduled tasks, file or
protocol associations, firewall rules, and registry values created by
InfoMancer. The cleanup implementation should use an explicit ownership
manifest rather than searching the whole machine by product name. **Media files and user-selected recovery packages are never deleted.** Installer tests
must create representative state, uninstall, and assert that every registered
InfoMancer-owned resource is gone.

Test Windows 11 first, then supported Windows 10 editions while they remain in
scope. Include local NTFS folders, removable drives, UNC shares, and unavailable
network shares in the test matrix.

## macOS

Preferred deliverable: a universal or separate architecture **DMG** containing
a signed `.app`.

The build must run on macOS. Produce and test Apple silicon and Intel artifacts,
sign with Developer ID, enable the hardened runtime as appropriate, submit for
Apple notarization, and staple the notarization result. Test access to external
volumes and network shares because macOS privacy controls can block storage
that works in Terminal.

## Linux

Recommended order:

1. Docker Compose for all supported distributions.
2. `.deb` packages for current Ubuntu and Debian releases, covering Linux Mint
   through its Ubuntu base.
3. `.rpm` for Fedora and RHEL-compatible systems if demand justifies it.
4. Flatpak only if InfoMancer develops a true desktop mode and its broad
   filesystem access can be explained and granted cleanly.
5. Arch packages only after there is a maintainer or demonstrated demand.

Do not prioritize AppImage. Briefcase currently recommends native system
packages or Flatpak instead because its AppImage backend is best-effort and can
introduce binary and desktop-integration problems.

Native Linux packages should integrate with systemd for a server install or a
desktop launcher for a personal install. Distribution packages need separate
testing because system Python and library versions differ between releases.

## Build infrastructure

Native artifacts cannot be treated as one cross-compiled binary. Use a release
matrix with a real Windows runner, a real macOS runner, and Linux runners for
each native target. Store signing credentials only in protected release
environments and never expose them to pull-request jobs.

Before enabling installer builds, add:

- an application version source shared by the UI and packages;
- deterministic database migration tests across released versions;
- an update manifest and rollback policy;
- installer smoke tests on clean virtual machines;
- Software Bill of Materials and dependency/license reporting;
- checksums and signed release notes.

## Primary packaging references

- [PyInstaller platform support](https://pyinstaller.org/en/stable/) builds
  must run separately on Windows, macOS, and Linux.
- [Briefcase Windows packaging](https://briefcase.beeware.org/en/stable/reference/platforms/windows/)
  covers Windows application folders and MSI installers.
- [Briefcase macOS packaging](https://briefcase.beeware.org/en/latest/reference/platforms/macOS/)
  covers signed/notarized app bundles, DMG, and PKG outputs.
- [Briefcase Linux system packages](https://briefcase.beeware.org/en/stable/reference/platforms/linux/system/)
  covers distribution-specific DEB, RPM, and Arch-family packages.
- [Apple Developer ID](https://developer.apple.com/support/developer-id/) and
  [macOS distribution](https://developer.apple.com/macos/distribution/) cover
  signing and notarization requirements.
- [Microsoft code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
  covers Windows trust and signing choices.

## Windows desktop alpha implementation

The Windows desktop shell is now carried in `desktop/` and built as an NSIS
current-user installer. It bundles the current InfoMancer Python core as a
PyInstaller sidecar and stores standalone state under Tauri's
`cloud.arsenik.infomancer` application-data directory.

The NSIS confirmation deliberately treats application-data deletion as mandatory
for a normal uninstall. Before destructive removal, an installation with a local
database offers to create and verify a portable `.infomancer-backup` at a
user-selected destination. Updates use Tauri's `/UPDATE` path and explicitly skip
the purge so a binary upgrade never erases the catalog. Silent uninstall is
reserved for unattended/CI use and performs the zero-residue purge without an
interactive backup prompt.

The desktop build has an automated Windows smoke test that seeds known Roaming and
Local AppData paths, silently uninstalls InfoMancer, and fails if owned state or
installer registration survives.

### Bundled FFprobe contract

Native desktop packages include FFprobe because media inspection must work on a
clean machine without requiring the user to install FFmpeg or modify `PATH`.
Only FFprobe is bundled for this feature. The full FFmpeg executable is not
included.

The approved Windows and Linux binaries are pinned BtbN/FFmpeg-Builds **LGPL**
static variants for FFmpeg `n9.0.1-29-gad500d59cb`. The exact FFmpeg source
commit and BtbN build-scripts commit are pinned alongside each archive SHA-256 in
`scripts/stage_ffprobe.py`; moving `latest` aliases are not used.

The staging script verifies the downloaded archive, extracts only FFprobe, then
executes the staged binary with `ffprobe -version`. Packaging fails if the binary
does not report the expected version or if its configure output contains
`--enable-gpl` or `--enable-nonfree`. The LGPL v2.1 license is fetched from the
exact FFmpeg source commit and its Git blob identity is verified before it is
included.

The executable plus `FFPROBE_LICENSE.txt`, `FFPROBE_NOTICE.txt`, and
`FFPROBE_BUILDINFO.txt` are added to the PyInstaller core. Before Tauri creates a
native installer, CI also executes the finished core with `--check-ffprobe` and
requires the bundled executable to run successfully.

At runtime, media inspection resolves FFprobe in this order:

1. `INFOMANCER_FFPROBE`, when an operator explicitly provides an override.
2. The FFprobe executable embedded in an approved native PyInstaller build.
3. A system `ffprobe` available on `PATH`, which remains useful for source and
   server installations.

For tagged Windows releases, the exact FFmpeg corresponding-source archive, the
exact BtbN build-scripts archive, and the FFprobe license/notice/build-info files
are uploaded to the same GitHub Release as the installer. The source archives
are staged before installer publication so a missing source download blocks the
release path.

macOS bundling is currently fail-closed because no macOS binary source has yet
been approved under the same provenance/licensing standard. Do not substitute an
unreviewed binary to make a macOS packaging job pass.

See `docs/FFPROBE_DISTRIBUTION.md` for the pinned versions, redistribution
controls, source-publication requirements, future-review triggers, and patent
note.
