from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.runtime import RuntimeLease, RuntimeLeaseError


class RuntimeLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_second_live_process_is_rejected(self):
        first = RuntimeLease(self.database, owner="first", ttl_seconds=60)
        second = RuntimeLease(self.database, owner="second", ttl_seconds=60)
        first.acquire()
        with self.assertRaises(RuntimeLeaseError):
            second.acquire()
        first.release()
        second.acquire()
        second.release()

    def test_expired_lease_can_be_reclaimed(self):
        first = RuntimeLease(self.database, owner="first", ttl_seconds=30)
        first.acquire()
        expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.database.connect() as conn:
            conn.execute("UPDATE runtime_leases SET heartbeat_at=?", (expired,))
        second = RuntimeLease(self.database, owner="second", ttl_seconds=30)
        second.acquire()
        second.release()


if __name__ == "__main__":
    unittest.main()
