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
InfoMancer currently pins FFprobe 6.1.1 from the `eugeneware/ffmpeg-static`
`b6.1.1` release and stages the matching binary for Windows x64, Linux x64,
Linux arm64, macOS x64, or macOS arm64 at package-build time.

The staging script pins SHA-256 values for the downloaded compressed asset, the
decompressed executable, and the upstream-provided platform license. Packaging
fails if any of those hashes differ. The executable and its license/notice are
then added to the PyInstaller core. Before Tauri is allowed to create the native
installer or image, CI executes the finished core with `--check-ffprobe`. That
check must successfully locate and execute the bundled FFprobe binary.

At runtime, media inspection resolves FFprobe in this order:

1. `INFOMANCER_FFPROBE`, when an operator explicitly provides an override.
2. The FFprobe executable embedded in a native PyInstaller build.
3. A system `ffprobe` available on `PATH`, which remains useful for source and
   server installations.

Only FFprobe is bundled for this inspection feature. The full FFmpeg executable
should not be added unless a future feature actually requires media conversion
or remuxing.

The native package carries the license text distributed with the pinned binary
and an InfoMancer third-party notice. Those files document what is shipped, but
their presence alone is not a claim that every redistribution obligation has
been satisfied. Before publishing a production release, confirm the exact
FFmpeg build configuration and applicable LGPL/GPL obligations, including any
source or build-correspondence requirements that apply to that binary.
