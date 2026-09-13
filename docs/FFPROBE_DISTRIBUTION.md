# FFprobe distribution and licensing

This document records the engineering controls used when InfoMancer redistributes FFprobe in a native desktop package. It is a compliance record and release checklist, not legal advice.

## What InfoMancer ships

InfoMancer's media-inspection feature needs `ffprobe` only. Native desktop packages do not bundle the `ffmpeg` transcoding executable.

FFprobe is invoked as a separate executable for local media inspection. InfoMancer does not link its Python or Rust application code against FFmpeg libraries.

Source/server installs may continue to use an operator-provided `INFOMANCER_FFPROBE` path or a system `ffprobe` available on `PATH`.

## Approved bundled build

The native Windows package builds its own minimal FFprobe executable directly from the pinned FFmpeg source commit. It does not redistribute a general-purpose third-party FFmpeg binary bundle.

The approved build is pinned to:

- FFmpeg source commit: `ad500d59cb6e0126add4fcb95afb4e2557c4292c`
- InfoMancer binary marker: `infomancer-ad500d59cb`
- target: Windows x86_64
- build script: `scripts/build_minimal_ffprobe.sh`
- effective FFmpeg license profile: GNU LGPL v2.1 or later

The build uses MinGW-w64 only as the compiler/toolchain. The produced `ffprobe.exe` is checked for unexpected MinGW runtime DLLs or optional third-party media-library DLLs before it can reach packaging.

FFmpeg derives its normal display version partly from repository metadata available in a checkout, so InfoMancer does not use a tag-derived version string as the security identity for a source-built binary. Instead the build verifies the full source commit before compilation, embeds `infomancer-ad500d59cb` with FFmpeg's `--extra-version` mechanism, and requires the native Windows binary to report that marker at runtime.

Linux and macOS native FFprobe bundling are not approved by this implementation. Source/server installs on those platforms continue to use a configured or system FFprobe until their native packaging path receives the same review.

## Why InfoMancer builds a minimal FFprobe

A broad prebuilt FFmpeg distribution can include many optional libraries even when the FFmpeg build itself is labelled LGPL. That creates additional license, notice, source, and provenance obligations that InfoMancer does not need for simple local media inspection.

InfoMancer therefore builds FFprobe from FFmpeg source with `--disable-autodetect` and does not enable optional external `lib*` integrations. The build also disables functionality InfoMancer does not use:

- the `ffmpeg` and `ffplay` programs;
- networking;
- encoders;
- muxers;
- filters;
- devices;
- hardware acceleration;
- iconv;
- POSIX pthreads in favor of Windows system threading;
- shared FFmpeg libraries.

Internal FFmpeg demuxers, parsers, and decoders remain available so FFprobe can identify normal local media containers and streams.

## License guardrails

FFmpeg is LGPL v2.1-or-later by default. Optional configure choices can change the effective license. In particular, `--enable-gpl` changes the FFmpeg build to GPL, `--enable-nonfree` creates a build FFmpeg says is not redistributable, and `--enable-version3` changes the applicable license profile to version 3.

InfoMancer applies all of these controls before a bundled FFprobe reaches PyInstaller:

1. The build fetches the exact FFmpeg commit and verifies the checked-out Git commit before compilation.
2. The build uses `--disable-autodetect` so installed build-runner libraries cannot silently change the binary.
3. No optional external `--enable-lib*` configure options are permitted.
4. `--enable-gpl`, `--enable-nonfree`, and `--enable-version3` are forbidden.
5. The exact-source build marker is embedded with `--extra-version=infomancer-ad500d59cb`.
6. The generated DLL dependency inventory is checked for MinGW runtime or FFmpeg/third-party media-library DLLs.
7. The Windows staging job executes the candidate binary with `ffprobe -version`.
8. The runtime output must identify FFprobe, contain the exact-source build marker, contain every required minimal-build flag, and contain no forbidden or optional external-library flag.
9. `COPYING.LGPLv2.1` is copied from the exact FFmpeg source tree and its Git blob identity is verified before packaging.
10. The exact source tree used to compile the binary is archived during the build and retained for publication with the release.

A future FFprobe update must repeat this review. Changing only the source commit or marker is not sufficient.

## FFmpeg and IJG attribution

FFmpeg's current license documentation notes that a small number of JPEG-related source files originate with the Independent JPEG Group and carry their own permissive notice. The corresponding upstream files request an IJG acknowledgement in accompanying documentation when executable code is distributed.

`FFPROBE_NOTICE.txt` therefore includes the required IJG acknowledgement. InfoMancer does not modify those IJG-derived FFmpeg files. The exact upstream files and their original copyright/license text are also present in the corresponding FFmpeg source archive published with each release.

## Files included with the application

The packaged InfoMancer core carries these files under its third-party FFprobe data, and the Windows installer also installs them as ordinary Tauri resources under `third-party/ffprobe` so users can inspect them without unpacking the PyInstaller sidecar:

- `FFPROBE_LICENSE.txt`: FFmpeg's GNU LGPL v2.1 license text from the exact source commit.
- `FFPROBE_NOTICE.txt`: identifies FFmpeg, the LGPL profile, IJG acknowledgement, purpose, source/build model, and separation between InfoMancer and FFmpeg.
- `FFPROBE_BUILDINFO.txt`: records the binary SHA-256, source-archive SHA-256, configure arguments, DLL dependency inventory, build marker, and the binary's own `ffprobe -version` output.
- `FFPROBE_BUILD_SCRIPT.sh`: the exact reproducible build recipe used for the bundled executable.
- `FFPROBE_CONFIGURE_ARGS.txt`: the configure argument list captured by the build.
- `FFPROBE_DLL_DEPENDENCIES.txt`: the imported DLL inventory generated from the candidate executable.
- `FFPROBE_SOURCE_COMMIT.txt`: the full verified FFmpeg source commit.
- `FFPROBE_BUILD_MARKER.txt`: the exact marker the candidate must report when executed.

InfoMancer must not claim ownership of FFmpeg/FFprobe or remove upstream copyright/license notices.

If InfoMancer later introduces an EULA or other restrictive license terms, those terms must preserve rights required by the LGPL for the FFmpeg component, including any applicable right to reverse engineer for debugging modifications to that component.

## Corresponding source on public releases

For every tagged Windows desktop release that contains bundled FFprobe, `.github/workflows/windows-desktop-release.yml` publishes the following to the same GitHub Release as the InfoMancer installer:

- the exact FFmpeg source archive generated from `ad500d59cb6e0126add4fcb95afb4e2557c4292c`;
- `FFPROBE_LICENSE.txt`;
- `FFPROBE_NOTICE.txt`;
- `FFPROBE_BUILDINFO.txt`;
- `FFPROBE_BUILD_SCRIPT.sh`;
- `FFPROBE_CONFIGURE_ARGS.txt`;
- `FFPROBE_DLL_DEPENDENCIES.txt`;
- `FFPROBE_SOURCE_COMMIT.txt`;
- `FFPROBE_BUILD_MARKER.txt`.

The source archive is generated from the verified source checkout before the Windows package is built. The signed Windows release job cannot proceed until the minimal FFprobe build job succeeds and its compliance bundle is available.

## Future changes that require a new review

Repeat the FFmpeg distribution review before any of the following:

- updating the pinned FFmpeg commit;
- adding native Linux, macOS, or Windows ARM64 FFprobe bundling;
- adding an optional external library to the FFmpeg configure line;
- enabling `--enable-version3`, GPL, or nonfree components;
- adding the full `ffmpeg` executable;
- using FFmpeg libraries through direct/static/dynamic linking from InfoMancer instead of spawning FFprobe as a separate process;
- adding remuxing, transcoding, encoding, or other media-processing features;
- adding an EULA or terms that restrict modification or reverse engineering of third-party components.

## Patent note

Copyright/open-source license compliance does not itself grant patent rights. Some media formats and codecs can have separate patent or royalty considerations depending on jurisdiction and use. InfoMancer currently bundles FFprobe for inspection rather than a transcoding/encoding feature, but any future expansion into media conversion or codec distribution should receive a separate patent/licensing review.

## Release gate

A Windows release containing bundled FFprobe is not ready to publish unless all of these are true:

- the source checkout matches the pinned FFmpeg commit;
- the minimal cross-build succeeds from that exact source;
- the DLL dependency inventory contains no prohibited external runtime dependency;
- the candidate executes successfully on a Windows runner;
- `ffprobe -version` reports the exact `infomancer-ad500d59cb` source marker;
- the configure line contains `--disable-autodetect`, the exact source marker, and the complete required minimal profile;
- the configure line contains none of `--enable-gpl`, `--enable-nonfree`, `--enable-version3`, or `--enable-lib*`;
- the LGPL license, IJG acknowledgement, notice, build-info, configure-args, source identity, dependency inventory, and build script are embedded with the native core and installed as readable Windows resources;
- the exact corresponding FFmpeg source archive is uploaded to the same release location as the installer;
- the packaged `infomancer-core --check-ffprobe` smoke test passes.
