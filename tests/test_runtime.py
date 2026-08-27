from __future__ import annotations

import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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

    def test_old_process_detects_ownership_loss_after_stale_reclaim(self):
        first = RuntimeLease(self.database, owner="first", ttl_seconds=30)
        second = RuntimeLease(self.database, owner="second", ttl_seconds=30)
        first.acquire()
        expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.database.connect() as conn:
            conn.execute("UPDATE runtime_leases SET heartbeat_at=?", (expired,))
        second.acquire()
        with self.assertRaises(RuntimeLeaseError):
            first.heartbeat()
        second.release()

    def test_dead_desktop_worker_can_be_reclaimed_without_waiting_for_ttl(self):
        host = socket.gethostname().replace(":", "_")
        dead_owner = f"desktop:{host}:2147483647:stale-worker"
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO runtime_leases(name,owner,heartbeat_at) VALUES (?,?,?)",
                ("web-runtime", dead_owner, datetime.now(timezone.utc).isoformat()),
            )

        with patch("app.runtime._process_is_alive", return_value=False):
            replacement = RuntimeLease(self.database, owner="replacement", ttl_seconds=90)
            replacement.acquire()
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT owner FROM runtime_leases WHERE name='web-runtime'"
            ).fetchone()
        self.assertEqual(row["owner"], "replacement")
        replacement.release()

    def test_live_desktop_worker_keeps_fresh_lease(self):
        host = socket.gethostname().replace(":", "_")
        live_owner = f"desktop:{host}:{os.getpid()}:live-worker"
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO runtime_leases(name,owner,heartbeat_at) VALUES (?,?,?)",
                ("web-runtime", live_owner, datetime.now(timezone.utc).isoformat()),
            )

        with patch("app.runtime._process_is_alive", return_value=True):
            second = RuntimeLease(self.database, owner="second", ttl_seconds=90)
            with self.assertRaises(RuntimeLeaseError):
                second.acquire()


if __name__ == "__main__":
    unittest.main()
