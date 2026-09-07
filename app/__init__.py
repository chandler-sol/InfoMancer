"""InfoMancer media catalog."""

from __future__ import annotations

import os
from pathlib import Path

import certifi


# Packaged Python runtimes, especially PyInstaller builds on macOS, cannot always
# discover a usable system CA bundle. Keep an explicitly configured valid bundle,
# but repair missing or invalid certificate paths with certifi's bundled store.
_configured_ca = os.environ.get("SSL_CERT_FILE", "").strip()
if not _configured_ca or not Path(_configured_ca).is_file():
    os.environ["SSL_CERT_FILE"] = certifi.where()
