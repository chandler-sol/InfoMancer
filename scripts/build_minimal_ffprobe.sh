#!/usr/bin/env bash
set -euo pipefail

FFMPEG_COMMIT="ad500d59cb6e0126add4fcb95afb4e2557c4292c"
FFMPEG_SHORT="ad500d59cb"
OUTPUT_DIR="${1:-build/minimal-ffprobe}"
WORK_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/infomancer-ffprobe-build"
JOBS="${JOBS:-2}"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

SOURCE_DIR="$WORK_DIR/ffmpeg"
BUILD_DIR="$WORK_DIR/build-win64"

git init -q "$SOURCE_DIR"
git -C "$SOURCE_DIR" remote add origin https://github.com/FFmpeg/FFmpeg.git
git -C "$SOURCE_DIR" fetch --depth=1 origin "$FFMPEG_COMMIT"
git -C "$SOURCE_DIR" checkout -q --detach FETCH_HEAD

ACTUAL_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
if [[ "$ACTUAL_COMMIT" != "$FFMPEG_COMMIT" ]]; then
  echo "FFmpeg source mismatch: expected $FFMPEG_COMMIT, got $ACTUAL_COMMIT" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

CONFIGURE_ARGS=(
  --target-os=mingw32
  --arch=x86_64
  --cross-prefix=x86_64-w64-mingw32-
  --disable-autodetect
  --disable-debug
  --disable-doc
  --disable-ffmpeg
  --disable-ffplay
  --disable-network
  --disable-avdevice
  --disable-devices
  --disable-filters
  --disable-encoders
  --disable-muxers
  --disable-hwaccels
  --disable-iconv
  --disable-pthreads
  --enable-w32threads
  --disable-x86asm
  --enable-static
  --disable-shared
)

"$SOURCE_DIR/configure" "${CONFIGURE_ARGS[@]}"
make -j"$JOBS" ffprobe.exe

if [[ ! -f ffprobe.exe ]]; then
  echo "Minimal FFprobe build did not produce ffprobe.exe" >&2
  exit 1
fi

# The binary must not depend on optional third-party DLLs. Windows system DLLs
# are expected; the FFmpeg libraries themselves are linked from this exact
# source tree into the standalone ffprobe.exe.
x86_64-w64-mingw32-objdump -p ffprobe.exe \
  | awk '/DLL Name:/ {print $3}' \
  | sort -fu \
  > "$OUTPUT_DIR/FFPROBE_DLL_DEPENDENCIES.txt"

cp ffprobe.exe "$OUTPUT_DIR/ffprobe.exe"
cp "$SOURCE_DIR/COPYING.LGPLv2.1" "$OUTPUT_DIR/FFPROBE_LICENSE.txt"
cp "$0" "$OUTPUT_DIR/FFPROBE_BUILD_SCRIPT.sh"
printf '%s\n' "${CONFIGURE_ARGS[@]}" > "$OUTPUT_DIR/FFPROBE_CONFIGURE_ARGS.txt"

# Publish the exact corresponding source used for the binary. This archive is
# created from the verified Git commit rather than from a moving branch/tag.
git -C "$SOURCE_DIR" archive \
  --format=tar.gz \
  --prefix="FFmpeg-$FFMPEG_COMMIT/" \
  -o "$OUTPUT_DIR/ffmpeg-source-$FFMPEG_COMMIT.tar.gz" \
  "$FFMPEG_COMMIT"

sha256sum "$OUTPUT_DIR/ffprobe.exe" > "$OUTPUT_DIR/FFPROBE_BINARY_SHA256.txt"
sha256sum "$OUTPUT_DIR/ffmpeg-source-$FFMPEG_COMMIT.tar.gz" \
  > "$OUTPUT_DIR/FFPROBE_SOURCE_SHA256.txt"

printf 'Built minimal FFprobe from FFmpeg %s (%s)\n' "$FFMPEG_SHORT" "$FFMPEG_COMMIT"
printf 'DLL dependencies:\n'
cat "$OUTPUT_DIR/FFPROBE_DLL_DEPENDENCIES.txt"
