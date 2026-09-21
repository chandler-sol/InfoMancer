from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

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

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.lock")

    @contextmanager
    def _exclusive_lock(self):
        """Serialize credential read-modify-write operations across processes."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self._lock_path,
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
        except OSError as exc:
            raise ProviderSecretError(
                "InfoMancer could not lock the saved provider credentials. Check that "
                "the application data folder is writable, then try again."
            ) from exc

        try:
            try:
                os.chmod(self._lock_path, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "r+b", closefd=True) as handle:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    while True:
                        try:
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.01)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ProviderSecretError:
            raise
        except OSError as exc:
            raise ProviderSecretError(
                "InfoMancer could not lock the saved provider credentials. Check that "
                "the application data folder is writable, then try again."
            ) from exc

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

    def _load_unlocked(self) -> dict[str, str]:
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

    def load(self) -> dict[str, str]:
        # Atomic replacement guarantees readers observe either the complete old
        # ciphertext or the complete new ciphertext, never a partial write.
        return self._load_unlocked()

    def delete(self, keys: set[str] | tuple[str, ...] | list[str]) -> None:
        with self._exclusive_lock():
            current = self._load_unlocked()
            changed = False
            for key in keys:
                if key in current:
                    del current[key]
                    changed = True
            if not changed:
                return
            self._write_unlocked(
                current,
                "InfoMancer could not update the saved provider credentials. Check that "
                "the application data folder is writable, then try again.",
            )

    def _write_unlocked(self, values: dict[str, str], error_message: str) -> None:
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
                        json.dumps(values, sort_keys=True).encode("utf-8")
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = ""
        except ProviderSecretError:
            raise
        except OSError as exc:
            raise ProviderSecretError(error_message) from exc
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def update(self, values: dict[str, str]) -> None:
        normalized = {
            str(key): str(value).strip()
            for key, value in values.items()
        }
        with self._exclusive_lock():
            current = self._load_unlocked()
            current.update(normalized)
            self._write_unlocked(
                current,
                "InfoMancer verified the credentials but could not save them. Check that the "
                "application data folder is writable, then try again.",
            )
