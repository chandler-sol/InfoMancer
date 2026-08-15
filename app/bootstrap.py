from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path


class BootstrapTokenManager:
    """Provide a one-time server-side secret for first-run account creation."""

    def __init__(self, path: Path, configured_token: str = ""):
        self.path = path
        self.configured_token = configured_token.strip()
        self._announced = False

    def token(self) -> str:
        if self.configured_token:
            return self.configured_token
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = ""
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
        if not token:
            token = secrets.token_urlsafe(32)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                token = self.path.read_text(encoding="utf-8").strip()
                if not token:
                    raise RuntimeError("The first-run bootstrap token file is empty.")
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(token + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        if not self._announced:
            print(
                "InfoMancer first-run bootstrap token: " + token,
                flush=True,
            )
            print(
                "Enter this token once at /setup. It is invalidated after the first Librarian is created.",
                flush=True,
            )
            self._announced = True
        return token

    def verify(self, submitted: str) -> bool:
        expected = self.token()
        return bool(submitted and hmac.compare_digest(submitted, expected))

    def clear(self) -> None:
        if not self.configured_token:
            self.path.unlink(missing_ok=True)
