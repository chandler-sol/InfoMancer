# InfoMancer third-party notices

InfoMancer uses and optionally integrates third-party software. This notice is
provided with release material so optional components remain attributable even
when they are installed separately from the base application.

## Episode Identity CPU OCR

### RapidOCR 3.9.2

- Project: RapidOCR
- Source: https://github.com/RapidAI/RapidOCR
- License: Apache License 2.0
- InfoMancer use: optional scene-text OCR for Episode Identity Normal
- Installation: optional through `requirements-ocr.txt`; not part of the base
  Python requirements and not bundled into the current native desktop core

RapidOCR documents that its source/engineering components use Apache-2.0 and
that its bundled/hosted OCR models are derived from PaddleOCR models and are
redistributed under Apache-2.0 terms. Model-specific attribution remains owned
by the upstream rights holders and is documented by RapidOCR.

### ONNX Runtime 1.30.0

- Project: ONNX Runtime
- Source: https://github.com/microsoft/onnxruntime
- License: MIT
- InfoMancer use: CPU inference backend for the optional RapidOCR adapter
- Installation: optional through `requirements-ocr.txt`

The installed ONNX Runtime Python distribution carries its own license and
third-party notice metadata. InfoMancer does not enable CUDA, DirectML, CANN, or
CoreML execution providers in the 0.9 CPU OCR adapter.

## Episode Identity local speech

### whisper.cpp 1.9.4

- Project: whisper.cpp
- Source: https://github.com/ggml-org/whisper.cpp
- License: MIT
- InfoMancer use: optional CPU-only local transcription for bounded Episode
  Identity speech windows
- Managed build: stable 1.9.4, using matching upstream build tag `b5130`
  from commit `927cfce34f31707e17f2bff35c349632fb9e2c3a`

InfoMancer does not include whisper.cpp in its base Python dependencies. On
supported Windows and Linux platforms, users may explicitly install a pinned
private runtime from upstream release artifacts. InfoMancer verifies the
archive and the installed runtime inventory before use. System or explicitly
configured `whisper-cli` installations remain optional alternatives.

### Whisper base q5_1 models

- Model family: OpenAI Whisper base / base.en converted for whisper.cpp
- Distribution: https://huggingface.co/ggerganov/whisper.cpp
- License: MIT
- Pinned model revision:
  `f281eb45af861ab5e5297d23694b7d46e090c02c`
- InfoMancer use: separately managed local speech-recognition models
- Default 0.9 model: `base-q5_1` multilingual
- Optional model: `base.en-q5_1` English-only

The model files are not bundled with the base application. Each model is
downloaded only on explicit user request and is verified by exact byte size and
SHA-256. Managed model directories carry the applicable OpenAI Whisper and
whisper.cpp MIT license text and an InfoMancer provenance notice.

## FFmpeg

InfoMancer's native packaging and managed-component path use the pinned
`eugeneware/ffmpeg-static` FFmpeg 6.1.1 family. Each staged or managed copy is
verified against platform-specific SHA-256 values and is accompanied by the
license file distributed with that platform asset plus an InfoMancer notice.

FFmpeg licensing can vary with build configuration. Before a production release,
the exact redistributed binary configuration and corresponding LGPL/GPL source
or build-correspondence obligations must be confirmed. Docker/source installs
may instead use the FFmpeg supplied by their operating system.

## Pillow

InfoMancer uses Pillow for bounded JPEG validation and Jellyfin Trickplay image
handling. Pillow is distributed under the HPND License, a permissive license
historically derived from the PIL Software License.

## Scope

This document is attribution and packaging metadata, not a replacement for the
license text carried by an installed package or bundled binary. Release tooling
must preserve applicable upstream license files and package metadata.
