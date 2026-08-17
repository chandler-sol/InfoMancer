from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class ProviderSecretError(RuntimeError):
    pass


class ProviderSecretStore:
    """Small encrypted store for provider credentials entered in the UI."""

    def __init__(self, path: Path, application_secret: str) -> None:
        self.path = path
        self.application_secret = application_secret.strip()

    def _cipher(self) -> Fernet:
        if self.application_secret:
            digest = hashlib.sha256(self.application_secret.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
        key_path = self.path.with_name("provider-secrets.key")
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                key = key_path.read_bytes().strip()
            except FileNotFoundError:
                key = Fernet.generate_key()
                try:
                    descriptor = os.open(
                        key_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except FileExistsError:
                    # Another process won the create race. Read the key it wrote
                    # rather than ever replacing an existing credential key.
                    key = key_path.read_bytes().strip()
                else:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                # Windows permission semantics differ; the application-data folder
                # remains the outer access boundary there.
                pass
            return Fernet(key)
        except (OSError, ValueError) as exc:
            raise ProviderSecretError(
                "InfoMancer could not create the encryption key used for provider credentials. "
                "Check that the application data folder is writable, then try again."
            ) from exc

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = self._cipher().decrypt(self.path.read_bytes())
            values = json.loads(payload.decode("utf-8"))
        except InvalidToken as exc:
            raise ProviderSecretError(
                "InfoMancer could not unlock the saved provider credentials. The server's "
                "INFOMANCER_SECRET may have changed. Restore the previous secret or enter the "
                "provider credentials again."
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderSecretError(
                "InfoMancer could not read its saved provider credentials. Check that the data "
                "folder is writable, then try again."
            ) from exc
        return {
            str(key): str(value)
            for key, value in values.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def update(self, values: dict[str, str]) -> None:
        current = self.load()
        current.update({key: value.strip() for key, value in values.items()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_bytes(
                self._cipher().encrypt(json.dumps(current, sort_keys=True).encode("utf-8"))
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ProviderSecretError(
                "InfoMancer verified the credentials but could not save them. Check that the "
                "application data folder is writable, then try again."
            ) from exc
