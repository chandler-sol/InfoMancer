import inspect
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

    def test_irreversible_cleanup_after_commit_cannot_reenter_rollback(self):
        package = self._prepare_restore()
        original_rmtree = shutil.rmtree
        cleanup_attempted = False

        def delete_then_fail_without_best_effort(path, *args, **kwargs):
            nonlocal cleanup_attempted
            target = Path(path)
            if target.name.startswith(".collection-art-rollback-"):
                cleanup_attempted = True
                # Reproduce the dangerous boundary exactly: rollback artwork is
                # already gone before cleanup reports a failure. The repaired
                # path marks this as best-effort cleanup, so no exception is
                # allowed to re-enter transactional rollback after commit.
                original_rmtree(path, *args, **kwargs)
                if not kwargs.get("ignore_errors"):
                    raise OSError("synthetic failure after rollback artwork deletion")
                return None
            return original_rmtree(path, *args, **kwargs)

        with patch("app.recovery_package.shutil.rmtree", side_effect=delete_then_fail_without_best_effort):
            result = self.service.restore(package, (self.data,))

        self.assertTrue(cleanup_attempted)
        self.assertTrue(result["authentication_reset"])
        self._assert_package_state_is_authoritative()

    def test_commit_point_precedes_return_and_rollback_database_has_no_transactional_unlink(self):
        source = inspect.getsource(RecoveryPackageService.restore)
        self.assertNotIn("rollback_database.unlink", source)
        self.assertLess(source.index("restore_committed = True"), source.index("return result"))
        self.assertIn("shutil.rmtree(rollback_art, ignore_errors=True)", source)
        self.assertIn("shutil.rmtree(staging_root, ignore_errors=True)", source)


if __name__ == "__main__":
    unittest.main()
