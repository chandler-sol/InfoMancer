from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.maintenance import create_database_backup, install_database_backup
from app.recovery_package import RecoveryPackageService
from app.runtime import RuntimeLease


_CONTENDER = r'''
import sys
from pathlib import Path
from app.db import Database
from app.runtime import RuntimeLease, RuntimeLeaseError

lease = RuntimeLease(Database(Path(sys.argv[1])), owner="competing-process", ttl_seconds=30)
try:
    lease.acquire()
except RuntimeLeaseError:
    raise SystemExit(23)
else:
    lease.release()
    raise SystemExit(0)
'''


class R303RuntimeReplacementOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "catalog.db")
        self.database.initialize()

    def contender_code(self) -> int:
        env = os.environ.copy()
        project_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = (
            project_root
            if not env.get("PYTHONPATH")
            else project_root + os.pathsep + env["PYTHONPATH"]
        )
        result = subprocess.run(
            [sys.executable, "-c", _CONTENDER, str(self.database.path)],
            cwd=project_root,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        return result.returncode

    def assert_replacement_has_no_persisted_owner(self) -> None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT owner FROM runtime_leases WHERE name='web-runtime'"
            ).fetchone()
        self.assertIsNone(row)

    def test_process_lock_survives_atomic_database_replacement(self):
        replacement = Database(self.root / "replacement.db")
        replacement.initialize()
        lease = RuntimeLease(self.database)
        lease.acquire()
        self.addCleanup(lease.release)

        os.replace(replacement.path, self.database.path)
        self.assert_replacement_has_no_persisted_owner()
        self.assertEqual(self.contender_code(), 23)

        lease.rebind_after_restore()
        lease.release()
        self.assertEqual(self.contender_code(), 0)

    def test_raw_restore_blocks_real_competitor_at_replace_boundary(self):
        candidate = create_database_backup(self.database.path, "r3-runtime-candidate")
        lease = RuntimeLease(self.database)
        lease.acquire()
        self.addCleanup(lease.release)
        real_replace = os.replace
        checked = False

        def intercept(source, destination):
            nonlocal checked
            result = real_replace(source, destination)
            if Path(destination) == self.database.path:
                self.assert_replacement_has_no_persisted_owner()
                self.assertEqual(self.contender_code(), 23)
                checked = True
            return result

        with patch("app.maintenance.os.replace", side_effect=intercept):
            install_database_backup(self.database.path, candidate)
        self.assertTrue(checked)
        self.assertEqual(self.contender_code(), 23)
        lease.rebind_after_restore()

    def test_portable_restore_blocks_real_competitor_at_replace_boundary(self):
        service = RecoveryPackageService(self.database.path, "r3-03-test")
        package = service.create()
        lease = RuntimeLease(self.database)
        lease.acquire()
        self.addCleanup(lease.release)
        real_replace = service._replace
        checked = False

        def intercept(source, destination):
            nonlocal checked
            result = real_replace(source, destination)
            if Path(destination) == self.database.path:
                self.assert_replacement_has_no_persisted_owner()
                self.assertEqual(self.contender_code(), 23)
                checked = True
            return result

        with patch.object(service, "_replace", side_effect=intercept):
            result = service.restore(package, (self.root,))
        self.assertTrue(checked)
        self.assertTrue(result["authentication_reset"])
        self.assertEqual(self.contender_code(), 23)
        lease.rebind_after_restore()


if __name__ == "__main__":
    unittest.main()
