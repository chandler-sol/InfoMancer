# FFprobe distribution and licensing

This document records the engineering controls used when InfoMancer redistributes FFprobe in a native desktop package. It is a compliance record and release checklist, not legal advice.

## What InfoMancer ships

InfoMancer's media-inspection feature needs `ffprobe` only. Native desktop packages do not bundle the `ffmpeg` transcoding executable.

FFprobe is invoked as a separate executable for local media inspection. InfoMancer does not link its Python or Rust application code against FFmpeg libraries.

Source/server installs may continue to use an operator-provided `INFOMANCER_FFPROBE` path or a system `ffprobe` available on `PATH`.

## Approved bundled build

The native staging script currently approves these BtbN/FFmpeg-Builds targets:

- Windows x86_64
- Windows ARM64
- Linux x86_64
- Linux ARM64

The approved build is pinned to:

- FFmpeg version: `n9.0.1-29-gad500d59cb`
- FFmpeg source commit: `ad500d59cb6e0126add4fcb95afb4e2557c4292c`
- BtbN release: `autobuild-2026-09-11-13-20`
- BtbN build-scripts commit: `cc8f0958be119db774cdaf6c50065651a4901e72`
- Variant: `lgpl`, static executable
- Effective FFmpeg license profile: GNU LGPL v3

Each platform archive is pinned by SHA-256 in `scripts/stage_ffprobe.py`. The staging script does not use BtbN's moving `latest` aliases.

The exact BtbN `defaults-lgpl.sh` at the pinned build-scripts commit explicitly enables `--enable-version3` and selects `COPYING.LGPLv3`. That means the effective license for this approved binary profile is LGPL v3, not FFmpeg's default LGPL v2.1-or-later profile.

macOS is intentionally not in the approved binary table. Native macOS packaging must fail closed until an LGPL build and corresponding-source path are reviewed and pinned. Do not restore the previous unreviewed `eugeneware/ffmpeg-static` macOS binaries as a workaround.

## License guardrails

FFmpeg is LGPL v2.1-or-later by default, but optional configure choices can change the effective license. A build configured with `--enable-gpl` becomes GPL. A build configured with `--enable-nonfree` is not redistributable under FFmpeg's normal terms. The approved BtbN LGPL profile also uses `--enable-version3`, which moves this build to LGPL v3.

InfoMancer therefore applies all of these checks before a bundled FFprobe reaches PyInstaller:

1. The selected archive filename must be one of the explicitly pinned BtbN `-lgpl-` variants.
2. The downloaded archive must match its pinned SHA-256.
3. Only the `ffprobe` executable is extracted from that verified archive.
4. The staged executable is run with `ffprobe -version` on the native build runner.
5. Packaging aborts if the reported build configuration contains `--enable-gpl` or `--enable-nonfree`.
6. Packaging aborts unless the reported build configuration contains the expected `--enable-version3` flag.
7. Packaging aborts if the binary does not report the exact pinned FFmpeg version.
8. The LGPL v3 text is fetched from the exact FFmpeg source commit and verified by its Git blob SHA-1 before being staged.

A future FFprobe update must repeat this review. Changing only the version string or download URL is not sufficient.

## Files included with the application

The packaged InfoMancer core carries these files under its third-party FFprobe data:

- `FFPROBE_LICENSE.txt`: GNU LGPL v3 license text from the exact FFmpeg source commit.
- `FFPROBE_NOTICE.txt`: identifies FFmpeg, the effective license profile, the binary builder, the exact source/build provenance, and the separation between InfoMancer and FFmpeg.
- `FFPROBE_BUILDINFO.txt`: records the archive checksum plus the staged binary's own `ffprobe -version` output, including its configure line.

InfoMancer must not claim ownership of FFmpeg/FFprobe or remove upstream copyright/license notices.

If InfoMancer later introduces an EULA or other restrictive license terms, those terms must preserve rights required by the LGPL for the FFmpeg component, including any applicable right to reverse engineer for debugging modifications to that component.

## Corresponding source on public releases

For every tagged Windows desktop release that contains the bundled FFprobe binary, `.github/workflows/windows-desktop-release.yml` stages and uploads the following to the same GitHub Release as the InfoMancer installer:

- the exact FFmpeg source tree for `ad500d59cb6e0126add4fcb95afb4e2557c4292c`;
- the exact BtbN build-scripts tree for `cc8f0958be119db774cdaf6c50065651a4901e72`;
- `FFPROBE_LICENSE.txt`;
- `FFPROBE_NOTICE.txt`;
- `FFPROBE_BUILDINFO.txt`.

The release job stages the source archives before publishing the installer so a missing source download blocks the release path instead of silently publishing a binary-only release.

BtbN's LGPL build can contain additional LGPL-compatible third-party dependencies. Their licenses and any dependency-specific notice/source duties belong in InfoMancer's broader third-party dependency review and SBOM. That broader release gate remains separate from this FFmpeg-specific control. If a future approved builder adds a dependency with a corresponding-source obligation, the source publication step must be expanded before that build can ship.

## Future changes that require a new review

Repeat the FFmpeg distribution review before any of the following:

- updating the pinned FFmpeg or BtbN build;
- adding macOS native FFprobe bundling;
- switching builders;
- changing the `--enable-version3` license profile;
- enabling GPL or nonfree components;
- adding the full `ffmpeg` executable;
- using FFmpeg libraries through direct/static/dynamic linking instead of spawning FFprobe as a separate process;
- adding remuxing, transcoding, encoding, or other media-processing features;
- adding an EULA or terms that restrict modification or reverse engineering of third-party components.

## Patent note

Copyright/open-source license compliance does not itself grant patent rights. Some media formats and codecs can have separate patent or royalty considerations depending on jurisdiction and use. InfoMancer currently bundles FFprobe for inspection rather than a transcoding/encoding feature, but any future expansion into media conversion or codec distribution should receive a separate patent/licensing review.

## Release gate

A release containing bundled FFprobe is not ready to publish unless all of these are true:

- the archive SHA-256 check passes;
- `ffprobe -version` reports the pinned version;
- the configure line contains neither `--enable-gpl` nor `--enable-nonfree`;
- the configure line contains the expected `--enable-version3` profile;
- the LGPL v3 license, notice, and build-info files are embedded in the package;
- the exact FFmpeg source and BtbN build scripts are uploaded to the same release location as the installer;
- the packaged `infomancer-core --check-ffprobe` smoke test passes.
