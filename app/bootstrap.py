from __future__ import annotations

import os
import secrets
from pathlib import Path

from .request_security import constant_time_equal


class BootstrapTokenManager:
    """Provide the one-time setup secret for a new InfoMancer Server.

    The guided Server setup helpers prime the normal ``/setup`` route, then read
    this protected token file from the local ``data/`` bind mount so the user
    does not have to hunt through Docker logs during first-time setup. The helper
    does not bypass token validation or account creation; it only presents the
    same one-time secret the Server would otherwise print to its local logs.
    ``START-HERE.txt`` and the guided installer are the supported first-run path;
    manual Docker setup remains available for advanced deployments.
    """

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
                "Copy the token above and paste it into the first-time Server setup screen. Keep it private until setup finishes; it becomes invalid after the first Librarian account is created.",
                flush=True,
            )
            self._announced = True
        return token

    def verify(self, submitted: str) -> bool:
        expected = self.token()
        return bool(submitted and constant_time_equal(submitted, expected))

    def clear(self) -> None:
        if not self.configured_token:
            self.path.unlink(missing_ok=True)
