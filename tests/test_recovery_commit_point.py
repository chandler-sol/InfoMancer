import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.recovery_package import RecoveryPackageService


class RecoveryCommitPointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            )
        artwork = self.data / "collection-art"
        artwork.mkdir()
        (artwork / "collection-1.webp").write_bytes(b"original artwork")
        self.service = RecoveryPackageService(self.database.path, "0.9-test")

    def tearDown(self):
        self.temporary.cleanup()

    def _prepare_restore(self) -> Path:
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('from-package','From Package','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"package artwork")
        package = self.service.create()

        with self.database.connect() as conn:
            conn.execute("DELETE FROM users WHERE username='from-package'")
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('current-only','Current Only','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"current artwork")
        return package

    def _assert_package_state_is_authoritative(self) -> None:
        with self.database.connect() as conn:
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM users WHERE username='from-package'").fetchone()
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM users WHERE username='current-only'").fetchone()
            )
        self.assertEqual(
            (self.data / "collection-art" / "collection-1.webp").read_bytes(),
            b"package artwork",
        )

    def test_rollback_art_cleanup_failure_after_commit_cannot_reenter_rollback(self):
        package = self._prepare_restore()
        original_rmtree = shutil.rmtree
        cleanup_attempted = False

        def fail_rollback_art_cleanup(path, *args, **kwargs):
            nonlocal cleanup_attempted
            target = Path(path)
            if target.name.startswith(".collection-art-rollback-"):
                cleanup_attempted = True
                if kwargs.get("ignore_errors"):
                    return None
                raise OSError("synthetic rollback artwork cleanup failure")
            return original_rmtree(path, *args, **kwargs)

        with patch("app.recovery_package.shutil.rmtree", side_effect=fail_rollback_art_cleanup):
            result = self.service.restore(package, (self.data,))

        self.assertTrue(cleanup_attempted)
        self.assertTrue(result["authentication_reset"])
        self._assert_package_state_is_authoritative()

    def test_rollback_database_cleanup_failure_after_commit_cannot_reenter_rollback(self):
        package = self._prepare_restore()
        original_unlink = Path.unlink
        cleanup_attempted = False

        def fail_rollback_database_cleanup(path, *args, **kwargs):
            nonlocal cleanup_attempted
            if Path(path).name == "rollback-live.db":
                cleanup_attempted = True
                raise OSError("synthetic rollback database cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_rollback_database_cleanup):
            result = self.service.restore(package, (self.data,))

        # The committed restore no longer explicitly unlinks rollback-live.db.
        # Staging cleanup owns it as best-effort post-commit residue instead.
        self.assertFalse(cleanup_attempted)
        self.assertTrue(result["authentication_reset"])
        self._assert_package_state_is_authoritative()


if __name__ == "__main__":
    unittest.main()
