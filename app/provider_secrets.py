from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


_FORMAT_MARKER = "infomancer-provider-secrets"
_FORMAT_VERSION = 2
_SCRYPT_SALT_BYTES = 16
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class ProviderSecretError(RuntimeError):
    pass


class ProviderSecretStore:
    """Small encrypted store for provider credentials entered in the UI."""

    def __init__(self, path: Path, application_secret: str) -> None:
        self.path = path
        self.application_secret = application_secret.strip()

    @property
    def _key_path(self) -> Path:
        return self.path.with_name("provider-secrets.key")

    @staticmethod
    def _fernet_from_key_material(material: bytes) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(material))

    def _legacy_application_cipher(self) -> Fernet:
        digest = hashlib.sha256(self.application_secret.encode("utf-8")).digest()
        return self._fernet_from_key_material(digest)

    def _application_cipher(self, salt: bytes) -> Fernet:
        if not self.application_secret:
            raise ProviderSecretError(
                "InfoMancer could not unlock the saved provider credentials because "
                "INFOMANCER_SECRET is not configured. Restore the secret used to save "
                "these credentials or enter them again."
            )
        material = Scrypt(
            salt=salt,
            length=32,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
        ).derive(self.application_secret.encode("utf-8"))
        return self._fernet_from_key_material(material)

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
                "InfoMancer could not create or read the encryption key used for provider "
                "credentials. Check that the application data folder is writable, then try again."
            ) from exc

    def _decode_envelope(self, raw: bytes) -> dict | None:
        try:
            candidate = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(candidate, dict) or candidate.get("format") != _FORMAT_MARKER:
            return None
        if candidate.get("version") != _FORMAT_VERSION:
            raise ProviderSecretError(
                "InfoMancer cannot read this provider credential format. Update InfoMancer "
                "or restore credentials written by a supported version."
            )
        return candidate

    def _decrypt_envelope(self, envelope: dict) -> bytes:
        token = envelope.get("token")
        key_source = envelope.get("key_source")
        if not isinstance(token, str) or not token:
            raise ProviderSecretError(
                "InfoMancer could not read its saved provider credentials because the "
                "encrypted credential envelope is incomplete."
            )
        try:
            token_bytes = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ProviderSecretError(
                "InfoMancer could not read its saved provider credentials because the "
                "encrypted credential token is invalid."
            ) from exc

        if key_source == "application_secret":
            if envelope.get("kdf") != "scrypt":
                raise ProviderSecretError(
                    "InfoMancer cannot read this provider credential key-derivation format."
                )
            salt_text = envelope.get("salt")
            if not isinstance(salt_text, str):
                raise ProviderSecretError(
                    "InfoMancer could not read its saved provider credentials because the "
                    "credential salt is missing."
                )
            try:
                salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
            except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
                raise ProviderSecretError(
                    "InfoMancer could not read its saved provider credentials because the "
                    "credential salt is invalid."
                ) from exc
            if len(salt) != _SCRYPT_SALT_BYTES:
                raise ProviderSecretError(
                    "InfoMancer could not read its saved provider credentials because the "
                    "credential salt is invalid."
                )
            cipher = self._application_cipher(salt)
        elif key_source == "local_key":
            cipher = self._local_cipher(create=False)
            if cipher is None:
                raise ProviderSecretError(
                    "InfoMancer could not unlock the saved provider credentials because the "
                    "local provider encryption key is missing."
                )
        else:
            raise ProviderSecretError(
                "InfoMancer cannot read this provider credential key source."
            )
        return cipher.decrypt(token_bytes)

    def _decrypt_legacy(self, raw: bytes) -> bytes:
        if self.application_secret:
            try:
                return self._legacy_application_cipher().decrypt(raw)
            except InvalidToken:
                # A server may add INFOMANCER_SECRET after credentials were already
                # protected by the generated local key. Preserve access to that file
                # and migrate it to the configured secret on the next successful write.
                local_cipher = self._local_cipher(create=False)
                if local_cipher is not None:
                    return local_cipher.decrypt(raw)
                raise
        local_cipher = self._local_cipher(create=False)
        if local_cipher is None:
            raise ProviderSecretError(
                "InfoMancer could not unlock the saved provider credentials because the "
                "local provider encryption key is missing."
            )
        return local_cipher.decrypt(raw)

    def _encrypt(self, payload: bytes) -> bytes:
        if self.application_secret:
            salt = os.urandom(_SCRYPT_SALT_BYTES)
            token = self._application_cipher(salt).encrypt(payload).decode("ascii")
            envelope = {
                "format": _FORMAT_MARKER,
                "version": _FORMAT_VERSION,
                "key_source": "application_secret",
                "kdf": "scrypt",
                "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
                "token": token,
            }
        else:
            local_cipher = self._local_cipher(create=True)
            assert local_cipher is not None
            envelope = {
                "format": _FORMAT_MARKER,
                "version": _FORMAT_VERSION,
                "key_source": "local_key",
                "token": local_cipher.encrypt(payload).decode("ascii"),
            }
        return json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_bytes()
            envelope = self._decode_envelope(raw)
            payload = (
                self._decrypt_envelope(envelope)
                if envelope is not None
                else self._decrypt_legacy(raw)
            )
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
                    self._encrypt(json.dumps(current, sort_keys=True).encode("utf-8"))
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
