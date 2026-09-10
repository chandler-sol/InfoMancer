"""InfoMancer media catalog."""

from __future__ import annotations

import os
from pathlib import Path

import certifi


# Public support floor used by the Server setup helpers and documentation. Keep
# these values aligned if the Docker requirement changes in a future release.
SERVER_DOCKER_ENGINE_MIN = "24.0.0"
SERVER_DOCKER_COMPOSE_MIN = "2.20.0"


# Packaged Python runtimes, especially PyInstaller builds on macOS, cannot always
# discover a usable system CA bundle. Keep an explicitly configured valid bundle,
# but repair missing or invalid certificate paths with certifi's bundled store.
_configured_ca = os.environ.get("SSL_CERT_FILE", "").strip()
if not _configured_ca or not Path(_configured_ca).is_file():
    os.environ["SSL_CERT_FILE"] = certifi.where()
