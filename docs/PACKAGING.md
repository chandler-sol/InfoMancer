# Cross-platform packaging plan

## Product names

Keep the names simple and consistent:

- **InfoMancer** is the product and the name shown in the interface.
- **InfoMancer Server** is the software package that stores the catalog, scans media, runs background work, and serves the web interface.
- **Local install** means InfoMancer Server is running on the same computer as the person using it. It is not a different package.
- **Dedicated server install** means the same InfoMancer Server package is running on another computer that stays on.
- A future dedicated desktop client, if one is built, should use a separate name such as **InfoMancer Desktop** so it is not confused with the server package.

Current release archives should use:

```text
InfoMancer-Server-VERSION.zip
```

The application itself should continue to display **InfoMancer**, not "InfoMancer Server", throughout normal library use. The Server label is primarily for downloads, installers, service names where useful, and documentation explaining what people need to install.

## Recommendation

Ship InfoMancer Server in two stages:

1. **First public beta:** an InfoMancer Server release ZIP with Docker Compose and, later, a prebuilt multi-architecture container image.
2. **Later native server installers:** signed Windows, macOS, and Linux packages that install InfoMancer Server as a local background service and open its web interface in the default browser.

Docker should remain supported after native installers exist. It is a strong fit for NAS devices, headless systems, always-on home servers, and people who want one InfoMancer installation available from several devices.

## Why the Server name helps

Without the Server label, a release ZIP called only `InfoMancer-VERSION.zip` can sound like a normal desktop application that should be installed separately on every computer.

That is not how the current product works.

InfoMancer Server is installed once. Browsers connect to that installation. The server may happen to run on the same computer as the browser, but it is still the component doing the cataloging, scanning, metadata work, and file operations.

This naming also leaves room for a future desktop or mobile client without making the existing package ambiguous.

## Native installers

A polished native InfoMancer Server package must decide and test:

- where the database, encryption key, configuration, and logs live;
- how the server starts, stops, restarts, and updates;
- how it opens the browser after installation;
- how media folders and network shares are granted;
- how FFprobe is bundled and licensed;
- how uninstall preserves or removes application data;
- how crashes are reported when no terminal window is visible;
- how the application and installer are signed.

The application logic is portable, but those operating-system pieces must be added before native packages are appropriate for non-technical users.

## Windows

Preferred deliverable: a signed **MSI** named clearly as InfoMancer Server.

Candidate build route:

- Package Python and InfoMancer with Briefcase or PyInstaller.
- Use Briefcase/WiX or a dedicated WiX project to produce the MSI.
- Install per-user initially where practical to avoid unnecessary administrator requirements.
- Run InfoMancer Server in the background rather than leaving a console window open.
- Open the local InfoMancer web interface after setup.
- Sign both the launcher/service and installer with a trusted code-signing certificate.

Test Windows 11 first. Include local NTFS folders, removable drives, UNC shares, and unavailable network shares in the test matrix.

## macOS

Preferred deliverable: a signed and notarized package that installs InfoMancer Server and provides a simple launcher/status surface.

The build must run on macOS. Produce and test Apple silicon and Intel artifacts as needed, sign with Developer ID, enable the hardened runtime where appropriate, submit for Apple notarization, and test access to external volumes and network shares.

macOS privacy controls can block storage that works from Terminal, so clean-machine testing is required.

## Linux

Recommended order:

1. Docker Compose for supported distributions.
2. `.deb` packages for current Ubuntu and Debian releases, covering Linux Mint through its Ubuntu base where practical.
3. `.rpm` packages for Fedora and RHEL-compatible systems if demand justifies them.
4. Other formats only when there is a clear user need and a maintainable update path.

Native Linux packages should integrate with systemd for an always-on InfoMancer Server installation. Distribution packages need separate testing because system libraries differ between releases.

## Future clients

A future desktop or mobile client should connect to InfoMancer Server rather than silently installing another independent server and catalog.

If a future desktop package is designed for single-computer use and bundles its own server internally, the installer must still explain that clearly so people understand where the authoritative catalog lives.

Do not use "local" as a package name. Local is a deployment choice, not a second product.

## Build infrastructure

Native artifacts cannot be treated as one cross-compiled binary. Use a release matrix with a real Windows runner, a real macOS runner, and Linux runners for each native target.

Store signing credentials only in protected release environments and never expose them to pull-request jobs.

Before enabling native installer builds, add:

- an application version source shared by the UI and packages;
- deterministic database migration tests across released versions;
- an update manifest and rollback policy;
- installer smoke tests on clean virtual machines;
- Software Bill of Materials and dependency/license reporting;
- checksums and signed release notes.

## Primary packaging references

- [PyInstaller platform support](https://pyinstaller.org/en/stable/) for platform-specific builds
- [Briefcase Windows packaging](https://briefcase.beeware.org/en/stable/reference/platforms/windows/)
- [Briefcase macOS packaging](https://briefcase.beeware.org/en/latest/reference/platforms/macOS/)
- [Briefcase Linux system packages](https://briefcase.beeware.org/en/stable/reference/platforms/linux/system/)
- [Apple Developer ID](https://developer.apple.com/support/developer-id/) and [macOS distribution](https://developer.apple.com/macos/distribution/)
- [Microsoft code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
