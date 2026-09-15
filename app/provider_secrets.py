from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet, InvalidToken


class ProviderSecretError(RuntimeError):
    pass


class ProviderSecretStore:
    """Small encrypted store for provider credentials entered in the UI.

    Cycle 0D deliberately preserves the 0.9 provider-secret wire format. A signed
    downgrade to the frozen qualified build must still be able to read credentials
    written by this build. KDF/envelope changes therefore belong in a versioned
    migration with an explicit downgrade path rather than in behavior-preserving 0D.
    """

    def __init__(self, path: Path, application_secret: str) -> None:
        self.path = path
        self.application_secret = application_secret.strip()

    @property
    def _key_path(self) -> Path:
        return self.path.with_name("provider-secrets.key")

    def _application_cipher(self) -> Fernet:
        digest = hashlib.sha256(self.application_secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def _local_cipher(self, *, create: bool) -> Fernet | None:
        key_path = self._key_path
        try:
            if create:
                key_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                key = key_path.read_bytes().strip()
            except FileNotFoundError:
                if not create:
                    return None
                key = Fernet.generate_key()
                try:
                    descriptor = os.open(
                        key_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except FileExistsError:
                    key = key_path.read_bytes().strip()
                else:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(key)
                        handle.flush()
                        os.fsync(handle.fileno())
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return Fernet(key)
        except (OSError, ValueError) as exc:
            raise ProviderSecretError(
                "InfoMancer could not create or read the encryption key used for provider "
                "credentials. Check that the application data folder is writable, then try again."
            ) from exc

    def _cipher(self, *, create_local: bool) -> Fernet:
        if self.application_secret:
            return self._application_cipher()
        local_cipher = self._local_cipher(create=create_local)
        if local_cipher is None:
            raise ProviderSecretError(
                "InfoMancer could not unlock the saved provider credentials because the "
                "local provider encryption key is missing."
            )
        return local_cipher

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = self._cipher(create_local=False).decrypt(self.path.read_bytes())
            values = json.loads(payload.decode("utf-8"))
        except InvalidToken as exc:
            raise ProviderSecretError(
                "InfoMancer could not unlock the saved provider credentials. The server's "
                "INFOMANCER_SECRET may have changed, or the local provider encryption key "
                "may no longer match. Restore the previous key material or enter the "
                "provider credentials again."
            ) from exc
        except ProviderSecretError:
            raise
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderSecretError(
                "InfoMancer could not read its saved provider credentials. Check that the data "
                "folder is writable, then try again."
            ) from exc
        if not isinstance(values, dict):
            raise ProviderSecretError(
                "InfoMancer could not read its saved provider credentials because the "
                "decrypted credential payload is invalid."
            )
        return {
            str(key): str(value)
            for key, value in values.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def update(self, values: dict[str, str]) -> None:
        current = self.load()
        current.update({key: value.strip() for key, value in values.items()})
        temporary = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(
                    self._cipher(create_local=True).encrypt(
                        json.dumps(current, sort_keys=True).encode("utf-8")
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = ""
        except OSError as exc:
            raise ProviderSecretError(
                "InfoMancer verified the credentials but could not save them. Check that the "
                "application data folder is writable, then try again."
            ) from exc
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
