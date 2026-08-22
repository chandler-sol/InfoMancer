from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.db import Database
from app.recovery_package import RecoveryPackageError, RecoveryPackageService


class RecoveryPackageAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        self.service = RecoveryPackageService(self.database.path, "0.8-adversarial-test")

    def _minimal_manifest(self) -> dict:
        return {
            "format": self.service.FORMAT,
            "format_version": self.service.FORMAT_VERSION,
            "files": [],
        }

    def test_verify_rejects_noncanonical_archive_paths(self):
        package = self.data / "noncanonical.infomancer-backup"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("collection-art//cover.webp", b"x")
            archive.writestr("manifest.json", json.dumps(self._minimal_manifest()))
        with self.assertRaisesRegex(RecoveryPackageError, "unsafe archive path"):
            self.service.verify(package)

    def test_verify_rejects_casefold_collisions_before_extraction(self):
        package = self.data / "collision.infomancer-backup"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("collection-art/Cover.webp", b"first")
            archive.writestr("collection-art/cover.webp", b"second")
            archive.writestr("manifest.json", json.dumps(self._minimal_manifest()))
        with self.assertRaisesRegex(RecoveryPackageError, "collide on another supported platform"):
            self.service.verify(package)

    def test_verify_rejects_windows_reserved_member_names(self):
        package = self.data / "reserved.infomancer-backup"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("collection-art/CON.webp", b"x")
            archive.writestr("manifest.json", json.dumps(self._minimal_manifest()))
        with self.assertRaisesRegex(RecoveryPackageError, "unsafe archive path"):
            self.service.verify(package)

    def test_verify_rejects_extreme_compression_ratio_before_decompression(self):
        package = self.data / "bomb.infomancer-backup"
        with zipfile.ZipFile(
            package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
        ) as archive:
            archive.writestr("collection-art/bomb.bin", b"\x00" * (1024 * 1024))
            archive.writestr("manifest.json", json.dumps(self._minimal_manifest()))
        with self.assertRaisesRegex(RecoveryPackageError, "unsafe compression ratio"):
            self.service.verify(package)

    def test_verify_rejects_non_integer_manifest_size_as_recovery_error(self):
        original = self.service.create()
        hostile = self.data / "bad-size.infomancer-backup"
        with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(
            hostile, "w", compression=zipfile.ZIP_DEFLATED,
        ) as target:
            for item in source.infolist():
                payload = source.read(item)
                if item.filename == "manifest.json":
                    manifest = json.loads(payload)
                    manifest["files"][0]["size"] = {"not": "an integer"}
                    payload = json.dumps(manifest).encode("utf-8")
                target.writestr(item.filename, payload)
        with self.assertRaisesRegex(RecoveryPackageError, "invalid size value"):
            self.service.verify(hostile)

    def test_safe_member_bounds_depth_and_component_length(self):
        deep = "/".join(["collection-art", *("d" for _ in range(40)), "cover.webp"])
        self.assertFalse(self.service._safe_member(deep))
        self.assertFalse(
            self.service._safe_member("collection-art/" + ("a" * 256) + ".webp")
        )


if __name__ == "__main__":
    unittest.main()
